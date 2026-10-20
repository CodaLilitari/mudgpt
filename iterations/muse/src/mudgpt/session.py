"""One persistent MUD connection; observations never certify command completion."""
from __future__ import annotations

import asyncio
from collections import deque
from copy import deepcopy
import json
import logging
import math
import re
import secrets
import time
import unicodedata

import telnetlib3
from telnetlib3.telopt import GMCP, MSDP

from .colors import ANSIStyleFilter, DEFAULT_STYLE, StyledText


# Backwards-compatible plain feed API for callers that do not need spans.
_TextFilter = ANSIStyleFilter


class Session:
    def __init__(self, prompt_pattern=None, max_events=512, max_chars=65536):
        if max_events < 2 or max_chars < 1024:
            raise ValueError("Observation limits are too small")
        self._prompt = re.compile(prompt_pattern) if prompt_pattern else None
        self._max_events, self._max_chars = max_events, max_chars
        self._events, self._sizes = deque(), deque()
        # One Session is created per broker process. Unlike generation/cursor,
        # this identifier cannot collide merely because that process restarted.
        self._broker_id = secrets.token_hex(16)
        self._size = self._seq = 0
        self._owner = "human"
        self._lease_epoch, self._lease = 0, secrets.token_urlsafe(24)
        self._connected = self._connecting = self._busy = False
        self._reader = self._writer = self._task = None
        self._changed = asyncio.Event()
        self._secrets, self._pending = [], ""
        self._pending_styles = ()
        self._filter, self._tail = _TextFilter(), ""
        self._last_activity = 0.0
        self._prompt_seq = self._generation = self._epoch = self._command_count = 0
        self._last_command = None

    def _add(self, kind, **values):
        self._seq += 1
        event = {"seq": self._seq, "kind": kind, "generation": self._generation, **values}
        size = len(json.dumps(event, ensure_ascii=False))
        self._events.append(event)
        self._sizes.append(size)
        self._size += size
        while len(self._events) > self._max_events or self._size > self._max_chars:
            self._events.popleft()
            self._size -= self._sizes.popleft()
        self._last_activity = asyncio.get_running_loop().time()
        self._changed.set()

    def _redact(self, text):
        if not self._secrets:
            return text
        return re.sub("|".join(re.escape(s) for s in sorted(self._secrets, key=len, reverse=True)),
                      "[REDACTED]", text)

    def _redact_styled(self, incoming):
        """Hold possible secret prefixes before exposing either text or styles."""
        text = self._pending + incoming.text
        styles = self._pending_styles + incoming.styles
        self._pending, self._pending_styles = "", ()
        if not self._secrets:
            return StyledText(text, styles)
        secrets_by_length = sorted(self._secrets, key=len, reverse=True)
        output, output_styles = [], []
        index = 0
        while index < len(text):
            remaining = len(text) - index
            # Hold a longer possible match even if a shorter secret already
            # matches: otherwise a chunk boundary can expose the longer suffix.
            if any(remaining < len(secret) and secret.startswith(text[index:])
                   for secret in secrets_by_length):
                self._pending, self._pending_styles = text[index:], styles[index:]
                break
            match = next((secret for secret in secrets_by_length if text.startswith(secret, index)), None)
            if match:
                marker = "[REDACTED]"
                output.append(marker)
                output_styles.extend([DEFAULT_STYLE] * len(marker))
                index += len(match)
            else:
                output.append(text[index])
                output_styles.append(styles[index])
                index += 1
        return StyledText("".join(output), tuple(output_styles))

    def _text(self, incoming):
        # Production reads are already 4096 characters. Keep direct calls and
        # tests equally bounded in working storage and individual event size.
        for start in range(0, len(incoming), 4096):
            self._styled_text(self._filter.feed_styled(incoming[start:start + 4096]))

    def _add_styled(self, safe):
        spans = safe.style_spans
        estimated = {"seq": self._seq + 1, "kind": "text", "generation": self._generation,
                     "text": safe.text, "style_spans": spans}
        if len(safe.text) > 1 and len(json.dumps(estimated, ensure_ascii=False)) > self._max_chars:
            # Dense style changes can cost more than text. Split before ring
            # insertion so one oversize event cannot erase the entire latest
            # observation; each new piece computes its own relative offsets.
            middle = len(safe.text) // 2
            self._add_styled(StyledText(safe.text[:middle], safe.styles[:middle]))
            self._add_styled(StyledText(safe.text[middle:], safe.styles[middle:]))
        else:
            self._add("text", text=safe.text, style_spans=spans)

    def _styled_text(self, incoming):
        safe = self._redact_styled(incoming)
        if not safe.text:
            return
        self._add_styled(safe)
        self._tail = (self._tail + safe.text)[-4096:]
        if self._prompt:
            match = next(reversed(list(self._prompt.finditer(self._tail))), None)
            if match and match.end() == len(self._tail):
                self._add("prompt", text=match.group())
                self._prompt_seq = self._seq

    def _protocol(self, protocol, data, emit=None):
        # Payload-dependent partial redaction reveals which values occur in a
        # secret. Once a secret is registered, withhold the entire structured
        # payload, including keys/numbers/shape, behind a constant JSON marker.
        # This deliberately trades authenticated GMCP/MSDP data for privacy;
        # the normal text/style stream keeps its incremental secret filtering.
        safe = {"redacted": True, "reason": "session_contains_secrets"} if self._secrets else data
        encoded = json.dumps(safe, ensure_ascii=False)
        (emit or self._add)("protocol", protocol=protocol, data=encoded[:4096], truncated=len(encoded) > 4096)

    async def connect(self, host, port, tls=False):
        if self._connected or self._connecting or self._busy:
            raise RuntimeError("Session is already connected or busy")
        if not isinstance(host, str) or not host or not 1 <= int(port) <= 65535:
            raise ValueError("A host and valid port are required")
        if type(tls) is not bool:
            raise ValueError("tls must be a boolean; validation cannot be disabled")
        self._connecting = True
        epoch = self._epoch
        session = self
        early_protocols, ready = deque(maxlen=128), False

        def protocol_event(protocol, data):
            emit = None if ready else lambda kind, **values: early_protocols.append((kind, values))
            session._protocol(protocol, data, emit=emit)

        silent = logging.Logger("mudgpt.transport.silent")
        silent.disabled = True

        class Client(telnetlib3.TelnetClient):
            def __init__(self, **kwargs):
                super().__init__(**kwargs)
                self.log = silent

            def connection_made(self, transport):
                super().connection_made(transport)
                for item in (self.reader, self.writer, self.writer.local_option,
                             self.writer.remote_option, self.writer.pending_option):
                    item.log = silent
                self.writer.set_ext_callback(MSDP, lambda data: protocol_event("MSDP", data))

            def on_gmcp(self, package, data):
                protocol_event("GMCP", {"package": package, "data": data})

        try:
            reader, writer = await telnetlib3.open_connection(
                host, int(port), client_factory=Client, encoding="utf8", encoding_errors="replace",
                term="MudGPT", connect_maxwait=1, connect_timeout=10, limit=65536,
                send_environ=[], ssl=True if tls else None)
            if epoch != self._epoch:
                writer.close()
                await writer.wait_closed()
                raise RuntimeError("Connection cancelled by close")
            self._reader, self._writer = reader, writer
            self._filter, self._tail, self._pending = _TextFilter(), "", ""
            self._pending_styles = ()
            self._generation += 1
            self._connected = True
            self._add("connected", tls=tls)
            ready = True
            for kind, values in early_protocols:
                self._add(kind, **values)
            self._task = asyncio.create_task(self._read())
        finally:
            self._connecting = False
        return self.status()

    async def _read(self):
        reason = "eof"
        try:
            while chunk := await self._reader.read(4096):
                self._text(chunk)
        except asyncio.CancelledError:
            reason = "closed"
        except Exception:
            reason = "transport_error"
        finally:
            # An unfinished possible secret is never flushed verbatim.
            if self._pending:
                self._pending = ""
                self._pending_styles = ()
                self._add("text", text="[REDACTED]", style_spans=[])
            if self._connected:
                self._connected = False
                self._add("disconnected", reason=reason)

    def claim(self, owner, takeover=False, lease=None):
        if not isinstance(owner, str) or not owner.strip() or len(owner) > 80:
            raise ValueError("A controller name of 1-80 characters is required")
        if takeover and owner != "human":
            raise PermissionError("Only the human controller may take over")
        if self._owner not in (None, owner) and not takeover:
            raise PermissionError("Another controller owns this session")
        if self._owner == owner and owner != "human":
            self.check_controller(owner, lease)
        self._owner = owner
        self._lease_epoch += 1
        self._lease = secrets.token_urlsafe(24)
        self._changed.set()
        return {**self.status(), "lease": self._lease}

    def check_controller(self, owner, lease=None):
        if self._owner != owner or owner is None:
            raise PermissionError("Controller does not own this session")
        # The trusted human CLI deliberately retains takeover/no-token access.
        if owner != "human" or lease is not None:
            if not isinstance(lease, str) or not secrets.compare_digest(lease, self._lease):
                raise PermissionError("A current controller lease is required")

    def release(self, owner, lease=None):
        self.check_controller(owner, lease)
        self._owner = None
        self._lease_epoch += 1
        self._lease = secrets.token_urlsafe(24)
        self._changed.set()
        return self.status()

    def status(self):
        return {"broker_id": self._broker_id,
                "capabilities": ["guarded_send_v1"],
                "connected": self._connected, "connecting": self._connecting,
                "busy": self._busy, "owner": self._owner, "cursor": self._seq,
                "lease_epoch": self._lease_epoch,
                "generation": self._generation,
                "last_command": dict(self._last_command) if self._last_command else None}

    def _check_observation(self, expected_broker_id=None, expected_generation=None, expected_cursor=None):
        """Check supplied observation guards without yielding before a write."""
        if expected_broker_id is not None:
            if not isinstance(expected_broker_id, str) or not expected_broker_id:
                raise ValueError("expected_broker_id must be a nonempty string")
            if expected_broker_id != self._broker_id:
                raise RuntimeError("Broker lifetime changed; nothing sent; obtain fresh observations")
        for name, expected, actual in (
                ("expected_generation", expected_generation, self._generation),
                ("expected_cursor", expected_cursor, self._seq)):
            if expected is not None:
                if type(expected) is not int or expected < 0:
                    raise ValueError(f"{name} must be a nonnegative integer")
                if expected != actual:
                    raise RuntimeError(f"{name} changed; nothing sent; obtain fresh observations")

    def observe(self, after=0, expected_broker_id=None):
        self._check_observation(expected_broker_id=expected_broker_id)
        if type(after) is not int or after < 0 or after > self._seq:
            raise ValueError("Cursor must be an integer within this session's history")
        oldest = self._events[0]["seq"] if self._events else self._seq + 1
        # A copy prevents observers from changing the authoritative event ring.
        return {"broker_id": self._broker_id,
                "events": [deepcopy(e) for e in self._events if e["seq"] > after],
                "cursor": self._seq, "oldest": oldest, "truncated": after < oldest - 1,
                "generation": self._generation}

    async def send(self, command, owner="human", timeout=3, quiet=0.3, secret=False, lease=None,
                   expected_broker_id=None, expected_generation=None, expected_cursor=None,
                   expires_at=None):
        if not isinstance(command, str) or len(command) > 500:
            raise ValueError("Command must be a string of at most 500 characters")
        if any(unicodedata.category(c).startswith("C") or c in "\u2028\u2029" for c in command):
            raise ValueError("Command cannot contain control characters or line separators")
        if any(type(v) not in (int, float) or not math.isfinite(v) or v <= 0
               for v in (timeout, quiet)):
            raise ValueError("timeout and quiet must be finite positive seconds")
        if not self._connected:
            raise ConnectionError("Session is disconnected")
        self.check_controller(owner, lease)
        if self._busy:
            raise RuntimeError("A command is already in flight")
        guards = (expected_broker_id, expected_generation, expected_cursor)
        if any(value is not None for value in guards) and any(value is None for value in guards):
            raise ValueError("Guarded sends require expected_broker_id, expected_generation and expected_cursor")
        self._check_observation(expected_broker_id, expected_generation, expected_cursor)
        if expires_at is not None:
            if type(expires_at) not in (int, float) or not math.isfinite(expires_at):
                raise ValueError("expires_at must be finite POSIX seconds")
            if time.time() >= expires_at:
                raise RuntimeError("Decision expired; nothing sent; obtain a fresh decision")
        # No await occurs between these checks and writer.write below. This binds
        # the decision to observations processed by this broker, not unseen bytes
        # in the network or the remote game's state.
        if secret and command and command not in self._secrets:
            if len(self._secrets) >= 32:
                raise ValueError("Session secret limit reached")
            self._secrets.append(command)
        self._busy = True
        lease_epoch = self._lease_epoch
        start = self._seq
        self._command_count += 1
        command_id = f"{self._generation}:{self._command_count}"
        self._last_command = {"command_id": command_id, "delivery": "unknown", "command_complete": False}
        self._add("send_attempt", command_id=command_id)
        response_start = self._seq
        loop = asyncio.get_running_loop()
        sent_at = loop.time()
        deadline = sent_at + timeout
        reason = "timeout"
        try:
            try:
                self._writer.write(command + "\r\n")
                await asyncio.wait_for(self._writer.drain(), max(0.001, deadline - loop.time()))
            except (Exception, asyncio.CancelledError) as error:
                self._add("send_uncertain", command_id=command_id, delivery="unknown")
                await self.close()
                if isinstance(error, asyncio.CancelledError):
                    raise
                return {"reason": "transport_error", **self._last_command, **self.observe(start)}
            self._last_command["delivery"] = "transport_accepted"
            while True:
                self._changed.clear()
                now = loop.time()
                if not self._connected:
                    reason = "disconnected"
                    break
                if self._owner != owner or self._lease_epoch != lease_epoch:
                    reason = "ownership_changed"
                    break
                if self._prompt_seq > start:
                    reason = "prompt"
                    break
                if now >= deadline:
                    break
                # Silence before any new event is not treated as a response.
                if self._seq > response_start and now >= max(sent_at, self._last_activity) + quiet:
                    reason = "quiet"
                    break
                wait = deadline - now
                if self._seq > response_start:
                    wait = min(wait, max(0.001, self._last_activity + quiet - now))
                try:
                    await asyncio.wait_for(self._changed.wait(), wait)
                except TimeoutError:
                    pass
            return {"reason": reason, **self._last_command, **self.observe(start)}
        except asyncio.CancelledError:
            if self._connected:
                self._last_command["delivery"] = "unknown"
                self._add("send_uncertain", command_id=command_id, delivery="unknown")
                await self.close()
            raise
        finally:
            self._busy = False

    async def close(self):
        self._epoch += 1
        if self._writer:
            self._writer.close()
            try:
                await asyncio.wait_for(self._writer.wait_closed(), 1)
            except (TimeoutError, ConnectionError):
                pass
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                # A read task cancelled before its first step never reaches its
                # own handler. Do not mistake that for cancellation of close().
                if asyncio.current_task().cancelling():
                    raise
            finally:
                self._task = None
        if self._connected:
            self._connected = False
            self._add("disconnected", reason="closed")
        self._reader = self._writer = None
        self._changed.set()
