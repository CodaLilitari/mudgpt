"""Decision history retention using disposable local SQLite files only."""
from contextlib import closing
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest

from mudgpt.decision_journal import DecisionJournal


class JournalRetentionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "decisions.sqlite3"
        self.marker = self.path.with_name(self.path.name + ".initialized")
        self.decision = {"command": "look", "broker": "fixture", "cursor": 4}
        self.pending = {"phase": "send_pending", "delivery": "unknown"}

    def test_new_journal_creates_retained_initialization_marker(self):
        with DecisionJournal(self.path) as journal:
            fresh, receipt = journal.admit("fixture-1", self.decision, self.pending)
        self.assertTrue(fresh)
        self.assertEqual(receipt["delivery"], "unknown")
        self.assertTrue(self.marker.is_file())

    def test_existing_id_survives_restart_with_saved_receipt(self):
        with DecisionJournal(self.path) as journal:
            journal.admit("fixture-1", self.decision, self.pending)
            journal.save("fixture-1", {"phase": "returned", "delivery": "transport_accepted"})
        with DecisionJournal(self.path) as journal:
            fresh, receipt = journal.admit("fixture-1", self.decision, self.pending)
        self.assertFalse(fresh)
        self.assertEqual(receipt["delivery"], "transport_accepted")

    def test_missing_initialized_database_is_rejected_without_recreation(self):
        with DecisionJournal(self.path) as journal:
            journal.admit("fixture-1", self.decision, self.pending)
        self.path.unlink()
        with self.assertRaisesRegex(OSError, "missing"):
            DecisionJournal(self.path)
        self.assertFalse(self.path.exists())
        self.assertTrue(self.marker.is_file())

    def test_corrupt_or_empty_existing_database_is_rejected_without_replacement(self):
        for contents in (b"not a SQLite database", b""):
            with self.subTest(contents=contents):
                if not self.path.exists():
                    with DecisionJournal(self.path):
                        pass
                self.path.write_bytes(contents)
                with self.assertRaises((OSError, sqlite3.DatabaseError)):
                    DecisionJournal(self.path)
                self.assertEqual(self.path.read_bytes(), contents)

    def test_pending_duplicate_remains_unknown_after_restart(self):
        with DecisionJournal(self.path) as journal:
            journal.admit("fixture-1", self.decision, self.pending)
        with DecisionJournal(self.path) as journal:
            fresh, receipt = journal.admit("fixture-1", self.decision, {"delivery": "not_sent"})
        self.assertFalse(fresh)
        self.assertEqual(receipt, {"phase": "send_pending", "delivery": "unknown"})

    def test_legacy_database_adopts_marker_without_losing_ids(self):
        with closing(sqlite3.connect(self.path)) as connection:
            with connection:
                connection.execute("CREATE TABLE relay_decisions (decision_id TEXT PRIMARY KEY, decision TEXT NOT NULL, receipt TEXT NOT NULL)")
                connection.execute("INSERT INTO relay_decisions VALUES (?, ?, ?)",
                                   ("fixture-1", json.dumps(self.decision, sort_keys=True), json.dumps(self.pending)))
        with DecisionJournal(self.path) as journal:
            fresh, receipt = journal.admit("fixture-1", self.decision, {"delivery": "not_sent"})
        self.assertFalse(fresh)
        self.assertEqual(receipt["delivery"], "unknown")
        self.assertTrue(self.marker.is_file())

    def test_invalid_marker_is_not_silently_replaced(self):
        with DecisionJournal(self.path) as journal:
            journal.admit("fixture-1", self.decision, self.pending)
        self.marker.write_bytes(b"interrupted marker write")
        with self.assertRaises(OSError):
            DecisionJournal(self.path)
        self.assertEqual(self.marker.read_bytes(), b"interrupted marker write")


if __name__ == "__main__":
    unittest.main()
