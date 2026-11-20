"""Portable actor tests: fake broker only, no game or provider connection."""
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from mudgpt import actor_client
from mudgpt.actor_client import ActorClient
from mudgpt.decision_journal import DecisionJournal
from mudgpt.errors import BrokerRequestError


class FakeBroker:
    def __init__(self):
        self.calls = []
        self.cursor = 3
        self.failure = None
        self.truncated = False

    def __call__(self, payload, control):
        self.calls.append(dict(payload))
        base = {"broker_id": "fixture-broker", "generation": 2, "cursor": self.cursor,
                "lease_epoch": 4, "owner": "fixture-player", "connected": True,
                "capabilities": ["guarded_send_v1", "conditional_actions_v1"]}
        if payload["op"] == "claim":
            return {**base, "lease": "private-fixture-lease"}
        if payload["op"] == "release":
            return {**base, "owner": None, "lease_epoch": 5}
        if payload["op"] == "observe":
            return {**base, "truncated": self.truncated, "oldest": 1,
                    "events": [{"kind": "text", "seq": self.cursor, "generation": 2,
                                "text": "A blue lamp glows.\n<20hp 30m 40mv> ",
                                "style_spans": [{"start": 2, "end": 6, "fg": "blue"}]}]}
        if payload["op"] in ("send", "conditional"):
            if self.failure:
                raise self.failure
            self.cursor += 1
            return {**base, "cursor": self.cursor, "delivery": "transport_accepted",
                    "command_id": "2:1", "command_complete": False, "reason": "prompt",
                    "events": [{"kind": "text", "seq": self.cursor,
                                "text": "You look around.\n<20hp 30m 40mv> "}]}
        return base


class ActorTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.config = self.root / "actor.json"
        self.config.write_text(json.dumps({"actor": "fixture-player", "control": "control.json",
                                          "state_dir": "private-state", "profile": "none",
                                          "max_ttl_seconds": 120}), encoding="utf-8")
        self.broker = FakeBroker()
        self.client = ActorClient(self.config, call=self.broker, now_fn=lambda: 100)
        self.client.execute("claim")
        self.broker.calls.clear()

    def decision(self, **changes):
        return {"decision_id": "fixture-decision-1", "command": "look", "broker": "fixture-broker",
                "generation": 2, "cursor": 3, "lease_epoch": 4, "expires_at": 190, **changes}

    def test_relocated_config_and_claim_keep_lease_private(self):
        self.assertEqual(self.client.control, self.root / "control.json")
        output = self.client.execute("status")
        self.assertNotIn("private-fixture-lease", json.dumps(output))
        state = json.loads((self.root / "private-state" / "actor-state.json").read_text())
        self.assertEqual(state["lease"], "private-fixture-lease")

    def test_send_preserves_original_tuple_even_when_ambient_cursor_advances(self):
        self.broker.cursor = 9
        result = self.client.execute("send", **self.decision())
        sent = [c for c in self.broker.calls if c["op"] == "send"]
        self.assertEqual(len(sent), 1)
        self.assertEqual(sent[0]["expected_cursor"], 3)
        self.assertEqual(sent[0]["expected_lease_epoch"], 4)
        self.assertEqual(sent[0]["expires_at"], 190)
        self.assertEqual(result["receipt"]["delivery"], "transport_accepted")
        self.assertNotIn("private-fixture-lease", json.dumps(result))
        journal = (self.root / "private-state" / "decisions.sqlite3").read_bytes()
        self.assertNotIn(b"private-fixture-lease", journal)

    def test_duplicate_after_new_process_returns_saved_receipt_without_call(self):
        self.client.execute("send", **self.decision())
        self.broker.calls.clear()
        restarted = ActorClient(self.config, call=self.broker, now_fn=lambda: 300)
        duplicate = restarted.execute("send", **self.decision())
        self.assertTrue(duplicate["duplicate"])
        self.assertFalse(duplicate["new_send"])
        self.assertEqual(self.broker.calls, [])

    def test_conflicting_decision_id_never_dispatches(self):
        self.client.execute("send", **self.decision())
        self.broker.calls.clear()
        with self.assertRaisesRegex(ValueError, "different authored"):
            self.client.execute("send", **self.decision(command="north"))
        self.assertEqual(self.broker.calls, [])

    def test_unknown_outcome_is_durable_and_never_replayed(self):
        self.broker.failure = RuntimeError("old broker did not return a receipt")
        result = self.client.execute("send", **self.decision())
        self.assertEqual(result["receipt"]["delivery"], "unknown")
        self.broker.calls.clear()
        duplicate = self.client.execute("send", **self.decision())
        self.assertTrue(duplicate["duplicate"])
        self.assertEqual(self.broker.calls, [])
        with self.assertRaisesRegex(ValueError, "observation"):
            self.client.execute("send", **self.decision(decision_id="new-decision"))

    def test_truncated_observation_does_not_clear_unknown_outcome_gate(self):
        self.broker.failure = RuntimeError("uncertain request")
        self.client.execute("send", **self.decision())
        self.broker.truncated = True
        observed = self.client.execute("observe")
        self.assertTrue(observed["receipt"]["truncated"])
        self.assertNotIn("next_decision", observed)
        with self.assertRaisesRegex(ValueError, "observation"):
            self.client.execute("send", **self.decision(decision_id="new-decision"))

    def test_observation_advances_cursor_and_retains_color_and_epoch(self):
        result = self.client.execute("observe")
        self.assertEqual(result["next_decision"]["lease_epoch"], 4)
        self.assertEqual(result["next_decision"]["expires_at"], 220)
        self.assertTrue(result["style_spans"])
        self.assertIn("[fg=blue]blue[/style]", result["annotated"])
        self.broker.calls.clear()
        self.client.execute("observe")
        self.assertEqual(self.broker.calls[-1]["after"], 3)

    def test_release_clears_lease_without_stopping_broker(self):
        result = self.client.execute("release")
        self.assertIsNone(result["receipt"]["owner"])
        self.assertEqual([c["op"] for c in self.broker.calls], ["release"])
        state = json.loads((self.root / "private-state" / "actor-state.json").read_text())
        self.assertNotIn("lease", state)

    def test_retreat_has_one_fixed_action_and_original_epoch(self):
        decision = self.decision()
        decision.pop("command")
        result = self.client.execute("retreat", enemy_status="A fixture is in perfect health.",
                                     allow_afk_combat=True, **decision)
        forwarded = next(c for c in self.broker.calls if c["op"] == "conditional")
        self.assertEqual(forwarded["action"], "lok_retreat_v1")
        self.assertNotIn("command", forwarded)
        self.assertEqual(forwarded["expected_lease_epoch"], 4)
        self.assertEqual(forwarded["expected_cursor"], 3)
        self.assertTrue(forwarded["condition"]["allow_afk_combat"])
        self.assertNotIn("private-fixture-lease", json.dumps(result))

    def test_invalid_actor_and_repo_state_are_rejected(self):
        data = json.loads(self.config.read_text())
        data["actor"] = "human"
        self.config.write_text(json.dumps(data))
        with self.assertRaises(ValueError):
            ActorClient(self.config, call=self.broker)
        data["actor"] = "fixture-player"
        (self.root / ".git").mkdir()
        self.config.write_text(json.dumps(data))
        with self.assertRaisesRegex(ValueError, "Git"):
            ActorClient(self.config, call=self.broker)

    def test_private_state_failure_before_dispatch_is_not_sent(self):
        with patch.object(actor_client, "atomic_json", side_effect=OSError("fixture disk full")):
            result = self.client.execute("send", **self.decision())
        self.assertEqual(result["receipt"]["delivery"], "not_sent")
        self.assertFalse(result["send_attempted"])
        self.assertEqual(self.broker.calls, [])

    def test_pending_journal_failure_before_dispatch_is_not_sent(self):
        with patch.object(DecisionJournal, "save", side_effect=sqlite3.OperationalError("fixture disk full")):
            result = self.client.execute("send", **self.decision())
        self.assertEqual(result["receipt"]["delivery"], "not_sent")
        self.assertFalse(result["send_attempted"])
        self.assertEqual(self.broker.calls, [])

    def test_final_journal_failure_preserves_accepted_receipt_without_replay(self):
        original_save = DecisionJournal.save
        def fail_final(journal, decision_id, receipt):
            if receipt.get("phase") == "returned":
                raise sqlite3.OperationalError("fixture final checkpoint failure")
            return original_save(journal, decision_id, receipt)
        with patch.object(DecisionJournal, "save", new=fail_final):
            result = self.client.execute("send", **self.decision())
        self.assertEqual(result["receipt"]["delivery"], "transport_accepted")
        self.assertIn("journal_error", result)
        self.broker.calls.clear()
        duplicate = self.client.execute("send", **self.decision())
        self.assertTrue(duplicate["duplicate"])
        self.assertEqual(duplicate["receipt"]["delivery"], "unknown")
        self.assertEqual(self.broker.calls, [])

    def test_only_typed_validated_prewrite_refusal_is_not_sent(self):
        errors = [
            (BrokerRequestError({"error_schema": 1, "error": "fixture refusal", "error_code": "cursor_changed",
                                 "delivery": "not_sent"}), "not_sent"),
            (RuntimeError("expected_cursor changed; nothing sent"), "unknown"),
            (BrokerRequestError({"error_schema": 1, "error": "fixture refusal", "error_code": "cursor_changed",
                                 "delivery": "unknown"}), "unknown"),
        ]
        for number, (failure, delivery) in enumerate(errors):
            with self.subTest(delivery=delivery, exception=type(failure).__name__):
                self.client.execute("observe")
                self.broker.failure = failure
                result = self.client.execute("send", **self.decision(decision_id=f"typed-{number}"))
                self.assertEqual(result["receipt"]["delivery"], delivery)
                self.assertNotIn("next_decision", result)


if __name__ == "__main__":
    unittest.main()
