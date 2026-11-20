"""Observation checkpoint and original-decision regressions; fake calls only."""
import contextlib
import importlib.util
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("checkpoint_helper", ROOT / "scripts/game_command.py")
helper = importlib.util.module_from_spec(spec)
spec.loader.exec_module(helper)


class CheckpointTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.control = Path(self.temp.name) / "control.json"
        self.calls = []
        self.cursor = 3

    def call(self, payload, control):
        self.calls.append(payload)
        base = {"broker_id": "fixture", "generation": 1, "cursor": self.cursor,
                "lease_epoch": 0, "truncated": False}
        if payload["op"] == "status": return base
        if payload["op"] == "observe":
            return {**base, "events": [{"kind": "text", "seq": self.cursor, "text": "A fixture arrives.\n"}]
                    if payload["after"] < self.cursor else []}
        if payload["op"] == "send":
            self.cursor += 2
            return {**base, "cursor": self.cursor, "command_id": "1:1", "reason": "prompt",
                    "delivery": "transport_accepted", "command_complete": False,
                    "events": [{"kind": "send_attempt", "seq": self.cursor - 1},
                               {"kind": "text", "seq": self.cursor, "text": "Done.\n"}]}

    def test_prepare_is_read_only_and_run_decision_keeps_original_cursor(self):
        with patch.object(helper, "request", self.call), patch.object(helper.time, "time", return_value=100), \
                contextlib.redirect_stdout(io.StringIO()):
            prepared = helper.prepare(self.control)
            self.assertFalse(any(c["op"] == "send" for c in self.calls))
            self.cursor = 8
            helper.run_decision(self.control, "look", prepared["next_decision"])
        sent = next(c for c in self.calls if c["op"] == "send")
        self.assertEqual(sent["expected_cursor"], 3)
        self.assertEqual(sent["expected_lease_epoch"], 0)
        self.assertEqual(sent["expires_at"], 220)

    def test_observe_only_checkpoint_prevents_old_ambient_repetition(self):
        with patch.object(helper, "request", self.call), contextlib.redirect_stdout(io.StringIO()):
            observed = helper.observe_only(self.control)
            result = helper.run(self.control, "look")
        self.assertIn("fixture arrives", observed["text"])
        self.assertEqual(result["ambient_before"], "")

    def test_growing_journal_is_never_read_with_read_text(self):
        dest = self.control.parent / "observations.jsonl"
        with dest.open("w", encoding="utf-8") as stream:
            stream.write('x' * (2 * 1024 * 1024) + '\n')
            stream.write(json.dumps({"receipt": {"broker_id": "fixture", "cursor": 2}}) + '\n')
        original = Path.read_text
        def guarded(path, *args, **kwargs):
            if path == dest: raise AssertionError("full journal read")
            return original(path, *args, **kwargs)
        with patch.object(Path, "read_text", guarded), patch.object(helper, "request", self.call), \
                contextlib.redirect_stdout(io.StringIO()):
            helper.run(self.control, "look")
        self.assertEqual(self.calls[-1]["after"], 2)

    def test_partial_last_journal_line_preserves_prior_complete_checkpoint(self):
        dest = self.control.parent / "observations.jsonl"
        dest.write_text(json.dumps({"receipt": {"broker_id": "fixture", "cursor": 2}})
                        + '\n{"incomplete":', encoding="utf-8")
        with patch.object(helper, "request", self.call), contextlib.redirect_stdout(io.StringIO()):
            helper.run(self.control, "look")
        self.assertEqual(self.calls[-1]["after"], 2)
        last = dest.read_text().splitlines()[-1]
        self.assertEqual(json.loads(last)["command"], "look")

    def test_expired_prepared_decision_sends_nothing(self):
        with patch.object(helper, "request", self.call), patch.object(helper.time, "time", return_value=100), \
                contextlib.redirect_stdout(io.StringIO()):
            prepared = helper.prepare(self.control)
        self.calls.clear()
        with patch.object(helper, "request", self.call), patch.object(helper.time, "time", return_value=221):
            with self.assertRaisesRegex(ValueError, "expired"):
                helper.run_decision(self.control, "north", prepared["next_decision"])
        self.assertEqual(self.calls, [])


if __name__ == "__main__":
    unittest.main()
