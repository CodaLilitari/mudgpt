"""Concurrent real subprocess startup and crash-release; no live MUD access."""
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest

from mudgpt.broker import request


class BrokerLockTests(unittest.TestCase):
    def test_simultaneous_start_has_one_winner_and_crash_releases_lock(self):
        workspace = Path(__file__).resolve().parents[1]
        env = {**os.environ, "PYTHONPATH": str(workspace / "src")}
        script = ("import sys; from mudgpt.broker import serve; "
                  "print('armed', flush=True); input(); serve(sys.argv[1])")
        children = []
        with tempfile.TemporaryDirectory(prefix="mudgpt-lock-test-") as directory:
            control = Path(directory) / "control.json"

            def spawn():
                child = subprocess.Popen(
                    [sys.executable, "-c", script, str(control)], cwd=workspace, env=env,
                    stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                    text=True, encoding="utf-8")
                children.append(child)
                self.assertEqual(child.stdout.readline().strip(), "armed")
                return child

            def go(child):
                child.stdin.write("go\n")
                child.stdin.flush()

            def ready():
                deadline = time.monotonic() + 8
                while time.monotonic() < deadline:
                    try:
                        if control.exists():
                            return request({"op": "status"}, control)
                    except (OSError, ValueError, RuntimeError):
                        pass
                    time.sleep(.02)
                self.fail("broker did not become ready")

            try:
                first, second = spawn(), spawn()
                go(first)
                go(second)
                ready()
                deadline = time.monotonic() + 5
                while all(c.poll() is None for c in (first, second)) and time.monotonic() < deadline:
                    time.sleep(.02)
                running = [c for c in (first, second) if c.poll() is None]
                self.assertEqual(len(running), 1, "both brokers survived concurrent startup")
                winner = running[0]
                loser = second if winner is first else first
                _, error = loser.communicate(timeout=2)
                self.assertNotEqual(loser.returncode, 0)
                self.assertIn("control file lock", error)
                saved = control.read_bytes()
                self.assertFalse(ready()["connected"])
                self.assertEqual(control.read_bytes(), saved)

                winner.kill()
                winner.communicate(timeout=3)
                self.assertTrue(control.exists(), "crash fixture should leave stale control")
                replacement = spawn()
                go(replacement)
                ready()
                self.assertNotEqual(control.read_bytes(), saved)
                request({"op": "stop", "owner": "human"}, control)
                _, error = replacement.communicate(timeout=5)
                self.assertEqual(replacement.returncode, 0, error)
                self.assertFalse(control.exists())
                self.assertTrue(control.with_name(control.name + ".lock").exists())
            finally:
                for child in children:
                    if child.poll() is None:
                        child.kill()
                    child.communicate(timeout=3)
