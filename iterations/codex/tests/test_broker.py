"""Integration tests: real local broker subprocess, synthetic loopback MUD only."""
from __future__ import annotations

import json
import os
from pathlib import Path
import socketserver
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request

from mudgpt.broker import request


WORKSPACE = Path(__file__).resolve().parents[1]


class _FakeHandler(socketserver.BaseRequestHandler):
    def handle(self):
        self.server.connection_open.set()
        self.request.sendall(b"Synthetic fixture room\r\n> ")
        state, line = "text", bytearray()
        try:
            while data := self.request.recv(4096):
                for byte in data:
                    if state == "iac":
                        state = "option" if byte in (251, 252, 253, 254) else "sub" if byte == 250 else "text"
                        continue
                    if state == "option":
                        state = "text"
                        continue
                    if state == "sub":
                        if byte == 255:
                            state = "sub_iac"
                        continue
                    if state == "sub_iac":
                        state = "text" if byte == 240 else "sub"
                        continue
                    if byte == 255:
                        state = "iac"
                    elif byte == 10:
                        command = bytes(line).rstrip(b"\r").decode("utf-8")
                        line.clear()
                        with self.server.command_lock:
                            self.server.commands.append(command)
                        if command == "silent":
                            self.server.silent_received.set()
                            continue
                        if command == "drop":
                            return
                        if command.startswith("synthetic-secret-"):
                            encoded = command.encode("utf-8")
                            self.request.sendall(encoded[:8])
                            time.sleep(.02)
                            self.request.sendall(encoded[8:] + b"\r\n> ")
                        else:
                            self.request.sendall(("Fixture response: " + command + "\r\n> ").encode("utf-8"))
                    else:
                        line.append(byte)
        except (ConnectionError, OSError):
            pass
        finally:
            self.server.connection_closed.set()


class FakeMud(socketserver.ThreadingTCPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self):
        super().__init__(("127.0.0.1", 0), _FakeHandler)
        self.commands = []
        self.command_lock = threading.Lock()
        self.connection_open = threading.Event()
        self.connection_closed = threading.Event()
        self.silent_received = threading.Event()
        self.thread = threading.Thread(target=self.serve_forever, kwargs={"poll_interval": .05}, daemon=True)
        self.thread.start()

    def close(self):
        self.shutdown()
        self.server_close()
        self.thread.join(2)


class BrokerHarness:
    """Owns only a test-created TemporaryDirectory and its subprocess."""
    def __init__(self):
        self.temp = tempfile.TemporaryDirectory(prefix="mudgpt-broker-test-")
        self.control = Path(self.temp.name) / "control.json"
        self.env = os.environ.copy()
        self.env.pop("MUDGPT_CONTROL", None)
        self.env["PYTHONPATH"] = str(WORKSPACE / "src")
        self.env["PYTHONUNBUFFERED"] = "1"
        self.fake = FakeMud()
        self.process = subprocess.Popen(
            [sys.executable, "-m", "mudgpt", "--control", str(self.control), "serve"],
            cwd=WORKSPACE, env=self.env, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, encoding="utf-8",
        )
        deadline = time.monotonic() + 8
        try:
            while time.monotonic() < deadline:
                if self.process.poll() is not None:
                    out, err = self.process.communicate()
                    raise AssertionError(f"fixture broker exited: {out}\n{err}")
                if self.control.exists():
                    try:
                        self.call(op="status")
                        break
                    except (OSError, ValueError, RuntimeError):
                        pass
                time.sleep(.02)
            else:
                raise AssertionError("fixture broker did not become ready")
        except BaseException:
            self.close()
            raise

    def call(self, **payload):
        return request(payload, self.control)

    def connect(self):
        return self.call(op="connect", owner="human", host="127.0.0.1", port=self.fake.server_address[1])

    def cli(self, *args, input=None):
        return subprocess.run(
            [sys.executable, "-m", "mudgpt", "--control", str(self.control), *args],
            cwd=WORKSPACE, env=self.env, input=input, capture_output=True,
            text=True, encoding="utf-8", timeout=12,
        )

    def close(self):
        if self.process.poll() is None:
            try:
                self.call(op="claim", owner="human", takeover=True)
                self.call(op="stop", owner="human")
            except (OSError, ValueError, RuntimeError):
                pass
            try:
                self.process.communicate(timeout=5)
            except subprocess.TimeoutExpired:
                self.process.terminate()
                self.process.communicate(timeout=5)
        else:
            self.process.communicate()
        self.fake.close()
        self.temp.cleanup()


class BrokerIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.h = BrokerHarness()
        self.addCleanup(self.h.close)

    def test_cli_secret_stdin_is_redacted_and_not_in_subsequent_observations(self):
        self.h.connect()
        secret = "synthetic-secret-9347"
        result = self.h.cli("send", "--stdin", "--secret", "--timeout", "1", input=secret + "\n")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn(secret, result.stdout + result.stderr)
        self.assertNotIn(secret, json.dumps(self.h.call(op="observe", after=0)))
        self.assertEqual(self.h.fake.commands, [secret])
        self.assertIn("[REDACTED]", result.stdout)

    def test_cli_rejects_secret_argument_and_multiline_without_sending(self):
        self.h.connect()
        result = self.h.cli("send", "synthetic-secret-argument", "--secret")
        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn("synthetic-secret-argument", result.stdout + result.stderr)
        bad = self.h.cli("send", "--stdin", input="look\nnorth\n")
        self.assertNotEqual(bad.returncode, 0)
        self.assertEqual(self.h.fake.commands, [])

    def test_two_observers_have_independent_cursors_and_wrong_owner_cannot_send(self):
        self.h.connect()
        self.h.call(op="release", owner="human")
        lease = self.h.call(op="claim", owner="lily")["lease"]
        with self.assertRaises(RuntimeError):
            self.h.call(op="send", command="north", owner="other")
        with self.assertRaises(RuntimeError):
            self.h.call(op="claim", owner="other")
        result = self.h.call(op="send", command="look", owner="lily", lease=lease, timeout=1, quiet=.05)
        first = self.h.call(op="observe", after=0)
        second = self.h.call(op="observe", after=0)
        self.assertEqual(first, second)
        self.assertIn("Fixture response: look", json.dumps(first))
        self.assertFalse(result["command_complete"])
        self.assertEqual(self.h.fake.commands, ["look"])

    def test_human_takeover_interrupts_wait_and_does_not_replay_command(self):
        self.h.connect()
        self.h.call(op="release", owner="human")
        lease = self.h.call(op="claim", owner="lily")["lease"]
        result = {}
        def send():
            try:
                result["value"] = self.h.call(op="send", command="silent", owner="lily", lease=lease, timeout=5)
            except Exception as error:
                result["error"] = error
        thread = threading.Thread(target=send, daemon=True)
        thread.start()
        self.assertTrue(self.h.fake.silent_received.wait(3))
        self.h.call(op="claim", owner="human", takeover=True)
        thread.join(2)
        self.assertFalse(thread.is_alive(), "takeover left the old command waiter blocked")
        self.assertNotIn("error", result)
        self.assertEqual(result["value"]["reason"], "ownership_changed")
        self.assertEqual(self.h.fake.commands, ["silent"])

    def test_same_named_controller_old_lease_cannot_mutate_reclaimed_session(self):
        self.h.connect()
        self.h.call(op="release", owner="human")
        old = self.h.call(op="claim", owner="lily")["lease"]
        self.h.call(op="release", owner="lily", lease=old)
        current = self.h.call(op="claim", owner="lily")["lease"]
        self.assertNotEqual(old, current)
        for operation in ("send", "close", "stop", "release", "claim"):
            with self.assertRaisesRegex(RuntimeError, "lease"):
                self.h.call(op=operation, owner="lily", lease=old, command="north")
        self.assertTrue(self.h.call(op="status")["connected"])
        self.assertEqual(self.h.fake.commands, [])
        sent = self.h.call(op="send", owner="lily", lease=current, command="look", timeout=1, quiet=.05)
        self.assertEqual(sent["delivery"], "transport_accepted")
        self.assertEqual(self.h.fake.commands, ["look"])

    def test_http_requires_token_and_rejects_browser_origin(self):
        config = json.loads(self.h.control.read_text())
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        for headers in ({}, {"Authorization": f"Bearer {config['token']}", "Origin": "https://untrusted.invalid"}):
            req = urllib.request.Request(f"http://127.0.0.1:{config['port']}/rpc", b'{"op":"status"}', headers, method="POST")
            with self.assertRaises(urllib.error.HTTPError) as caught:
                opener.open(req, timeout=2)
            self.assertEqual(caught.exception.code, 403)
            caught.exception.close()

    def test_stop_closes_socket_exits_and_removes_own_control_file(self):
        self.h.connect()
        self.h.call(op="stop", owner="human")
        out, err = self.h.process.communicate(timeout=5)
        self.assertEqual(self.h.process.returncode, 0, err)
        self.assertTrue(self.h.fake.connection_closed.wait(2))
        self.assertFalse(self.h.control.exists())
        self.assertNotIn("Traceback", out + err)

    def test_second_broker_refuses_live_control_file_without_replacing_it(self):
        before = self.h.control.read_bytes()
        result = self.h.cli("serve")
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.h.control.read_bytes(), before)
        self.assertEqual(self.h.process.poll(), None)

