"""Synthetic-only tests for the separate Muse adapter. No real MUD or tailnet."""
import importlib.util
import json
import http.client
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import secrets
import socket
import sqlite3
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from muse.adapter import Adapter, Journal, LocalBroker, UpstreamError, fixture_server, verify_tailnet


class FakeBroker:
    def __init__(self):
        self.identity = "a" * 32
        self.calls = []
        self.owner = None
        self.fail_send = False
        self.capabilities = ["guarded_send_v1"]
        self.event = {"seq": 1, "kind": "text", "generation": 1, "text": "Red rose",
                      "style_spans": [{"start": 0, "end": 3, "fg": [255, 0, 0], "bold": True}]}

    def configuration(self):
        return 12345, "local-token-is-never-exposed"

    def call(self, config, payload):
        self.calls.append(dict(payload))
        result = {"broker_id": self.identity, "generation": 1, "cursor": 1,
                  "owner": self.owner, "connected": True, "capabilities": self.capabilities}
        if payload["op"] == "claim":
            self.owner = "nua"
            result.update(owner="nua", lease="private-lease-not-for-remote")
        if payload["op"] == "release":
            self.owner = None
            result["owner"] = None
        if payload["op"] in {"observe", "send"}:
            result["events"] = [self.event]
        if payload["op"] == "send":
            if self.fail_send:
                raise UpstreamError("synthetic lost response")
            result["command_complete"] = False
        return result


class AdapterFixture(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="muse-adapter-test-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.token = secrets.token_urlsafe(32)
        self.auth = f"Bearer {self.token}"
        self.credential = self.root / "credential.json"
        self.write_credential()
        self.journal = Journal(self.root / "journal.sqlite")
        self.addCleanup(self.journal.close)
        self.broker = FakeBroker()
        self.adapter = Adapter(self.root / "control.json", self.credential, self.journal, broker=self.broker)

    def write_credential(self, *, enabled=True, token=None):
        self.credential.write_text(json.dumps({"enabled": enabled, "token": token or self.token}))

    def call(self, data, auth=None):
        return self.adapter.process(data, self.auth if auth is None else auth)

    def claim(self):
        code, result = self.call({"op": "status"})
        self.assertEqual(code, 200)
        code, result = self.call({"op": "claim", "expected_adapter_id": self.adapter.adapter_id,
                                 "expected_broker_id": self.broker.identity})
        self.assertEqual(code, 200)
        return result

    def command(self, **changes):
        data = {"op": "send", "expected_adapter_id": self.adapter.adapter_id,
                "expected_broker_id": self.broker.identity, "expected_generation": 1,
                "expected_cursor": 1, "expires_at": time.time() + 20,
                "command_id": secrets.token_hex(16), "command": "look"}
        data.update(changes)
        return data


class AdapterTests(AdapterFixture):
    def test_fixed_owner_secret_stripping_and_color_preservation(self):
        claim = self.claim()
        self.assertNotIn("lease", json.dumps(claim))
        code, observed = self.call({"op": "observe", "after": 0, "expected_broker_id": self.broker.identity})
        self.assertEqual(code, 200)
        self.assertEqual(observed["result"]["events"], [self.broker.event])
        request = self.command()
        code, response = self.call(request)
        self.assertEqual(code, 200)
        sent = self.broker.calls[-1]
        self.assertEqual(sent["owner"], "nua")
        self.assertEqual(sent["lease"], "private-lease-not-for-remote")
        for key in ("expected_broker_id", "expected_generation", "expected_cursor", "expires_at"):
            self.assertEqual(sent[key], request[key])
        self.assertNotIn("local-token", json.dumps(response))
        self.assertNotIn("private-lease", json.dumps(response))
        self.assertFalse(response["receipt"]["command_complete"])
        self.assertEqual(response["result"]["events"], [self.broker.event])

    def test_forbidden_operations_fields_and_malformed_values_never_forward(self):
        self.claim()
        self.broker.calls.clear()
        packets = [{"op": op} for op in ("connect", "close", "stop", "config")]
        packets += [None, [], {"op": []}, {"op": "status", "owner": "human"}]
        packets += [self.command(**{key: value}) for key, value in
                    (("owner", "human"), ("lease", "stolen"), ("takeover", True),
                     ("secret", True), ("host", "example.org"), ("expected_cursor", True),
                     ("expires_at", float("nan")), ("expires_at", float("inf")),
                     ("expires_at", time.time() - 1), ("expires_at", time.time() + 60),
                     ("command", "look\nquit"), ("command_id", "short"))]
        for packet in packets:
            with self.subTest(packet=packet):
                code, _ = self.call(packet)
                self.assertEqual(code, 400)
        self.assertEqual(self.broker.calls, [])

    def test_auth_disable_rotate_and_adapter_restart_invalidate_old_decisions(self):
        self.claim()
        old_decision = self.command()
        self.write_credential(enabled=False)
        self.assertEqual(self.call(old_decision)[0], 403)
        replacement = secrets.token_urlsafe(32)
        self.write_credential(token=replacement)
        self.assertEqual(self.call(old_decision)[0], 403)
        self.assertEqual(self.call(old_decision, f"Bearer {replacement}")[0], 409)
        self.assertIsNone(self.adapter.lease)
        previous_id = self.adapter.adapter_id
        replacement_adapter = Adapter(self.root / "control.json", self.credential, self.journal, broker=self.broker)
        self.assertNotEqual(previous_id, replacement_adapter.adapter_id)
        old_decision["expected_adapter_id"] = previous_id
        self.assertEqual(replacement_adapter.process(old_decision, f"Bearer {replacement}")[0], 409)

    def test_broker_restart_invalidates_lease_and_mutation(self):
        self.claim()
        packet = self.command()
        self.broker.identity = "b" * 32
        code, response = self.call(packet)
        self.assertEqual(code, 409)
        self.assertIsNone(self.adapter.lease)
        self.assertEqual(response["receipt"]["outcome"], "not_forwarded")
        self.assertEqual(sum(p["op"] == "send" for p in self.broker.calls), 0)

    def test_broker_without_guard_capability_fails_closed(self):
        self.broker.capabilities = []
        self.assertEqual(self.call({"op": "status"})[0], 502)

    def test_uncertain_send_is_durable_and_never_replayed(self):
        self.claim()
        self.broker.fail_send = True
        packet = self.command()
        code, response = self.call(packet)
        self.assertEqual(code, 502)
        self.assertEqual(response["receipt"]["outcome"], "uncertain")
        self.assertEqual(self.call(packet)[0], 409)
        second_journal = Journal(self.root / "journal.sqlite")
        try:
            adapter = Adapter(self.root / "control.json", self.credential, second_journal, broker=self.broker)
            packet["expected_adapter_id"] = adapter.adapter_id
            code, response = adapter.process(packet, self.auth)
            self.assertEqual(code, 409)
            self.assertEqual(response["receipt"]["outcome"], "uncertain")
        finally:
            second_journal.close()
        self.assertEqual(sum(p["op"] == "send" for p in self.broker.calls), 1)

    def test_durable_reservation_precedes_forward_and_full_journal_refuses(self):
        self.claim()
        self.journal.capacity = 1
        packet = self.command()
        original_call = self.broker.call

        def verify_reserved(config, data):
            if data["op"] == "send":
                independent = Journal(self.root / "journal.sqlite")
                try:
                    prior = independent.reserve(packet["command_id"], packet)
                    self.assertEqual(prior["outcome"], "uncertain")
                finally:
                    independent.close()
            return original_call(config, data)

        with patch.object(self.broker, "call", side_effect=verify_reserved):
            self.assertEqual(self.call(packet)[0], 200)
        self.assertEqual(self.call(self.command())[0], 503)
        self.assertEqual(sum(p["op"] == "send" for p in self.broker.calls), 1)

    def test_busy_operation_is_not_queued(self):
        self.adapter.lock.acquire()
        try:
            self.assertEqual(self.call({"op": "status"})[0], 409)
        finally:
            self.adapter.lock.release()
        self.assertEqual(self.broker.calls, [])

    def test_failed_journal_reservation_cannot_forward(self):
        self.claim()
        self.broker.calls.clear()
        with patch.object(self.journal, "reserve", side_effect=sqlite3.OperationalError("synthetic disk failure")):
            self.assertEqual(self.call(self.command())[0], 502)
        self.assertEqual(self.broker.calls, [])

    def test_tailnet_requires_verified_self_peer_and_running_backend(self):
        status = {"BackendState": "Running", "Self": {"TailscaleIPs": ["100.90.0.1"]},
                  "Peer": {"peer": {"TailscaleIPs": ["100.90.0.2"]}}}
        self.assertEqual(verify_tailnet(status, "100.90.0.1", "100.90.0.2"),
                         ("100.90.0.1", "100.90.0.2"))
        for own, peer in (("0.0.0.0", "100.90.0.2"), ("127.0.0.1", "100.90.0.2"),
                          ("192.168.1.2", "100.90.0.2"), ("100.90.0.3", "100.90.0.2"),
                          ("100.90.0.1", "100.90.0.3"), ("100.90.0.1", "100.90.0.1")):
            with self.assertRaises(ValueError):
                verify_tailnet(status, own, peer)
        status["BackendState"] = "Stopped"
        with self.assertRaises(ValueError):
            verify_tailnet(status, "100.90.0.1", "100.90.0.2")


class HTTPTests(AdapterFixture):
    def setUp(self):
        super().setUp()
        self.server = fixture_server(self.adapter)
        self.thread = threading.Thread(target=self.server.serve_forever,
                                       kwargs={"poll_interval": .02}, daemon=True)
        self.thread.start()
        self.addCleanup(self.stop_server)

    def stop_server(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(2)

    def http(self, packet, headers=None):
        body = json.dumps(packet).encode()
        actual = {"Authorization": self.auth, "Content-Type": "application/json"}
        actual.update(headers or {})
        connection = http.client.HTTPConnection(*self.server.server_address, timeout=3)
        try:
            connection.request("POST", "/rpc", body=body, headers=actual)
            response = connection.getresponse()
            raw = response.read()
            return response.status, raw
        finally:
            connection.close()

    def test_http_origin_including_empty_and_bad_auth_are_refused(self):
        self.assertEqual(self.http({"op": "status"})[0], 200)
        for origin in ("", "https://example.org"):
            self.assertEqual(self.http({"op": "status"}, {"Origin": origin})[0], 403)
        self.assertEqual(self.http({"op": "status"}, {"Authorization": "Bearer wrong"})[0], 403)
        self.assertEqual(self.http({"op": "status"}, {"Authorization": "Bearer \u00e9"})[0], 403)

    def test_http_oversize_body_and_chunked_are_refused(self):
        self.assertEqual(self.http({"op": "status", "padding": "a" * 8192})[0], 400)
        self.assertEqual(self.http({"op": "status"}, {"Transfer-Encoding": "chunked"})[0], 400)

    def test_http_duplicate_auth_and_headers_budget_are_refused(self):
        with socket.create_connection(self.server.server_address, timeout=3) as client:
            client.sendall((f"POST /rpc HTTP/1.0\r\nAuthorization: {self.auth}\r\n"
                            f"Authorization: {self.auth}\r\nContent-Length: 2\r\n\r\n{{}}").encode())
            self.assertIn(b" 403 ", client.recv(4096).split(b"\r\n", 1)[0])
        code, _ = self.http({"op": "status"}, {"X-Padding": "a" * 9000})
        self.assertEqual(code, 431)

    def test_source_peer_filter_is_enforced_before_http(self):
        self.server.allowed_peer = "127.0.0.2"
        with self.assertRaises((ConnectionError, http.client.RemoteDisconnected)):
            self.http({"op": "status"})

    def test_worker_capacity_rejects_without_creating_an_unbounded_queue(self):
        for _ in range(4):
            self.assertTrue(self.server.slots.acquire(blocking=False))
        try:
            with self.assertRaises((ConnectionError, http.client.RemoteDisconnected)):
                self.http({"op": "status"})
        finally:
            for _ in range(4):
                self.server.slots.release()
        self.assertEqual(self.broker.calls, [])

    def test_duplicate_json_field_is_rejected(self):
        body = b'{"op":"status","op":"claim"}'
        with socket.create_connection(self.server.server_address, timeout=3) as client:
            client.sendall((f"POST /rpc HTTP/1.0\r\nAuthorization: {self.auth}\r\n"
                            f"Content-Type: application/json\r\nContent-Length: {len(body)}\r\n\r\n").encode() + body)
            self.assertIn(b" 400 ", client.recv(4096).split(b"\r\n", 1)[0])
        self.assertEqual(self.broker.calls, [])


class RealBrokerIntegrationTests(unittest.TestCase):
    def test_real_broker_guarded_send_duplicate_and_stale_observation(self):
        root = Path(__file__).resolve().parents[2]
        spec = importlib.util.spec_from_file_location("muse_core_broker_fixture", root / "tests/test_broker.py")
        fixtures = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(fixtures)
        harness = fixtures.BrokerHarness()
        self.addCleanup(harness.close)
        harness.connect()
        harness.call(op="release", owner="human")
        base = Path(harness.temp.name)
        credential = base / "muse-credential.json"
        token = secrets.token_urlsafe(32)
        credential.write_text(json.dumps({"token": token, "enabled": True}))
        journal = Journal(base / "muse-journal.sqlite")
        self.addCleanup(journal.close)
        adapter = Adapter(harness.control, credential, journal)
        server = fixture_server(adapter)
        thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": .02}, daemon=True)
        thread.start()

        def cleanup():
            server.shutdown()
            server.server_close()
            thread.join(2)

        self.addCleanup(cleanup)

        def call(packet):
            connection = http.client.HTTPConnection(*server.server_address, timeout=8)
            try:
                connection.request("POST", "/rpc", body=json.dumps(packet).encode(),
                                   headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"})
                response = connection.getresponse()
                return response.status, json.loads(response.read())
            finally:
                connection.close()

        code, status = call({"op": "status"})
        self.assertEqual(code, 200)
        identity = status["result"]["broker_id"]
        aid = status["adapter_id"]
        self.assertEqual(call({"op": "claim", "expected_adapter_id": aid,
                               "expected_broker_id": identity})[0], 200)
        code, observation = call({"op": "observe", "after": 0, "expected_broker_id": identity})
        self.assertEqual(code, 200)
        command = {"op": "send", "expected_adapter_id": aid, "expected_broker_id": identity,
                   "expected_generation": observation["result"]["generation"],
                   "expected_cursor": observation["result"]["cursor"],
                   "expires_at": time.time() + 20, "command": "look",
                   "command_id": secrets.token_hex(16)}
        code, sent = call(command)
        self.assertEqual(code, 200, sent)
        self.assertFalse(sent["result"]["command_complete"])
        self.assertEqual(call(command)[0], 409)
        stale = {**command, "command_id": secrets.token_hex(16)}
        code, rejected = call(stale)
        self.assertEqual(code, 502)  # Conservative uncertainty on upstream rejection.
        self.assertEqual(rejected["receipt"]["outcome"], "uncertain")
        self.assertEqual(call(stale)[0], 409)
        expires_while_checking_status = {**command, "command_id": secrets.token_hex(16),
                                        "expected_cursor": sent["result"]["cursor"],
                                        "expires_at": time.time() + .05}
        original_call = adapter.broker.call

        def delayed_status(config, payload):
            if payload["op"] == "status":
                time.sleep(.1)
            return original_call(config, payload)

        with patch.object(adapter.broker, "call", side_effect=delayed_status):
            code, expired = call(expires_while_checking_status)
        self.assertEqual(code, 502)
        self.assertEqual(expired["receipt"]["outcome"], "uncertain")
        self.assertEqual(harness.fake.commands, ["look"])
        release = {"op": "release", "expected_adapter_id": aid, "expected_broker_id": identity}
        self.assertEqual(call(release)[0], 200)


class UpstreamDeadlineTests(unittest.TestCase):
    def test_slow_drip_cannot_extend_absolute_upstream_deadline(self):
        class SlowHandler(BaseHTTPRequestHandler):
            def log_message(self, *_):
                pass

            def do_POST(self):
                self.rfile.read(int(self.headers["Content-Length"]))
                body = b'{"padding":"' + b"x" * 100 + b'"}'
                self.send_response(200)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                try:
                    for byte in body:
                        self.wfile.write(bytes([byte]))
                        self.wfile.flush()
                        time.sleep(.025)  # Each byte arrives before the idle timeout.
                except OSError:
                    pass

        server = ThreadingHTTPServer(("127.0.0.1", 0), SlowHandler)
        server.daemon_threads = True
        worker = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": .02}, daemon=True)
        worker.start()
        try:
            before = time.monotonic()
            with patch("muse.adapter.UPSTREAM_TIMEOUT", .15):
                with self.assertRaises(UpstreamError):
                    LocalBroker("unused-fixture-path").call(
                        (server.server_port, "a" * 64), {"op": "status"})
            self.assertLess(time.monotonic() - before, 1)
        finally:
            server.shutdown()
            server.server_close()
            worker.join(2)


if __name__ == "__main__":
    unittest.main()
