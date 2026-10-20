"""Scoped Nua HTTP adapter: local broker secrets never leave this process.

Only the production CLI verifies and binds a Tailscale endpoint. Tests use the
explicit loopback fixture constructor. Nothing enrolls a device or logs in a MUD.
"""
from __future__ import annotations

import argparse
import hashlib
import hmac
import http.client
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import ipaddress
import json
import math
import os
from pathlib import Path
import re
import secrets
import socket
import sqlite3
import subprocess
import threading
import time
import unicodedata

TAILNET = ipaddress.ip_network("100.64.0.0/10")
MAX_BODY = 8192
MAX_REPLY = 1024 * 1024
MAX_TTL = 30.0
UPSTREAM_TIMEOUT = 8.0
ID = re.compile(r"[A-Za-z0-9_-]{16,96}\Z")


class Rejected(Exception):
    def __init__(self, message, code=400):
        super().__init__(message)
        self.code = code


class UpstreamError(Exception):
    """An upstream transport/protocol failure; never assume a mutation failed."""


def read_json(path, limit=4096):
    with Path(path).open("rb") as stream:
        data = stream.read(limit + 1)
    if len(data) > limit:
        raise ValueError("local configuration is too large")
    parsed = json.loads(data)
    if not isinstance(parsed, dict):
        raise ValueError("local configuration must be an object")
    return parsed


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON field")
        result[key] = value
    return result


class LocalBroker:
    """Fixed local control path; never accepts a remote host, URL or credential."""
    def __init__(self, control):
        self.control = Path(control).resolve()

    def configuration(self):
        config = read_json(self.control)
        port, token = config.get("port"), config.get("token")
        if type(port) is not int or not 1 <= port <= 65535:
            raise ValueError("invalid local port")
        if not isinstance(token, str) or not re.fullmatch(r"[a-f0-9]{64}", token):
            raise ValueError("invalid local token")
        return port, token

    def call(self, config, payload):
        port, token = config
        connection = http.client.HTTPConnection("127.0.0.1", port, timeout=UPSTREAM_TIMEOUT)
        # An expiry-triggered close must not make http.client reopen the socket.
        connection.auto_open = 0
        sockets = []
        deadline = time.monotonic() + UPSTREAM_TIMEOUT

        def expire():
            for active in sockets:
                try:
                    active.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass
            connection.close()

        timer = threading.Timer(UPSTREAM_TIMEOUT, expire)
        timer.daemon = True
        timer.start()
        try:
            connection.connect()
            sockets.append(connection.sock)
            if time.monotonic() >= deadline:
                raise UpstreamError("broker operation deadline expired")
            connection.request("POST", "/rpc", body=json.dumps(payload).encode("utf-8"),
                               headers={"Content-Type": "application/json",
                                        "Authorization": f"Bearer {token}"})
            response = connection.getresponse()
            raw = response.read(MAX_REPLY + 1)
            if len(raw) > MAX_REPLY:
                raise UpstreamError("broker response exceeded the limit")
            result = json.loads(raw)
            if response.status != 200 or not isinstance(result, dict) or "error" in result:
                # Do not reflect arbitrary broker exception strings or credentials.
                raise UpstreamError("broker rejected or did not complete the request")
            return result
        except (OSError, http.client.HTTPException, ValueError) as exc:
            raise UpstreamError("broker transport or protocol failed") from exc
        finally:
            timer.cancel()
            connection.close()


class Journal:
    """Bounded, durable replay barrier. Never evicts a used command ID."""
    def __init__(self, path, capacity=10000):
        if type(capacity) is not int or not 1 <= capacity <= 100000:
            raise ValueError("invalid journal capacity")
        self.capacity = capacity
        path = Path(path).resolve()
        path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path, timeout=1, check_same_thread=False)
        os.chmod(path, 0o600)
        self.db.execute("PRAGMA journal_mode=DELETE")
        self.db.execute("PRAGMA synchronous=FULL")
        self.db.execute("CREATE TABLE IF NOT EXISTS commands ("
                        "id TEXT PRIMARY KEY, digest TEXT NOT NULL, "
                        "created REAL NOT NULL, receipt TEXT NOT NULL)")
        self.db.commit()

    def reserve(self, command_id, payload):
        old = self.db.execute("SELECT receipt FROM commands WHERE id=?", (command_id,)).fetchone()
        if old:
            return json.loads(old[0])
        if self.db.execute("SELECT count(*) FROM commands").fetchone()[0] >= self.capacity:
            raise Rejected("journal capacity reached; local maintenance required", 503)
        receipt = {"command_id": command_id, "outcome": "uncertain",
                   "reason": "reserved before forwarding; inspect observations",
                   "command_complete": False}
        digest = hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()
        self.db.execute("INSERT INTO commands VALUES (?, ?, ?, ?)",
                        (command_id, digest, time.time(), json.dumps(receipt)))
        self.db.commit()  # FULL synchronous commit MUST precede the upstream send.
        return None

    def finish(self, command_id, receipt):
        self.db.execute("UPDATE commands SET receipt=? WHERE id=?",
                        (json.dumps(receipt), command_id))
        self.db.commit()

    def close(self):
        self.db.close()


def without_secrets(value):
    """Keep text and style spans intact while removing transport capabilities."""
    if isinstance(value, dict):
        return {key: without_secrets(item) for key, item in value.items()
                if key not in {"token", "lease"}}
    if isinstance(value, list):
        return [without_secrets(item) for item in value]
    return value


class Adapter:
    FIELDS = {
        "status": {"op"},
        "observe": {"op", "after", "expected_broker_id"},
        "claim": {"op", "expected_adapter_id", "expected_broker_id"},
        "release": {"op", "expected_adapter_id", "expected_broker_id"},
        "send": {"op", "expected_adapter_id", "command_id", "command",
                 "expected_broker_id", "expected_generation", "expected_cursor", "expires_at"},
    }

    def __init__(self, control, credential_file, journal, *, broker=None):
        self.broker = broker or LocalBroker(control)
        self.credential_file = Path(credential_file).resolve()
        self.journal = journal
        self.lock = threading.Lock()
        self.adapter_id = secrets.token_hex(16)
        self.broker_id = None
        self.lease = None
        self.credential_version = None

    def authenticate(self, authorization):
        try:
            value = read_json(self.credential_file)
            token = value.get("token")
            if value.get("enabled") is not True or not isinstance(token, str):
                return None
            if not re.fullmatch(r"[A-Za-z0-9_-]{43,128}", token):
                return None
            if not isinstance(authorization, str) or not hmac.compare_digest(
                    authorization.encode("utf-8"), f"Bearer {token}".encode("ascii")):
                return None
            return hashlib.sha256(token.encode()).hexdigest()
        except (OSError, ValueError):
            return None

    @staticmethod
    def validate(data):
        if not isinstance(data, dict) or not isinstance(data.get("op"), str) or data["op"] not in Adapter.FIELDS:
            raise Rejected("unsupported operation")
        op = data["op"]
        if set(data) != Adapter.FIELDS[op]:
            raise Rejected("request fields must exactly match the scoped API")
        if op != "status" and (not isinstance(data["expected_broker_id"], str)
                               or not ID.fullmatch(data["expected_broker_id"])):
            raise Rejected("invalid expected_broker_id")
        if op in {"claim", "send", "release"}:
            if not isinstance(data["expected_adapter_id"], str) or not ID.fullmatch(data["expected_adapter_id"]):
                raise Rejected("invalid expected_adapter_id")
        if op == "observe" and (type(data["after"]) is not int or data["after"] < 0):
            raise Rejected("after must be a nonnegative integer")
        if op == "send":
            if not isinstance(data["command_id"], str) or not ID.fullmatch(data["command_id"]):
                raise Rejected("command_id must be 16..96 URL-safe characters")
            command = data["command"]
            if not isinstance(command, str) or len(command) > 500:
                raise Rejected("command must be a string of at most 500 characters")
            if any(unicodedata.category(c).startswith("C") or c in "\u2028\u2029" for c in command):
                raise Rejected("command cannot contain control characters or line separators")
            for field in ("expected_generation", "expected_cursor"):
                if type(data[field]) is not int or data[field] < 0:
                    raise Rejected(f"{field} must be a nonnegative integer")
            expiry = data["expires_at"]
            if type(expiry) not in (int, float) or not math.isfinite(expiry):
                raise Rejected("expires_at must be finite Unix seconds")
            remaining = expiry - time.time()
            if not 0 < remaining <= MAX_TTL:
                raise Rejected("expires_at must be in the next 30 seconds")
        return op

    def process(self, data, authorization):
        if not self.lock.acquire(blocking=False):
            return 409, {"error": "adapter busy; no command was queued"}
        command_id = None
        try:
            version = self.authenticate(authorization)
            if version is None:
                raise Rejected("scoped authentication required", 403)
            if self.credential_version is not None and version != self.credential_version:
                self.lease = None
                self.adapter_id = secrets.token_hex(16)
            self.credential_version = version
            op = self.validate(data)
            if op in {"claim", "release", "send"} and data["expected_adapter_id"] != self.adapter_id:
                raise Rejected("adapter lifetime changed; obtain status and reclaim locally if needed", 409)
            if op == "send":
                command_id = data["command_id"]
                old = self.journal.reserve(command_id, data)
                if old is not None:
                    return 409, {"error": "duplicate command_id; nothing replayed", "receipt": old}
            config = self.broker.configuration()
            status = self.broker.call(config, {"op": "status"})
            capabilities = status.get("capabilities")
            if not isinstance(capabilities, list) or "guarded_send_v1" not in capabilities:
                raise UpstreamError("broker does not advertise atomic guarded sends")
            identity = status.get("broker_id")
            if not isinstance(identity, str) or not ID.fullmatch(identity):
                raise UpstreamError("broker lacks a valid lifetime identity")
            if self.broker_id is not None and identity != self.broker_id:
                self.lease = None
                self.adapter_id = secrets.token_hex(16)
            self.broker_id = identity
            if op != "status" and data["expected_broker_id"] != identity:
                raise Rejected("broker lifetime changed; nothing sent; obtain fresh status", 409)
            if op in {"claim", "release", "send"} and data["expected_adapter_id"] != self.adapter_id:
                raise Rejected("adapter lifetime changed; nothing sent; obtain fresh status", 409)
            if op == "status":
                result = status
            elif op == "observe":
                result = self.broker.call(config, {"op": "observe", "after": data["after"],
                                                   "expected_broker_id": identity})
            elif op == "claim":
                payload = {"op": "claim", "owner": "nua", "takeover": False}
                if self.lease is not None:
                    payload["lease"] = self.lease
                result = self.broker.call(config, payload)
                if result.get("broker_id") != identity or not isinstance(result.get("lease"), str):
                    raise UpstreamError("claim response did not identify the expected broker and lease")
                self.lease = result["lease"]
            else:
                if self.lease is None:
                    raise Rejected("no current local Nua lease; claim first", 409)
                if op == "release":
                    try:
                        result = self.broker.call(config, {"op": "release", "owner": "nua", "lease": self.lease})
                    finally:
                        self.lease = None
                else:
                    # These guards are ALSO checked atomically by the broker,
                    # after any concurrent output and before writer.write().
                    result = self.broker.call(config, {
                        "op": "send", "owner": "nua", "lease": self.lease,
                        "command": data["command"], "timeout": 3, "quiet": 0.3,
                        **{field: data[field] for field in
                           ("expected_broker_id", "expected_generation", "expected_cursor", "expires_at")},
                    })
            if result.get("broker_id") != identity:
                self.lease = None
                raise UpstreamError("broker lifetime changed during the operation")
            response = {"adapter_id": self.adapter_id, "result": without_secrets(result)}
            if command_id:
                receipt = {"command_id": command_id, "outcome": "broker_response",
                           "broker_id": identity, "cursor": result.get("cursor"),
                           "generation": result.get("generation"), "command_complete": False}
                self.journal.finish(command_id, receipt)
                response["receipt"] = receipt
            return 200, response
        except Rejected as exc:
            response = {"error": str(exc), "adapter_id": self.adapter_id}
            if command_id:
                receipt = {"command_id": command_id, "outcome": "not_forwarded",
                           "reason": str(exc), "command_complete": False}
                try:
                    self.journal.finish(command_id, receipt)
                except sqlite3.Error:
                    pass  # The earlier durable reservation still prevents replay.
                response["receipt"] = receipt
            return exc.code, response
        except (OSError, ValueError, UpstreamError, sqlite3.Error):
            response = {"error": "local broker or journal unavailable; inspect state before a new decision",
                        "adapter_id": self.adapter_id}
            if command_id:
                receipt = {"command_id": command_id, "outcome": "uncertain",
                           "reason": "no automatic retry; inspect fresh observations",
                           "command_complete": False}
                try:
                    self.journal.finish(command_id, receipt)
                except sqlite3.Error:
                    pass
                response["receipt"] = receipt
            return 502, response
        finally:
            self.lock.release()


class _HeaderLimit:
    def __init__(self, stream):
        self.stream, self.remaining = stream, 8192

    def readline(self, limit=-1):
        value = self.stream.readline(min(self.remaining + 1, limit) if limit >= 0 else self.remaining + 1)
        self.remaining -= len(value)
        if self.remaining < 0:
            raise http.client.HTTPException("header size limit")
        return value


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.0"  # One request per connection, no pipelined queue.

    def setup(self):
        super().setup()
        self.connection.settimeout(3)
        self.deadline = threading.Timer(25, self._expire)
        self.deadline.daemon = True
        self.deadline.start()

    def _expire(self):
        try:
            self.connection.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass

    def finish(self):
        self.deadline.cancel()
        super().finish()

    def log_message(self, *_):
        pass

    def parse_request(self):
        original = self.rfile
        self.rfile = _HeaderLimit(original)
        try:
            return super().parse_request()
        finally:
            self.rfile = original

    def do_POST(self):
        if self.path != "/rpc":
            return self.reply(404, {"error": "not found"})
        if self.headers.get_all("Origin") is not None:
            return self.reply(403, {"error": "browser Origin requests are refused"})
        auth = self.headers.get_all("Authorization", [])
        if len(auth) != 1 or self.server.adapter.authenticate(auth[0]) is None:
            return self.reply(403, {"error": "scoped authentication required"})
        try:
            lengths = self.headers.get_all("Content-Length", [])
            if len(lengths) != 1 or not lengths[0].isdigit() or self.headers.get_all("Transfer-Encoding"):
                raise Rejected("one bounded Content-Length is required")
            size = int(lengths[0])
            if not 0 < size <= MAX_BODY:
                raise Rejected("request must be 1..8192 bytes")
            if self.headers.get("Content-Type") != "application/json":
                raise Rejected("Content-Type must be application/json")
            raw = self.rfile.read(size)
            if len(raw) != size:
                raise Rejected("incomplete body")
            data = json.loads(raw, object_pairs_hook=unique_object)
            code, result = self.server.adapter.process(data, auth[0])
        except (ValueError, Rejected) as exc:
            code, result = 400, {"error": str(exc) if isinstance(exc, Rejected) else "invalid JSON"}
        except OSError:
            code, result = 408, {"error": "request read timed out"}
        self.reply(code, result)

    def reply(self, code, value):
        encoded = json.dumps(value, ensure_ascii=True, allow_nan=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(encoded)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("Connection", "close")
        self.end_headers()
        try:
            self.wfile.write(encoded)
        except OSError:
            pass


class ScopedServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = False
    request_queue_size = 4

    def __init__(self, address, adapter, allowed_peer):
        self.adapter = adapter
        self.allowed_peer = str(ipaddress.ip_address(allowed_peer))
        self.slots = threading.BoundedSemaphore(4)
        super().__init__(address, Handler)

    def server_bind(self):
        if os.name == "nt":
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        super().server_bind()

    def verify_request(self, request, client_address):
        return client_address[0] == self.allowed_peer

    def process_request(self, request, client_address):
        if not self.slots.acquire(blocking=False):
            self.shutdown_request(request)
            return
        try:
            super().process_request(request, client_address)
        except Exception:
            self.slots.release()
            raise

    def process_request_thread(self, request, client_address):
        try:
            super().process_request_thread(request, client_address)
        finally:
            self.slots.release()

    def handle_error(self, request, client_address):
        pass  # No tracebacks containing private request or connection details.


def fixture_server(adapter):
    """Constructor-only loopback synthetic fixture. Never used by the CLI."""
    return ScopedServer(("127.0.0.1", 0), adapter, "127.0.0.1")


def verify_tailnet(status, listen_ip, peer_ip):
    """Use a fresh `tailscale status --json` result, not an assumed 100.x address."""
    listen, peer = ipaddress.ip_address(listen_ip), ipaddress.ip_address(peer_ip)
    if listen.version != 4 or peer.version != 4 or listen not in TAILNET or peer not in TAILNET or listen == peer:
        raise ValueError("distinct Tailscale IPv4 addresses are required")
    if status.get("BackendState") != "Running":
        raise ValueError("Tailscale is not running")
    own = status.get("Self", {}).get("TailscaleIPs", [])
    peers = {ip for entry in status.get("Peer", {}).values() for ip in entry.get("TailscaleIPs", [])}
    if str(listen) not in own or str(peer) not in peers:
        raise ValueError("listen address or selected peer is absent from Tailscale status")
    return str(listen), str(peer)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="action", required=True)
    create = sub.add_parser("create-credential", help="create a private local scoped credential; do not print it")
    create.add_argument("path", type=Path)
    serve = sub.add_parser("serve")
    serve.add_argument("--control", type=Path, required=True)
    serve.add_argument("--credential-file", type=Path, required=True)
    serve.add_argument("--journal", type=Path, required=True)
    serve.add_argument("--listen-ip", required=True)
    serve.add_argument("--peer-ip", required=True)
    serve.add_argument("--listen-port", type=int, default=8811)
    serve.add_argument("--tailscale", default="tailscale", help="local Tailscale executable")
    args = parser.parse_args()
    if args.action == "create-credential":
        args.path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(args.path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as out:
            json.dump({"enabled": True, "token": secrets.token_urlsafe(32)}, out)
        print("Scoped credential created locally. No credential was printed.")
        return
    if not 1 <= args.listen_port <= 65535:
        parser.error("listen-port must be 1..65535")
    status = subprocess.run([args.tailscale, "status", "--json"], check=True,
                            capture_output=True, text=True, timeout=10)
    listen, peer = verify_tailnet(json.loads(status.stdout), args.listen_ip, args.peer_ip)
    journal = Journal(args.journal)
    adapter = Adapter(args.control, args.credential_file, journal)
    # Validate local configuration without printing or sending any credentials.
    credential = read_json(args.credential_file)
    if adapter.authenticate(f"Bearer {credential.get('token', '')}") is None:
        raise ValueError("scoped credential is disabled or invalid")
    adapter.broker.configuration()
    server = ScopedServer((listen, args.listen_port), adapter, peer)
    print("Scoped Nua adapter ready on the verified Tailscale interface.", flush=True)
    try:
        server.serve_forever(poll_interval=0.2)
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        journal.close()


if __name__ == "__main__":
    main()
