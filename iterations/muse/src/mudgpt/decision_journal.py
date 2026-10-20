"""Durable admission for a local one-shot courier, independent of any actor.

Use one stable database on a filesystem with working SQLite locking/fsync.
An admitted ID is never retried, even if its last checkpoint is unfinished.
"""
import json
import os
from pathlib import Path
import sqlite3


class DecisionConflict(ValueError):
    """The same stable ID was presented with different authored content."""


def encoded(value):
    return json.dumps(value, ensure_ascii=True, sort_keys=True, allow_nan=False)


class DecisionJournal:
    def __init__(self, path):
        self.path = Path(path).resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(self.path, timeout=10)
        try:
            os.chmod(self.path, 0o600)
            self.connection.execute("PRAGMA journal_mode=DELETE")
            self.connection.execute("PRAGMA synchronous=FULL")
            with self.connection:
                self.connection.execute("""CREATE TABLE IF NOT EXISTS relay_decisions (
                    decision_id TEXT PRIMARY KEY,
                    decision TEXT NOT NULL,
                    receipt TEXT NOT NULL
                )""")
        except BaseException:
            self.connection.close()
            raise

    def admit(self, decision_id, decision, receipt):
        """Commit one admission or return the existing receipt; never resume it."""
        authored = encoded(decision)
        with self.connection:
            self.connection.execute("BEGIN IMMEDIATE")
            previous = self.connection.execute(
                "SELECT decision, receipt FROM relay_decisions WHERE decision_id=?",
                (decision_id,)).fetchone()
            if previous:
                if previous[0] != authored:
                    raise DecisionConflict("decision ID already names different authored content; nothing sent")
                saved = json.loads(previous[1])
                if not isinstance(saved, dict):
                    raise ValueError("invalid existing receipt; journal requires manual inspection")
                return False, saved
            self.connection.execute(
                "INSERT INTO relay_decisions(decision_id, decision, receipt) VALUES(?,?,?)",
                (decision_id, authored, encoded(receipt)))
        return True, receipt

    def save(self, decision_id, receipt):
        with self.connection:
            updated = self.connection.execute(
                "UPDATE relay_decisions SET receipt=? WHERE decision_id=?",
                (encoded(receipt), decision_id))
            if updated.rowcount != 1:
                raise RuntimeError("decision was not admitted; refusing an untracked operation")

    def close(self):
        self.connection.close()

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()
