"""Manual relay regression tests; temporary journals and synthetic loopback only."""
import asyncio
from contextlib import asynccontextmanager
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from mudgpt.decision_journal import DecisionConflict, DecisionJournal
from test_broker import BrokerHarness, WORKSPACE

spec = importlib.util.spec_from_file_location("relay_once", WORKSPACE / "scripts/relay_once.py")
relay = importlib.util.module_from_spec(spec)
spec.loader.exec_module(relay)


def authored(**changes):
    return {"decision_id": "fixture-decision-1", "actor": "fixture-actor",
            "source": "synthetic-saved-conversation", "control": "/synthetic/control.json",
            "command": "look", "expected_broker_id": "fixture-broker",
            "expected_generation": 1, "expected_cursor": 3,
            "expires_at": time.time() + 60, **changes}


def result(value):
    return SimpleNamespace(is_error=False, structured_content=value, content=[])


class FakeClient:
    def __init__(self, decision):
        self.decision, self.calls = decision, []
        self.fail_claim = self.fail_send = self.fail_release = False
        self.capable = True

    async def initialize(self):
        self.calls.append(("initialize", {}))

    async def call_tool(self, name, args):
        self.calls.append((name, args.copy()))
        if name == "mud_status":
            return result({key: self.decision[f"expected_{key}"]
                           for key in ("broker_id", "generation", "cursor")} |
                          {"capabilities": ["guarded_send_v1"] if self.capable else []})
        if name == "mud_claim":
            if self.fail_claim:
                raise RuntimeError("synthetic unconfirmed claim")
            return result({"lease_epoch": 2, "owner": self.decision["actor"],
                           "lease": "DO_NOT_STORE_SYNTHETIC_LEASE"})
        if name == "mud_send":
            if self.fail_send:
                raise RuntimeError("synthetic transport lost after dispatch")
            return result({"broker_id": self.decision["expected_broker_id"], "generation": 1,
                           "cursor": 6, "command_id": "1:1", "delivery": "transport_accepted",
                           "command_complete": False, "reason": "quiet", "truncated": False,
                           "events": [{"seq": 6, "kind": "text", "text": "Synthetic room",
                                       "style_spans": [{"start": 0, "end": 9, "fg": "green"}]}]})
        if name == "mud_release":
            if self.fail_release:
                raise RuntimeError("synthetic stale lease after takeover")
            return result({"owner": None})
        raise AssertionError(name)


class RelayUnitTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="mudgpt-relay-unit-")
        self.addCleanup(self.temp.cleanup)
        self.journal = DecisionJournal(Path(self.temp.name) / "decisions.sqlite3")
        self.addCleanup(self.journal.close)
        self.decision = authored()
        self.client = FakeClient(self.decision)
        self.cleanup_error = False

    @asynccontextmanager
    async def factory(self, control):
        self.assertEqual(control, self.decision["control"])
        yield self.client
        if self.cleanup_error:
            raise RuntimeError("synthetic MCP context cleanup failure")

    async def carry(self):
        return await relay.relay(self.decision, self.journal, self.factory)

    def names(self):
        return [name for name, _ in self.client.calls]

    async def test_original_tuple_and_expiry_forwarded_and_success_deduplicated_after_expiry(self):
        first = await self.carry()
        sent = next(args for name, args in self.client.calls if name == "mud_send")
        for key in ("expected_broker_id", "expected_generation", "expected_cursor", "expires_at"):
            self.assertEqual(sent[key], self.decision[key])
        self.assertEqual(first["observation"]["style_spans"][0]["fg"], "green")
        self.assertNotIn("DO_NOT_STORE_SYNTHETIC_LEASE", json.dumps(first))
        saved = self.journal.connection.execute("SELECT receipt FROM relay_decisions").fetchone()[0]
        self.assertNotIn("DO_NOT_STORE_SYNTHETIC_LEASE", saved)
        before = list(self.client.calls)
        with patch.object(relay.time, "time", return_value=self.decision["expires_at"] + 100):
            repeated = await self.carry()
        self.assertTrue(repeated["duplicate"])
        self.assertFalse(repeated["new_send"])
        self.assertEqual(repeated["observation"], first["observation"])
        self.assertEqual(self.client.calls, before)
        self.assertFalse(repeated["command_complete"])

    async def test_changed_authored_fields_under_same_id_conflict_without_mcp(self):
        await self.carry()
        before = list(self.client.calls)
        changes = {"actor": "other", "source": "other-source", "control": "/other/control",
                   "command": "north", "expected_broker_id": "other-broker",
                   "expected_generation": 2, "expected_cursor": 4,
                   "expires_at": self.decision["expires_at"] + 1}
        for key, value in changes.items():
            with self.assertRaises(DecisionConflict):
                await relay.relay({**self.decision, key: value}, self.journal, self.factory)
        self.assertEqual(self.client.calls, before)

    async def test_unconfirmed_claim_never_releases_or_sends(self):
        self.client.fail_claim = True
        receipt = await self.carry()
        self.assertIn("claim_error", receipt)
        self.assertEqual(receipt["delivery"], "not_sent")
        self.assertNotIn("mud_send", self.names())
        self.assertNotIn("mud_release", self.names())

    async def test_send_failure_is_durably_unknown_and_never_retried_after_reopen(self):
        self.client.fail_send = True
        first = await self.carry()
        self.assertEqual(first["delivery"], "unknown")
        self.assertIn("send_error", first)
        self.assertTrue(first["released"])
        with DecisionJournal(self.journal.path) as reopened:
            repeated = await relay.relay(self.decision, reopened, self.factory)
        self.assertEqual(repeated["delivery"], "unknown")
        self.assertEqual(self.names().count("mud_send"), 1)
        self.assertTrue(repeated["duplicate"])

    async def test_release_failure_preserves_authoritative_send_and_repeat_is_read_only(self):
        self.client.fail_release = True
        first = await self.carry()
        self.assertEqual(first["delivery"], "transport_accepted")
        self.assertEqual(first["send_receipt"]["command_id"], "1:1")
        self.assertIn("stale lease", first["release_error"])
        before = list(self.client.calls)
        repeated = await self.carry()
        self.assertEqual(repeated["observation"], first["observation"])
        self.assertEqual(self.client.calls, before)

    async def test_context_cleanup_failure_does_not_erase_already_saved_send(self):
        self.cleanup_error = True
        first = await self.carry()
        self.assertIn("cleanup", first["courier_error"])
        self.assertEqual(first["delivery"], "transport_accepted")
        repeated = await self.carry()
        self.assertEqual(repeated["send_receipt"], first["send_receipt"])
        self.assertEqual(self.names().count("mud_send"), 1)

    async def test_admission_failure_prevents_every_mcp_operation(self):
        with patch.object(self.journal, "admit", side_effect=OSError("synthetic full disk")):
            with self.assertRaises(OSError):
                await self.carry()
        self.assertEqual(self.client.calls, [])

    async def test_pending_checkpoint_failure_prevents_send_and_releases_confirmed_claim(self):
        original = self.journal.save

        def save(decision_id, receipt):
            if receipt["phase"] == "send_pending":
                raise OSError("synthetic journal commit failure")
            original(decision_id, receipt)

        with patch.object(self.journal, "save", side_effect=save):
            receipt = await self.carry()
        self.assertNotIn("mud_send", self.names())
        self.assertIn("mud_release", self.names())
        self.assertIn("courier_error", receipt)
        repeated = await self.carry()
        self.assertTrue(repeated["duplicate"])
        self.assertNotIn("mud_send", self.names())

    async def test_expired_decision_is_recorded_without_contacting_mcp(self):
        self.decision["expires_at"] = time.time() - 1
        receipt = await self.carry()
        self.assertIn("expired", receipt["rejection"])
        self.assertEqual(self.client.calls, [])
        self.assertTrue((await self.carry())["duplicate"])

    async def test_expiry_after_claim_releases_without_sending(self):
        self.decision["expires_at"] = 101.0
        with patch.object(relay.time, "time", side_effect=[100, 100, 102]):
            receipt = await self.carry()
        self.assertIn("after claim", receipt["rejection"])
        self.assertNotIn("mud_send", self.names())
        self.assertIn("mud_release", self.names())

    async def test_nonfinite_expiry_rejected_before_admission(self):
        for expiry in (float("nan"), float("inf"), True):
            self.decision["expires_at"] = expiry
            with self.assertRaises(ValueError):
                await self.carry()
        self.assertEqual(self.client.calls, [])
        self.assertEqual(self.journal.connection.execute("SELECT count(*) FROM relay_decisions").fetchone()[0], 0)

    async def test_old_broker_without_capability_never_claims_or_sends(self):
        self.client.capable = False
        receipt = await self.carry()
        self.assertIn("guarded_send_v1", receipt["rejection"])
        self.assertEqual(self.names(), ["initialize", "mud_status"])


class JournalProcessTests(unittest.TestCase):
    def test_concurrent_admission_and_crash_checkpoint_survive_process_exit(self):
        env = {**os.environ, "PYTHONPATH": str(WORKSPACE / "src")}
        script = (
            "import os,sys; from mudgpt.decision_journal import DecisionJournal; "
            "print('armed',flush=True); input(); "
            "j=DecisionJournal(sys.argv[1]); "
            "accepted,receipt=j.admit('stable',{'command':'look'},"
            "{'phase':'send_pending','delivery':'unknown'}); "
            "print('admitted' if accepted else 'duplicate',flush=True); "
            "os._exit(17 if accepted else 0)"
        )
        children = []
        with tempfile.TemporaryDirectory(prefix="mudgpt-decision-process-") as directory:
            path = Path(directory) / "decisions.sqlite3"
            try:
                for _ in range(2):
                    child = subprocess.Popen([sys.executable, "-c", script, str(path)],
                        cwd=WORKSPACE, env=env, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                        stderr=subprocess.PIPE, text=True)
                    children.append(child)
                    self.assertEqual(child.stdout.readline().strip(), "armed")
                for child in children:
                    child.stdin.write("go\n")
                    child.stdin.flush()
                outputs = [child.communicate(timeout=12) for child in children]
                self.assertEqual(sorted(child.returncode for child in children), [0, 17], outputs)
                self.assertEqual(sorted(out.strip() for out, _ in outputs), ["admitted", "duplicate"])
                with DecisionJournal(path) as journal:
                    accepted, saved = journal.admit("stable", {"command": "look"}, {})
                self.assertFalse(accepted)
                self.assertEqual(saved, {"phase": "send_pending", "delivery": "unknown"})
            finally:
                for child in children:
                    if child.poll() is None:
                        child.kill()
                        child.communicate(timeout=5)


class RelayMcpIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.h = BrokerHarness()
        self.addCleanup(self.h.close)
        self.h.connect()
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            observed = self.h.call(op="observe")
            if any(e["kind"] == "text" for e in observed["events"]):
                break
            time.sleep(.01)
        self.h.call(op="release", owner="human")
        self.decision = authored(control=str(self.h.control), expected_broker_id=observed["broker_id"],
                                 expected_generation=observed["generation"], expected_cursor=observed["cursor"])
        self.journal = DecisionJournal(Path(self.h.temp.name) / "decisions.sqlite3")
        self.addCleanup(self.journal.close)

    def cli(self, decision):
        args = [sys.executable, str(WORKSPACE / "scripts/relay_once.py"),
                "--journal", str(self.journal.path)]
        for key in ("decision_id", "actor", "source", "control",
                    "expected_broker_id", "expected_generation", "expected_cursor", "expires_at"):
            args += ["--" + key.replace("_", "-"), str(decision[key])]
        args.append(decision["command"])
        completed = subprocess.run(args, cwd=WORKSPACE, env=self.h.env, capture_output=True,
                                   text=True, encoding="utf-8", timeout=25)
        return completed, json.loads(completed.stdout)

    def test_real_mcp_cli_sends_once_and_duplicate_process_never_reopens_claim(self):
        process, first = self.cli(self.decision)
        self.assertEqual(process.returncode, 0, process.stderr)
        self.assertEqual(first["delivery"], "transport_accepted")
        self.assertEqual(self.h.fake.commands, ["look"])
        self.assertEqual(self.h.call(op="status")["owner"], None)
        epoch = self.h.call(op="status")["lease_epoch"]
        repeated_process, repeated = self.cli(self.decision)
        self.assertEqual(repeated_process.returncode, 0, repeated_process.stderr)
        self.assertTrue(repeated["duplicate"])
        self.assertFalse(repeated["new_send"])
        self.assertEqual(self.h.fake.commands, ["look"])
        self.assertEqual(self.h.call(op="status")["lease_epoch"], epoch)

    def test_stale_original_tuple_is_not_refreshed_by_real_mcp_relay(self):
        self.h.call(op="claim", owner="human", takeover=True)
        self.h.call(op="send", command="synthetic-intervening-event", owner="human", timeout=1, quiet=.05)
        self.h.call(op="release", owner="human")
        process, receipt = self.cli(self.decision)
        self.assertEqual(process.returncode, 1)
        self.assertIn("no longer matches", receipt["rejection"])
        self.assertEqual(self.h.fake.commands, ["synthetic-intervening-event"])

    def test_new_events_after_preflight_reach_atomic_core_guard_without_refresh(self):
        @asynccontextmanager
        async def client_factory(control):
            async with relay.mcp_client(control) as client:
                class Proxy:
                    async def initialize(self):
                        return await client.initialize()

                    async def call_tool(inner, name, args):
                        returned = await client.call_tool(name, args)
                        if name == "mud_claim":
                            # A separate synthetic command creates new observations
                            # after relay preflight and before its authored send.
                            event = await client.call_tool("mud_send", {
                                "owner": self.decision["actor"], "command": "synthetic-event",
                                "timeout": 1})
                            self.assertFalse(event.is_error)
                        return returned
                yield Proxy()

        async def run():
            return await relay.relay(self.decision, self.journal, client_factory)

        receipt = asyncio.run(run())
        self.assertIn("expected_cursor changed", receipt["send_error"])
        self.assertEqual(receipt["delivery"], "unknown")  # Tool error text is not a typed delivery proof.
        self.assertEqual(self.h.fake.commands, ["synthetic-event"])
        self.assertTrue(receipt["released"])


if __name__ == "__main__":
    unittest.main()

