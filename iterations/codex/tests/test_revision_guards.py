"""Regression tests for Red's client findings; synthetic data and loopback only."""
import asyncio
import contextlib
import importlib.util
import io
import json
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import AsyncMock, Mock, patch

from mudgpt.session import Session
from test_broker import BrokerHarness

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("game_command", ROOT / "scripts/game_command.py")
helper = importlib.util.module_from_spec(spec)
spec.loader.exec_module(helper)


class GuardTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.session = Session()
        self.session._connected = True
        self.session._writer = Mock(drain=AsyncMock())
        self.observation = self.session.observe()
        self.guard = helper.guards_of(self.observation)

    async def test_distinct_broker_lifetimes_reject_same_integer_cursor(self):
        other = Session()
        self.assertIn("guarded_send_v1", other.status()["capabilities"])
        self.assertEqual(other.status()["cursor"], self.observation["cursor"])
        self.assertEqual(other.status()["generation"], self.observation["generation"])
        self.assertNotEqual(other.status()["broker_id"], self.observation["broker_id"])
        with self.assertRaisesRegex(RuntimeError, "Broker lifetime"):
            other.observe(0, expected_broker_id=self.observation["broker_id"])
        self.assertEqual(self.session.status()["broker_id"], self.session.observe()["broker_id"])

    async def test_stale_broker_generation_or_cursor_sends_no_bytes_or_attempt(self):
        for key in self.guard:
            wrong = dict(self.guard)
            wrong[key] = "other-broker" if key.endswith("broker_id") else wrong[key] + 1
            with self.assertRaisesRegex(RuntimeError, "nothing sent"):
                await self.session.send("north", **wrong)
            self.session._writer.write.assert_not_called()
            self.assertEqual(self.session.observe(), self.observation)

    async def test_ambient_event_between_observe_and_send_is_rejected(self):
        self.session._add("text", text="A synthetic stranger arrives.")
        with self.assertRaisesRegex(RuntimeError, "expected_cursor changed"):
            await self.session.send("north", **self.guard)
        self.session._writer.write.assert_not_called()
        self.assertFalse(any(event["kind"] == "send_attempt" for event in self.session.observe()["events"]))

    async def test_partial_or_malformed_guards_rejected(self):
        cases = [{"expected_cursor": 0},
                 {**self.guard, "expected_cursor": True},
                 {**self.guard, "expected_generation": -1},
                 {**self.guard, "expected_broker_id": ""}]
        for guard in cases:
            with self.assertRaises(ValueError):
                await self.session.send("north", **guard)
        self.session._writer.write.assert_not_called()

    async def test_expiry_rechecked_at_broker_write_boundary(self):
        for expiry in (True, float("nan"), float("inf"), "tomorrow"):
            with self.assertRaises(ValueError):
                await self.session.send("north", expires_at=expiry, **self.guard)
        with patch("mudgpt.session.time.time", return_value=100):
            for expiry in (99, 100):
                with self.assertRaisesRegex(RuntimeError, "Decision expired"):
                    await self.session.send("north", expires_at=expiry, **self.guard)
        self.session._writer.write.assert_not_called()
        self.assertEqual(self.session.observe(), self.observation)

    async def test_matching_guards_write_once_and_preserve_receipt(self):
        result = await self.session.send("look", timeout=.01, quiet=.01,
                                         expires_at=time.time() + 30, **self.guard)
        self.session._writer.write.assert_called_once_with("look\r\n")
        self.assertEqual(result["broker_id"], self.observation["broker_id"])
        self.assertEqual(result["delivery"], "transport_accepted")
        self.assertFalse(result["command_complete"])

    async def test_connect_result_has_finished_connecting_and_id_survives_reconnect(self):
        session = Session()
        reader = Mock(read=AsyncMock(return_value=""))
        writer = Mock(wait_closed=AsyncMock())
        with patch("mudgpt.session.telnetlib3.open_connection",
                   AsyncMock(return_value=(reader, writer))):
            first = await session.connect("127.0.0.1", 1234)
            self.assertTrue(first["connected"])
            self.assertFalse(first["connecting"])
            await session.close()
            second = await session.connect("127.0.0.1", 1234)
            self.assertEqual(second["broker_id"], first["broker_id"])
            self.assertEqual(second["generation"], first["generation"] + 1)
            self.assertFalse(second["connecting"])
            await session.close()

    async def test_protocol_suppression_is_constant_after_secret_registration(self):
        data = {"hp": 42, "name": "synthetic"}
        self.session._protocol("GMCP", data)
        self.assertEqual(json.loads(self.session.observe()["events"][-1]["data"]), data)
        self.session._secrets = ["fixture-key-on-1"]
        outputs = []
        for payload in ("1", "on", "unrelated", 1, 42, {"fixture": "x"}, {"hp": 12},
                        ["k", 9, None], "x" * 8192):
            self.session._protocol("GMCP", payload)
            event = self.session.observe()["events"][-1]
            outputs.append((event["data"], event["truncated"]))
        self.assertEqual(len(set(outputs)), 1)
        self.assertEqual(json.loads(outputs[0][0]),
                         {"redacted": True, "reason": "session_contains_secrets"})
        self.assertFalse(outputs[0][1])


class HelperTests(unittest.TestCase):
    def invoke(self, previous, *, pages=False, observation_failure=False, blank_no_text=False):
        with tempfile.TemporaryDirectory() as directory:
            control = Path(directory) / "control.json"
            dest = Path(directory) / "observations.jsonl"
            dest.write_text(json.dumps(previous) + "\n", encoding="utf-8")
            calls, cursor = [], 3

            def request(payload, _control):
                nonlocal cursor
                calls.append(payload)
                if payload["op"] == "status":
                    return {"broker_id": "new", "generation": 1, "cursor": cursor}
                if payload["op"] == "send":
                    cursor += 2
                    text = "[Hit Return to continue]" if pages and cursor == 5 else "Fixture reply"
                    result = {"broker_id": "new", "generation": 1, "cursor": cursor,
                              "command_id": f"1:{cursor}", "delivery": "transport_accepted",
                              "reason": "quiet", "command_complete": False, "truncated": False,
                              "events": [{"seq": cursor - 1, "kind": "send_attempt"},
                                         {"seq": cursor, "kind": "text", "text": text}]}
                    if blank_no_text and payload["command"] == "":
                        result["events"] = result["events"][:1]
                        result["reason"] = "timeout"
                    return result
                if observation_failure:
                    raise RuntimeError("Synthetic broker connection lost after command")
                return {"events": [{"kind": "text", "seq": 1, "text": "New broker ambient"}],
                        "cursor": cursor, "truncated": False}

            output, errors = io.StringIO(), io.StringIO()
            with patch.object(helper, "request", request), contextlib.redirect_stdout(output), \
                    contextlib.redirect_stderr(errors):
                entry = helper.run(control, "look", pages=pages)
            persisted = json.loads(dest.read_text(encoding="utf-8").splitlines()[-1])
            self.assertEqual(entry, persisted)
            return entry, calls, output.getvalue(), errors.getvalue()

    def test_null_and_ahead_old_cursors_reset_before_send(self):
        for cursor in (None, 99):
            entry, calls, output, _ = self.invoke({"receipt": {"cursor": cursor}})
            self.assertEqual(calls[0]["op"], "status")
            self.assertEqual(calls[-1]["after"], 0)
            self.assertIn("Fixture reply", output)
            self.assertEqual(entry["receipt"]["command_id"], "1:5")
            self.assertEqual([c["op"] for c in calls].count("send"), 1)

    def test_old_broker_same_cursor_recovers_new_ambient(self):
        entry, calls, _, _ = self.invoke({"receipt": {"cursor": 3, "broker_id": "old"}})
        self.assertEqual(calls[-1]["after"], 0)
        self.assertEqual(entry["ambient_before"], "New broker ambient")
        self.assertEqual(calls[1]["expected_broker_id"], "new")
        self.assertEqual(calls[1]["expected_generation"], 1)
        self.assertEqual(calls[1]["expected_cursor"], 3)

    def test_post_send_observation_failure_still_persists_and_prints_receipt(self):
        entry, calls, output, errors = self.invoke(
            {"receipt": {"cursor": 2, "broker_id": "new"}}, observation_failure=True)
        self.assertIn("observation_error", entry)
        self.assertIn("Fixture reply", output)
        self.assertIn('"command_id": "1:5"', output)
        self.assertIn("do not automatically resend", errors)
        self.assertEqual([c["op"] for c in calls].count("send"), 1)

    def test_pager_receipts_separate_and_next_cursor_after_last_page(self):
        entry, calls, _, _ = self.invoke({"receipt": {"cursor": None}}, pages=True)
        self.assertEqual(entry["receipt"]["command_id"], "1:5")
        self.assertEqual([r["command_id"] for r in entry["pager_receipts"]], ["1:7"])
        self.assertEqual(entry["observation_cursor"], 7)
        self.assertEqual([c["command"] for c in calls if c["op"] == "send"], ["look", ""])

    def test_pager_does_not_reuse_old_marker_after_blank_send_times_out(self):
        entry, calls, _, _ = self.invoke({"receipt": {"cursor": None}},
                                       pages=True, blank_no_text=True)
        self.assertEqual([c["command"] for c in calls if c["op"] == "send"], ["look", ""])
        self.assertEqual(len(entry["pager_receipts"]), 1)
        self.assertEqual(entry["pager_receipts"][0]["reason"], "timeout")


class GuardBrokerIntegration(unittest.TestCase):
    def test_guard_fields_reach_broker_and_reject_before_network_write(self):
        harness = BrokerHarness()
        self.addCleanup(harness.close)
        connected = harness.connect()
        self.assertFalse(connected["connecting"])
        # The fixture greeting must have reached the broker before this decision.
        until = time.monotonic() + 2
        while time.monotonic() < until:
            observation = harness.call(op="observe")
            if any(e["kind"] == "text" for e in observation["events"]):
                break
            time.sleep(.01)
        guards = helper.guards_of(observation)
        for mutation in ({"expected_broker_id": "old"},
                         {"expected_generation": observation["generation"] + 1},
                         {"expected_cursor": observation["cursor"] + 1},
                         {"expires_at": time.time() - 1}):
            with self.assertRaisesRegex(RuntimeError, "nothing sent"):
                harness.call(op="send", command="north", **{**guards, **mutation})
        self.assertEqual(harness.fake.commands, [])
        result = harness.call(op="send", command="look", timeout=1, quiet=.05, **guards)
        self.assertEqual(result["broker_id"], observation["broker_id"])
        self.assertEqual(harness.fake.commands, ["look"])
