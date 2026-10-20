"""Offline tests: generated disposable credentials and fake sockets only."""
from contextlib import redirect_stdout, redirect_stderr
from dataclasses import replace
import hashlib
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from muse import call
from muse import credentials


class FakeSocket:
    def __init__(self, wire):
        self.wire = wire
        self.sent = []
        self.closed = False

    def sendall(self, value):
        self.sent.append(value)

    def recv(self, _):
        wire, self.wire = self.wire, b""
        return wire

    def settimeout(self, value):
        if value <= 0:
            raise AssertionError("timeout must stay positive")

    def close(self):
        self.closed = True


class HelperTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.key = rsa.generate_private_key(public_exponent=65537, key_size=3072)

    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix="muse-public-helper-test-")
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        # Documentation-only test pins within the supported range; never dialled.
        self.config = call.Config("http://100.64.0.1:8811/rpc", "100.64.0.1", 8811,
                                  "100.64.0.2", self.root / "key.pem",
                                  self.root / "envelope.json", "0" * 64)
        self.token = "fixture_disposable_not_a_real_credential_" + "x" * 32

    def write_envelope(self, **changes):
        private = self.key.private_bytes(serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8, serialization.NoEncryption())
        credentials.write_exclusive(self.config.key_path, private)
        pubpath = self.root / "public.pem"
        pubpath.write_bytes(self.key.public_key().public_bytes(serialization.Encoding.PEM,
                            serialization.PublicFormat.SubjectPublicKeyInfo))
        credpath = self.root / "credential.json"
        credpath.write_text(json.dumps({"token": self.token, "enabled": True}))
        result = credentials.encrypt_credential(pubpath, credentials.fingerprint(self.key.public_key()),
            credpath, self.config.envelope_path, self.config.endpoint, self.config.peer_ip, "offline fixture")
        raw = self.config.envelope_path.read_bytes()
        if changes:
            obj = json.loads(raw)
            obj.update(changes)
            raw = json.dumps(obj).encode()
            self.config.envelope_path.write_bytes(raw)
        self.config = replace(self.config, envelope_sha256=hashlib.sha256(raw).hexdigest())
        return raw, result

    def config_file(self):
        path = self.root / "config.json"
        path.write_text(json.dumps({"format": call.CONFIG_FORMAT,
            "endpoint": self.config.endpoint, "peer_ip": self.config.peer_ip,
            "key_path": "key.pem", "envelope_path": "envelope.json",
            "envelope_sha256": self.config.envelope_sha256}))
        return path

    def invoke(self, extra=None):
        request = self.root / "request.json"
        request.write_bytes(b'{ "op" : "status" }\n')
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = call.main(["--config", str(self.config_file()), "--request", str(request)] + (extra or []))
        return code, out.getvalue(), err.getvalue()

    def test_config_pins_relative_paths_and_has_no_network_side_effect(self):
        with patch.object(call.socket, "create_connection") as connect:
            config = call.load_config(self.config_file())
        self.assertEqual(config, self.config)
        connect.assert_not_called()

    def test_config_rejects_dns_non_tailnet_credentials_and_url_ambiguity(self):
        for endpoint in ("https://100.64.0.1:8811/rpc", "http://localhost:8811/rpc",
            "http://127.0.0.1:8811/rpc", "http://192.0.2.10:8811/rpc", "http://100.63.0.1:8811/rpc",
            "http://100.128.0.1:8811/rpc", "http://u:p@100.64.0.1:8811/rpc",
            "http://100.64.0.1:8811/rpc?next=x", "http://100.64.0.1:8811/rpc#x",
            "http://100.64.0.1:08811/rpc", "http://100.64.0.1/rpc", "http://[::1]:8811/rpc"):
            with self.subTest(endpoint=endpoint), self.assertRaises(call.Fail):
                call.validate_destination(endpoint, self.config.peer_ip)
        with self.assertRaises(call.Fail):
            call.validate_destination(self.config.endpoint, self.config.host)

    def test_duplicate_or_extra_config_fields_are_refused(self):
        path = self.config_file()
        base = path.read_bytes()
        for ending in (b',"endpoint":"http://100.64.0.1:8811/rpc"}', b',"fallback":"https://example.org"}'):
            path.write_bytes(base[:-1] + ending)
            with self.assertRaises(call.Fail):
                call.load_config(path)

    def test_envelope_round_trip_hash_recipient_and_destination_pins(self):
        raw, receipt = self.write_envelope()
        self.assertNotIn(self.token, raw.decode())
        self.assertFalse(receipt["transmitted"])
        self.assertEqual(call.decrypt_token(raw, self.config, self.key), self.token)
        for config in (replace(self.config, envelope_sha256="f" * 64),
                       replace(self.config, endpoint="http://100.64.0.3:8811/rpc"),
                       replace(self.config, peer_ip="100.64.0.4")):
            with self.assertRaises(call.Fail) as raised:
                call.decrypt_token(raw, config, self.key)
            self.assertEqual(raised.exception.outcome, "not_sent")
        obj = json.loads(raw)
        obj["recipient_spki_sha256"] = "f" * 64
        altered = json.dumps(obj).encode()
        altered_config = replace(self.config, envelope_sha256=hashlib.sha256(altered).hexdigest())
        with self.assertRaises(call.Fail):
            call.decrypt_token(altered, altered_config, self.key)

    def test_validate_only_never_opens_socket_or_prints_token(self):
        self.write_envelope()
        with patch.object(call.socket, "create_connection") as connect:
            code, out, err = self.invoke(["--validate-only"])
        connect.assert_not_called()
        self.assertEqual(code, 0, err)
        self.assertEqual(json.loads(out)["outcome"], "not_sent")
        self.assertNotIn(self.token, out + err)

    def test_request_is_exact_bytes_and_duplicate_field_refused(self):
        path = self.root / "request.json"
        body = b'{ "op": "status" }\n'
        path.write_bytes(body)
        self.assertEqual(call.load_request(path), body)
        path.write_bytes(b'{"op":"status","op":"send"}')
        with self.assertRaises(call.Fail):
            call.load_request(path)

    def exchange(self, wire, body=b'{ "op":"status" }\n'):
        fake = FakeSocket(wire)
        with patch.object(call, "tunnel_proxy_parts", return_value=("fixture-proxy", 3130, "fixture-basic")), \
             patch.object(call.socket, "create_connection", return_value=fake) as connect:
            result = call.exchange(body, self.token, self.config)
        connect.assert_called_once()
        return result, fake

    def test_one_connect_one_exact_inner_request_and_no_inner_redirect_follow(self):
        body = b'{ "op":"status" }\n'
        payload = b'{"location":"https://example.org"}'
        wire = b"HTTP/1.1 200 Connection established\r\n\r\nHTTP/1.0 302 Found\r\nContent-Length: " + str(len(payload)).encode() + b"\r\n\r\n" + payload
        result, fake = self.exchange(wire, body)
        self.assertEqual(result, (302, payload))
        self.assertEqual(len(fake.sent), 2)
        self.assertNotIn(self.token.encode(), fake.sent[0])
        self.assertIn(b"Proxy-Authorization:", fake.sent[0])
        self.assertNotIn(b"Proxy-Authorization:", fake.sent[1])
        self.assertTrue(fake.sent[1].endswith(body))
        self.assertTrue(fake.closed)

    def test_outer_refusal_never_sends_bearer_and_never_retries(self):
        fake = FakeSocket(b"HTTP/1.1 302 Found\r\nLocation: http://elsewhere.invalid\r\n\r\n")
        with patch.object(call, "tunnel_proxy_parts", return_value=("fixture", 3130, None)), \
             patch.object(call.socket, "create_connection", return_value=fake) as connect:
            with self.assertRaises(call.Fail) as raised:
                call.exchange(b'{"op":"status"}', self.token, self.config)
        self.assertEqual(raised.exception.outcome, "not_sent")
        connect.assert_called_once()
        self.assertEqual(len(fake.sent), 1)
        self.assertNotIn(self.token.encode(), fake.sent[0])

    def test_incomplete_inner_reply_is_uncertain_and_one_attempt(self):
        with self.assertRaises(call.Fail) as raised:
            self.exchange(b"HTTP/1.1 200 OK\r\n\r\nHTTP/1.0 200 OK\r\nContent-Length: 999\r\n\r\n{}")
        self.assertEqual(raised.exception.outcome, "uncertain")

    def test_ambiguous_framing_and_oversize_response_are_refused(self):
        for head in (b"Content-Length: 2\r\nContent-Length: 2", b"Transfer-Encoding: chunked",
                     b"Content-Length: -1", b"Content-Length: 1048577"):
            with self.subTest(head=head), self.assertRaises(call.Fail):
                call.parse_inner_head(b"HTTP/1.0 200 OK\r\n" + head + b"\r\n\r\n")

    def test_token_echo_suppressed_even_when_json_escaped(self):
        self.write_envelope()
        escaped = "".join("\\u%04x" % ord(c) for c in self.token)
        for payload in (json.dumps({"message": self.token}).encode(), ('{"message":"' + escaped + '"}').encode()):
            with patch.object(call, "exchange", return_value=(200, payload)):
                code, out, err = self.invoke()
            self.assertEqual(code, 6)
            self.assertEqual(out, "")
            self.assertNotIn(self.token, err)
            self.assertEqual(json.loads(err)["outcome"], "uncertain")

    def test_color_spans_survive_and_capability_keys_are_removed(self):
        self.write_envelope()
        value = {"events": [{"text": "Red rose", "style_spans": [{"start": 0, "end": 3, "fg": [255, 0, 0]}]}],
                 "token": "discard", "nested": {"lease": "discard", "Authorization": "discard"}}
        with patch.object(call, "exchange", return_value=(200, json.dumps(value).encode())):
            code, out, err = self.invoke()
        self.assertEqual(code, 0, err)
        filtered = json.loads(out)["result"]
        self.assertEqual(filtered["events"], value["events"])
        self.assertEqual(filtered["nested"], {})
        self.assertNotIn("discard", out)

    def test_keygen_is_exclusive_and_outputs_only_public_fingerprint(self):
        private, public = self.root / "generated.pem", self.root / "generated.pub.pem"
        receipt = credentials.generate_key(private, public)
        self.assertFalse(receipt["private_key_printed"])
        self.assertFalse(receipt["transmitted"])
        self.assertEqual(call.load_private_key(private).key_size, 3072)
        before = private.read_bytes()
        with self.assertRaises(ValueError):
            credentials.generate_key(private, public)
        self.assertEqual(private.read_bytes(), before)
        if os.name != "nt":
            self.assertEqual(private.stat().st_mode & 0o777, 0o600)

    def test_proxy_requires_documented_transport_and_never_returns_env_to_output(self):
        with patch.dict(os.environ, {"HTTPS_PROXY": "https://proxy.example.invalid:443"}):
            with self.assertRaises(call.Fail) as raised:
                call.tunnel_proxy_parts()
            self.assertNotIn("proxy.example", str(raised.exception))
        with patch.dict(os.environ, {"HTTPS_PROXY": "http://u%40name:p%3Ass@proxy.example.invalid:8080"}):
            host, port, auth = call.tunnel_proxy_parts()
        self.assertEqual((host, port), ("proxy.example.invalid", 3130))
        self.assertEqual(auth, "dUBuYW1lOnA6c3M=")


if __name__ == "__main__":
    unittest.main()
