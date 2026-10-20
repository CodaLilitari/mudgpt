#!/usr/bin/env python3
"""Nua adapter call helper: one bounded, credential-carrying call per run.

Reads an RSA-OAEP-encrypted scoped-credential envelope (validated against an
approved packet SHA-256 and this host's receiving key), decrypts the token in
memory only, and issues exactly one POST /rpc to the configured pinned endpoint
through the runtime's authenticated CONNECT proxy (Tailscale port 3130).

The token is never written to disk, logged, printed, or placed in argv.
The destination Bearer header is written only after the CONNECT tunnel is
established, only to that pinned endpoint. One attempt: no retries, no
redirects, no alternate targets. Failures are sanitized and phase-labelled.

Every terminal report carries an explicit outcome:
  response_received  a well-formed inner HTTP response with a JSON body
  not_sent           validation, key, or outer-tunnel failure; the inner
                     request was provably never written
  uncertain          anything after a possible inner write: transport,
                     framing, malformed status, incomplete or non-JSON
                     response. Never safe to retry; inspect and re-decide.
  unknown            unexpected internal exception; stop and inspect, never
                     assume the command was not sent or automatically retry.
Exit codes: 0 response_received (even HTTP errors, not operation success);
1 unexpected internal exception (unknown); 2 validation (not_sent); 3 key/decryption
(not_sent); 4 outer tunnel (not_sent); 5 inner transport (uncertain);
6 inner framing/response (uncertain). This helper never retries.

If an inner response echoes the decrypted token anywhere (key, value, or
string fragment), the response is suppressed entirely and replaced with a
static uncertain result: the credential value must never reach any output.
"""
from __future__ import annotations

import argparse
import base64
import binascii
import hashlib
import ipaddress
import json
import os
import re
import socket
import sys
import time
from pathlib import Path
from dataclasses import dataclass
from urllib.parse import urlparse, unquote

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa

DEST_PATH = "/rpc"
EXPECTED_FORMAT = "mudgpt-gameplay-credential-envelope-v1"
EXPECTED_ALGORITHM = "RSA-OAEP-SHA256-MGF1-SHA256-empty-label"
CONFIG_FORMAT = "mudgpt-muse-call-config-v1"
TAILNET = ipaddress.ip_network("100.64.0.0/10")
ENVELOPE_FIELDS = ("format", "algorithm", "recipient_spki_sha256", "endpoint",
                   "peer_ip", "purpose", "revocation", "ciphertext_base64")
TOKEN_SHAPE = re.compile(r"[A-Za-z0-9_-]{43,128}\Z")
STATUS_LINE = re.compile(r"HTTP/1\.[01] ([0-9]{3})(?: .*)?\Z")
MAX_REQUEST = 8192
MAX_RESPONSE = 1024 * 1024
MAX_HEADERS = 16384
CONNECT_WAIT = 150.0   # accommodates the human per-address approval card
INNER_WAIT = 30.0
OVERALL = 180.0

@dataclass(frozen=True)
class Config:
    endpoint: str
    host: str
    port: int
    peer_ip: str
    key_path: Path
    envelope_path: Path
    envelope_sha256: str


class Fail(Exception):
    """A sanitized, phase-labelled failure. Messages never carry secrets."""

    def __init__(self, code, message, outcome):
        super().__init__(message)
        self.code = code
        self.outcome = outcome


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON field")
        result[key] = value
    return result


def bounded_read(path, limit, label):
    try:
        with path.open("rb") as stream:
            raw = stream.read(limit + 1)
    except OSError:
        raise Fail(2, f"{label} unavailable", "not_sent")
    if not 0 < len(raw) <= limit:
        raise Fail(2, f"{label} size out of bounds", "not_sent")
    return raw


def validate_destination(endpoint, peer_ip):
    """Require literal, distinct tailnet IPv4 pins; no DNS or URL ambiguity."""
    try:
        parsed = urlparse(endpoint)
        host = str(ipaddress.IPv4Address(parsed.hostname))
        peer = str(ipaddress.IPv4Address(peer_ip))
        port = parsed.port
        if (parsed.scheme != "http" or port is None or not 1 <= port <= 65535
                or endpoint != f"http://{host}:{port}/rpc"
                or ipaddress.ip_address(host) not in TAILNET
                or ipaddress.ip_address(peer) not in TAILNET or host == peer):
            raise ValueError
    except (ValueError, TypeError, AttributeError):
        raise Fail(2, "destination requires distinct literal Tailscale IPv4 pins and /rpc", "not_sent")
    return host, port, peer


def load_config(path):
    raw = bounded_read(path, 8192, "config")
    try:
        obj = json.loads(raw, object_pairs_hook=unique_object)
        fields = {"format", "endpoint", "peer_ip", "key_path", "envelope_path", "envelope_sha256"}
        if not isinstance(obj, dict) or set(obj) != fields or any(not isinstance(v, str) for v in obj.values()):
            raise ValueError
        if obj["format"] != CONFIG_FORMAT or not re.fullmatch(r"[a-fA-F0-9]{64}", obj["envelope_sha256"]):
            raise ValueError
        if not obj["key_path"] or not obj["envelope_path"]:
            raise ValueError
    except (ValueError, TypeError):
        raise Fail(2, "config fields or format invalid", "not_sent")
    host, port, peer = validate_destination(obj["endpoint"], obj["peer_ip"])
    base = path.resolve().parent
    return Config(obj["endpoint"], host, port, peer,
                  (base / Path(obj["key_path"]).expanduser()).resolve(),
                  (base / Path(obj["envelope_path"]).expanduser()).resolve(),
                  obj["envelope_sha256"].lower())


def load_private_key(path):
    try:
        if os.name != "nt" and path.stat().st_mode & 0o077:
            raise Fail(3, "receiving key must be accessible only to its owner", "not_sent")
        raw = bounded_read(path, 16384, "receiving key")
    except OSError:
        raise Fail(3, "receiving key unavailable", "not_sent")
    try:
        key = serialization.load_pem_private_key(raw, password=None)
        if not isinstance(key, rsa.RSAPrivateKey) or key.key_size != 3072:
            raise ValueError
        return key
    except Exception:
        raise Fail(3, "receiving key could not be loaded", "not_sent")


def decrypt_token(envelope_bytes, config, key):
    """Validate the envelope exactly, then decrypt the token in memory."""
    if hashlib.sha256(envelope_bytes).hexdigest() != config.envelope_sha256:
        raise Fail(2, "envelope hash does not match the approved packet hash",
                   "not_sent")
    try:
        env = json.loads(envelope_bytes, object_pairs_hook=unique_object)
    except Exception:
        raise Fail(2, "envelope is not valid JSON", "not_sent")
    if not isinstance(env, dict) or set(env) != set(ENVELOPE_FIELDS):
        raise Fail(2, "envelope must be a JSON object", "not_sent")
    for field in ENVELOPE_FIELDS:
        if not isinstance(env.get(field), str):
            raise Fail(2, f"envelope field missing or not a string: {field}",
                       "not_sent")
    if env["format"] != EXPECTED_FORMAT:
        raise Fail(2, "envelope format does not match the pinned format",
                   "not_sent")
    if env["algorithm"] != EXPECTED_ALGORITHM:
        raise Fail(2, "envelope algorithm does not match the pinned scheme",
                   "not_sent")
    der = key.public_key().public_bytes(
        serialization.Encoding.DER,
        serialization.PublicFormat.SubjectPublicKeyInfo)
    fingerprint = hashlib.sha256(der).hexdigest()
    if env["recipient_spki_sha256"].replace(":", "").lower() != fingerprint:
        raise Fail(2, "envelope recipient does not match the receiving key",
                   "not_sent")
    if env["endpoint"] != config.endpoint:
        raise Fail(2, "envelope endpoint does not match the pinned endpoint",
                   "not_sent")
    if env["peer_ip"] != config.peer_ip:
        raise Fail(2, "envelope peer does not match the approved peer",
                   "not_sent")
    try:
        ciphertext = base64.b64decode(env["ciphertext_base64"], validate=True)
    except (binascii.Error, ValueError):
        raise Fail(2, "ciphertext is not valid base64", "not_sent")
    if len(ciphertext) != 384:
        raise Fail(2, "ciphertext is not exactly one RSA-3072 block", "not_sent")
    try:
        plaintext = key.decrypt(ciphertext, padding.OAEP(
            mgf=padding.MGF1(hashes.SHA256()),
            algorithm=hashes.SHA256(), label=None))
    except Exception:
        raise Fail(3, "decryption failed", "not_sent")
    try:
        token = plaintext.decode("ascii")
    except UnicodeDecodeError:
        raise Fail(3, "decrypted credential is not ASCII", "not_sent")
    if not TOKEN_SHAPE.fullmatch(token):
        raise Fail(3, "decrypted credential has an unexpected shape", "not_sent")
    return token


def load_request(path):
    """Return the request file's exact bytes; the decision tuple is untouched."""
    try:
        raw = bounded_read(path, MAX_REQUEST, "request file")
    except OSError:
        raise Fail(2, "request file unavailable", "not_sent")
    if not 0 < len(raw) <= MAX_REQUEST:
        raise Fail(2, "request body size out of bounds", "not_sent")
    try:
        parsed = json.loads(raw, object_pairs_hook=unique_object)
    except Exception:
        raise Fail(2, "request is not valid JSON", "not_sent")
    if not isinstance(parsed, dict) or not isinstance(parsed.get("op"), str):
        raise Fail(2, "request must be a JSON object with an op", "not_sent")
    return raw


def tunnel_proxy_parts():
    """Derive the Tailscale proxy (port 3130) from the runtime proxy env.

    The documented transport is plain http to the runtime proxy. Any other
    scheme fails closed rather than speaking plaintext to a TLS endpoint.
    Values stay in memory and are never printed.
    """
    parsed = urlparse(os.environ.get("HTTPS_PROXY", ""))
    if parsed.scheme != "http" or not parsed.hostname:
        raise Fail(4, "runtime proxy is not the documented http transport",
                   "not_sent")
    auth = None
    if parsed.username:
        userpass = unquote(parsed.username)
        if parsed.password:
            userpass += ":" + unquote(parsed.password)
        auth = base64.b64encode(userpass.encode()).decode()
    return parsed.hostname, 3130, auth


def parse_status_code(head):
    """Strictly parse an HTTP status line; return the 3-digit code or None.

    Nothing raw is ever reported: callers see a bounded numeric code or a
    static error.
    """
    line = head.split(b"\r\n", 1)[0].decode("latin-1")
    match = STATUS_LINE.fullmatch(line)
    if match is None:
        return None
    return int(match.group(1))


class Pipe:
    """Buffered socket reader that preserves post-CONNECT leftover bytes."""

    def __init__(self, sock):
        self.sock = sock
        self.buf = bytearray()

    def _fill(self, deadline):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError
        self.sock.settimeout(remaining)
        chunk = self.sock.recv(65536)
        if not chunk:
            raise EOFError
        self.buf += chunk

    def read_until(self, marker, limit, deadline):
        while True:
            idx = self.buf.find(marker)
            if idx >= 0:
                end = idx + len(marker)
                if end > limit:
                    raise OverflowError
                out = bytes(self.buf[:end])
                del self.buf[:end]
                return out
            if len(self.buf) > limit:
                raise OverflowError
            self._fill(deadline)

    def read_exact(self, count, deadline):
        while len(self.buf) < count:
            self._fill(deadline)
        out = bytes(self.buf[:count])
        del self.buf[:count]
        return out

    def read_to_eof(self, limit, deadline):
        out = bytearray(self.buf)
        self.buf.clear()
        while True:
            if len(out) > limit:
                raise OverflowError
            try:
                self._fill(deadline)
            except EOFError:
                return bytes(out)
            out += self.buf
            self.buf.clear()


def build_connect_request(auth, config):
    lines = [f"CONNECT {config.host}:{config.port} HTTP/1.1",
             f"Host: {config.host}:{config.port}"]
    if auth:
        lines.append(f"Proxy-Authorization: Basic {auth}")
    return ("\r\n".join(lines) + "\r\n\r\n").encode()


def build_inner_request(body, token, config):
    head = "\r\n".join([
        f"POST {DEST_PATH} HTTP/1.0",
        f"Host: {config.host}:{config.port}",
        "Content-Type: application/json",
        f"Content-Length: {len(body)}",
        f"Authorization: Bearer {token}",
        "Connection: close",
        "", "",
    ])
    return head.encode() + body


def parse_inner_head(head):
    """Strict framing for the inner response: unambiguous length, no TE."""
    status = parse_status_code(head)
    if status is None:
        raise Fail(6, "inner response status line was malformed", "uncertain")
    headers = {}
    for line in head.decode("latin-1").split("\r\n")[1:]:
        if not line:
            continue
        if ": " not in line:
            raise Fail(6, "inner response header was malformed", "uncertain")
        name, value = line.split(": ", 1)
        name = name.lower()
        if name in headers:
            raise Fail(6, "inner response carried a duplicate header",
                       "uncertain")
        headers[name] = value
    if "transfer-encoding" in headers:
        raise Fail(6, "inner response used an unsupported transfer encoding",
                   "uncertain")
    length = None
    if "content-length" in headers:
        raw = headers["content-length"]
        if not re.fullmatch(r"[0-9]+", raw):
            raise Fail(6, "inner Content-Length was malformed", "uncertain")
        length = int(raw)
        if length > MAX_RESPONSE:
            raise Fail(6, "inner response exceeded the bound", "uncertain")
    return status, length


def exchange(body, token, config):
    """One tunnel, one request, one response. No retry under any outcome."""
    host, port, auth = tunnel_proxy_parts()
    start = time.monotonic()
    try:
        sock = socket.create_connection((host, port), timeout=15)
    except OSError:
        raise Fail(4, "could not reach the runtime proxy (outer tunnel)",
                   "not_sent")
    try:
        pipe = Pipe(sock)
        try:
            sock.sendall(build_connect_request(auth, config))
        except OSError:
            raise Fail(4, "could not write the tunnel request (outer tunnel)",
                       "not_sent")
        try:
            head = pipe.read_until(b"\r\n\r\n", MAX_HEADERS, start + CONNECT_WAIT)
        except TimeoutError:
            raise Fail(4, "proxy approval/response timed out (outer tunnel)",
                       "not_sent")
        except (OSError, EOFError, OverflowError):
            raise Fail(4, "proxy tunnel response was malformed (outer tunnel)",
                       "not_sent")
        proxy_status = parse_status_code(head)
        if proxy_status is None:
            raise Fail(4, "proxy tunnel response was malformed (outer tunnel)",
                       "not_sent")
        if proxy_status != 200:
            raise Fail(4, f"proxy refused the tunnel (outer HTTP {proxy_status})",
                       "not_sent")
        # The tunnel is established. Fix the inner deadline BEFORE writing,
        # and bound the write itself by the remaining budget.
        inner_deadline = min(start + OVERALL, time.monotonic() + INNER_WAIT)
        remaining = inner_deadline - time.monotonic()
        if remaining <= 0:
            raise Fail(5, "inner deadline expired before any write; "
                          "nothing was sent", "not_sent")
        sock.settimeout(remaining)
        try:
            sock.sendall(build_inner_request(body, token, config))
        except OSError:
            raise Fail(5, "inner request write failed; outcome uncertain",
                       "uncertain")
        # From here on, any failure is post-write: outcome uncertain.
        try:
            head = pipe.read_until(b"\r\n\r\n", MAX_HEADERS, inner_deadline)
        except (OSError, EOFError, OverflowError):
            raise Fail(6, "inner response did not complete after the request "
                          "was written", "uncertain")
        status, length = parse_inner_head(head)
        try:
            if length is not None:
                payload = pipe.read_exact(length, inner_deadline)
            else:
                payload = pipe.read_to_eof(MAX_RESPONSE, inner_deadline)
        except OverflowError:
            raise Fail(6, "inner response exceeded the bound", "uncertain")
        except (OSError, EOFError):
            raise Fail(6, "inner response body was incomplete", "uncertain")
        return status, payload
    finally:
        try:
            sock.close()
        except OSError:
            pass


def filtered(value):
    """Remove credential-bearing keys at any depth before anything is shown."""
    if isinstance(value, dict):
        return {key: filtered(item) for key, item in value.items()
                if key.lower() not in {"token", "lease", "authorization"}}
    if isinstance(value, list):
        return [filtered(item) for item in value]
    return value


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="One bounded scoped-adapter call via the CONNECT proxy.")
    parser.add_argument("--config", type=Path, required=True,
                        help="private local pins and key/envelope paths (JSON)")
    parser.add_argument("--request", type=Path, required=True,
                        help="nonsecret request JSON; sent byte-for-byte")
    parser.add_argument("--validate-only", action="store_true",
                        help="validate + decrypt locally; make no connection")
    args = parser.parse_args(argv)
    token = None
    try:
        config = load_config(args.config)
        key = load_private_key(config.key_path)
        envelope_bytes = bounded_read(config.envelope_path, 16384, "envelope file")
        token = decrypt_token(envelope_bytes, config, key)
        body = load_request(args.request)
        if args.validate_only:
            print(json.dumps({"validated": True, "outcome": "not_sent",
                              "request_bytes": len(body)}))
            return 0
        status, payload = exchange(body, token, config)
        if token in payload.decode("latin-1"):
            raise Fail(6, "inner response echoed the credential; "
                          "output suppressed", "uncertain")
        try:
            result = filtered(json.loads(payload))
        except Exception:
            raise Fail(6, "inner response was not JSON; outcome uncertain",
                       "uncertain")
        out_text = json.dumps({"http_status": status,
                               "outcome": "response_received",
                               "result": result})
        if token in out_text:
            raise Fail(6, "inner response echoed the credential; "
                          "output suppressed", "uncertain")
        print(out_text)
        return 0
    except Fail as exc:
        text = json.dumps({"error": str(exc), "outcome": exc.outcome})
        if token and token in text:
            text = json.dumps({"error": "output suppressed",
                               "outcome": exc.outcome})
        print(text, file=sys.stderr)
        return exc.code
    except Exception:
        print(json.dumps({"error": "unexpected internal failure",
                          "outcome": "unknown"}), file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
