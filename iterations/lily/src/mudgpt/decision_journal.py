"""Durable admission for a local one-shot courier, independent of any actor.

Use one stable database on a filesystem with working SQLite locking/fsync.
An admitted ID is never retried, even if its last checkpoint is unfinished.
Retain the database AND its .initialized marker, including during archival.
Never prune/reset them to bypass consumed IDs. The marker detects isolated
database loss; losing both files or rolling both back is not detectable here.
Keep the sibling .init.lock inode in place; never delete an initialization lock.
"""
from contextlib import contextmanager
import errno
import json
import os
from pathlib import Path
import sqlite3
import stat
import time


_INITIALIZED = b"MUDweave decision journal initialized v1\n"


class DecisionConflict(ValueError):
    """The same stable ID was presented with different authored content."""


def encoded(value):
    return json.dumps(value, ensure_ascii=True, sort_keys=True, allow_nan=False)


@contextmanager
def _initialization_lock(path):
    """Serialize first initialization and retained-file checks across processes."""
    deadline = time.monotonic() + 10
    descriptor = os.open(path, os.O_RDWR | os.O_CREAT, 0o600)
    with os.fdopen(descriptor, "r+b") as lock:
        info = os.fstat(lock.fileno())
        if not stat.S_ISREG(info.st_mode):
            raise OSError("decision journal initialization lock must be a regular file")
        while True:
            try:
                lock.seek(0)
                if os.name == "nt":
                    import msvcrt
                    msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except OSError as error:
                if error.errno not in (errno.EACCES, errno.EAGAIN, errno.EDEADLK):
                    raise
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError("decision journal initialization lock timed out; nothing sent") from None
                time.sleep(min(0.05, remaining))
        if os.fstat(lock.fileno()).st_size == 0:
            # Byte-range locks may cover an empty file. Populate it only after
            # locking so another constructor cannot write through a held lock.
            lock.write(b"\0")
            lock.flush()
        # Closing this handle releases the lock, including after exceptions.
        yield


class DecisionJournal:
    def __init__(self, path):
        self.path = Path(path).resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        lock_path = self.path.with_name(self.path.name + ".init.lock")
        with _initialization_lock(lock_path):
            self._initialize()

    def _initialize(self):
        retained = self._retain_marker()
        if retained:
            # SQLite accepts an empty file as a fresh database. That must not
            # silently erase consumed IDs after truncation of a retained file.
            with self.path.open("rb") as source:
                if source.read(16) != b"SQLite format 3\0":
                    raise sqlite3.DatabaseError("retained decision journal is invalid; restore it before use")
            # An isolated deletion between inspection and opening cannot create
            # a fresh database through SQLite's default create-if-missing mode.
            self.connection = sqlite3.connect(self.path.as_uri() + "?mode=rw", uri=True, timeout=10)
        else:
            self.connection = sqlite3.connect(self.path, timeout=10)
        try:
            if retained:
                columns = self.connection.execute("PRAGMA table_info(relay_decisions)").fetchall()
                shape = {row[1]: (row[2].upper(), row[5]) for row in columns}
                if shape != {"decision_id": ("TEXT", 1), "decision": ("TEXT", 0), "receipt": ("TEXT", 0)}:
                    raise sqlite3.DatabaseError("retained decision journal schema is invalid; restore it before use")
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

    def _retain_marker(self):
        """Publish an exclusive, durable initialization mark before DB writes."""
        marker = self.path.with_name(self.path.name + ".initialized")
        try:
            descriptor = os.open(marker, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError:
            with marker.open("rb") as source:
                if source.read(len(_INITIALIZED) + 1) != _INITIALIZED:
                    raise OSError("decision journal initialization marker is invalid; inspect retained state")
            if not self.path.is_file():
                raise FileNotFoundError("initialized decision journal is missing; restore the database and retain its marker")
            return True
        with os.fdopen(descriptor, "wb") as output:
            output.write(_INITIALIZED)
            output.flush()
            os.fsync(output.fileno())
        if os.name != "nt":
            directory = os.open(self.path.parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
        # Existing legacy databases adopt a marker before any database writes.
        return self.path.exists()

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
