"""Deterministic unit/fake-server tests; no external MUD or real credentials."""
import asyncio
import json
import logging
import unittest
from unittest.mock import AsyncMock, patch

from mudgpt.session import Session, _TextFilter


class FilterTests(unittest.TestCase):
    def test_segmented_ansi_and_both_newline_orders(self):
        f = _TextFilter()
        chunks = ["\x1b[3", "1mRed\x1b[0m\r", "\nX\n", "\rY\x1b]0;hidden", "\x1b\\Z"]
        self.assertEqual("".join(f.feed(c) for c in chunks), "Red\nX\nYZ")


class SessionTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.commands = asyncio.Queue()
        self.peers = []
        self.handlers = set()
        self.session = Session(prompt_pattern=r"<\d+hp/\d+hp/\d+m/\d+m/\d+mv/\d+mv>")

        async def handle(reader, writer):
            task = asyncio.current_task()
            self.handlers.add(task)
            self.peers.append(writer)
            try:
                while command := await reader.readline():
                    await self.commands.put(command)
            finally:
                writer.close()
                await writer.wait_closed()
                self.handlers.discard(task)

        self.server = await asyncio.start_server(handle, "127.0.0.1", 0)
        port = self.server.sockets[0].getsockname()[1]
        await self.session.connect("127.0.0.1", port)

    async def asyncTearDown(self):
        await self.session.close()
        for peer in self.peers:
            peer.close()
        self.server.close()
        await self.server.wait_closed()
        if self.handlers:
            await asyncio.gather(*self.handlers, return_exceptions=True)

    async def wait_until(self, condition):
        async with asyncio.timeout(2):
            while not condition():
                await asyncio.sleep(0.005)

    async def emit(self, data):
        self.peers[-1].write(data)
        await self.peers[-1].drain()
        await asyncio.sleep(0.02)

    def text(self, after=0):
        return "".join(e.get("text", "") for e in self.session.observe(after)["events"]
                       if e["kind"] == "text")

    async def test_segmented_prompt_repeated_and_command_capture(self):
        for command in ("look", "score"):
            pending = asyncio.create_task(self.session.send(command, timeout=1, quiet=0.1))
            self.assertEqual(await self.commands.get(), command.encode() + b"\r\n")
            await self.emit(b"\x1b[32mRoom\x1b[0m\n")
            await self.emit(b"\r<3890hp/3890hp/3571m/")
            await self.emit(b"3571m/907mv/909mv>")
            result = await pending
            self.assertEqual(result["reason"], "prompt")
            self.assertFalse(result["command_complete"])
            self.assertTrue(any(e["kind"] == "prompt" for e in result["events"]))
        self.assertIn("Room\n<3890hp", self.text())
        self.assertNotIn("\x1b", self.text())

    async def test_roleplay_color_tokens_preserved_but_received_ansi_removed(self):
        # These are opaque game commands, not client-side formatting instructions.
        for command in (
            "emote {Dstudies the old tome.{x",
            "pmote folds his cloak and nods toward you.",
            "description {DA scholar in a travel-worn cloak.{x",
        ):
            pending = asyncio.create_task(self.session.send(command, timeout=1, quiet=0.05))
            self.assertEqual(await self.commands.get(), command.encode() + b"\r\n")
            await self.emit(b"\x1b[3")
            await self.emit(b"0mRendered words\x1b[0m; literal {Dtokens{x\n\r")
            result = await pending
            text = "".join(e.get("text", "") for e in result["events"] if e["kind"] == "text")
            self.assertEqual(text, "Rendered words; literal {Dtokens{x\n")
            self.assertEqual(result["delivery"], "transport_accepted")
            self.assertFalse(result["command_complete"])

    async def test_concurrent_send_rejected_and_timeout_not_completion(self):
        pending = asyncio.create_task(self.session.send("look", timeout=0.15))
        self.assertEqual(await self.commands.get(), b"look\r\n")
        with self.assertRaisesRegex(RuntimeError, "in flight"):
            await self.session.send("north")
        result = await pending
        self.assertEqual(result["reason"], "timeout")
        self.assertFalse(result["command_complete"])
        self.assertTrue(self.commands.empty())

    async def test_quiet_and_empty_pager_command(self):
        pending = asyncio.create_task(self.session.send("", timeout=1, quiet=0.05))
        self.assertEqual(await self.commands.get(), b"\r\n")
        await self.emit(b"More text\n\r[Hit Return to continue]")
        result = await pending
        self.assertEqual(result["reason"], "quiet")
        self.assertFalse(result["command_complete"])

    async def test_ownership_and_human_takeover(self):
        with self.assertRaises(PermissionError):
            self.session.claim("lily")
        self.session.release("human")
        lease = self.session.claim("lily")["lease"]
        with self.assertRaises(PermissionError):
            await self.session.send("look", owner="human")
        with self.assertRaises(PermissionError):
            self.session.release("other")
        with self.assertRaises(PermissionError):
            self.session.claim("other", takeover=True)
        pending = asyncio.create_task(self.session.send("look", owner="lily", lease=lease, timeout=1))
        await self.commands.get()
        self.session.claim("human", takeover=True)
        self.assertEqual((await pending)["reason"], "ownership_changed")
        self.assertEqual(self.session.status()["owner"], "human")

    async def test_same_name_reacquisition_rejects_stale_lease_and_waiter(self):
        self.session.release("human")
        old = self.session.claim("lily")["lease"]
        pending = asyncio.create_task(self.session.send("look", owner="lily", lease=old, timeout=1))
        await self.commands.get()
        self.session.release("lily", lease=old)
        current = self.session.claim("lily")["lease"]
        self.assertNotEqual(old, current)
        self.assertEqual((await pending)["reason"], "ownership_changed")
        for stale in (None, old):
            with self.assertRaises(PermissionError):
                await self.session.send("north", owner="lily", lease=stale)
            with self.assertRaises(PermissionError):
                self.session.release("lily", lease=stale)
            with self.assertRaises(PermissionError):
                self.session.claim("lily", lease=stale)
        self.assertTrue(self.commands.empty())
        self.assertNotIn("lease", self.session.status())
        self.assertNotIn(current, json.dumps(self.session.observe()))
        self.session.release("lily", lease=current)

    async def test_nondestructive_cursors_copy_and_truncation(self):
        cursor = self.session.status()["cursor"]
        await self.emit(b"hello\n\r")
        first = self.session.observe(cursor)
        self.assertEqual(first, self.session.observe(cursor))
        first["events"][0]["text"] = "tampered"
        self.assertNotIn("tampered", self.text())
        self.assertEqual(self.session.observe(first["cursor"])["events"], [])
        bounded = Session(max_events=3, max_chars=1024)
        for value in range(10):
            bounded._add("text", text=str(value))
        self.assertEqual(len(bounded.observe()["events"]), 3)
        self.assertEqual(bounded.observe()["oldest"], 8)
        self.assertTrue(bounded.observe(2)["truncated"])
        with self.assertRaises(ValueError):
            bounded.observe(99)

    async def test_split_secret_echo_never_visible(self):
        secret = "test-password-42"
        pending = asyncio.create_task(self.session.send(secret, secret=True, timeout=0.3, quiet=0.05))
        self.assertEqual(await self.commands.get(), secret.encode() + b"\r\n")
        await self.emit(b"Greeting test-")
        self.assertEqual(self.text(), "Greeting ")
        await self.emit(b"pass\x1b[31mword-")
        self.assertEqual(self.text(), "Greeting ")
        await self.emit(b"42\x1b[0m accepted\n\r")
        result = await pending
        self.assertNotIn(secret, json.dumps(result))
        self.assertNotIn("test-", json.dumps(self.session.observe()))
        self.assertIn("[REDACTED] accepted", self.text())
        self.assertFalse(any(e["kind"] == "command" for e in self.session.observe()["events"]))

    async def test_secret_masking_rebases_spans_across_style_boundaries(self):
        session = Session()
        session._secrets = ["key", "key-long"]
        session._text("\x1b[31mékey")
        first = session.observe()["events"]
        self.assertEqual(first[0]["text"], "é")
        self.assertEqual(first[0]["style_spans"], [{"start": 0, "end": 1, "fg": "red"}])
        self.assertNotIn("key", json.dumps(session.observe()))
        session._text("\x1b[34m-lo")
        self.assertEqual(len(session.observe()["events"]), 1)
        session._text("\x1b[1mng🙂\x1b[0m!")
        last = session.observe()["events"][-1]
        self.assertEqual(last["text"], "[REDACTED]🙂!")
        self.assertEqual(last["style_spans"], [{"start": 10, "end": 11, "fg": "blue", "bold": True}])
        self.assertEqual(session._pending_styles, ())
        self.assertNotIn("key", json.dumps(session.observe()))
        self.assertNotIn("-lo", json.dumps(session.observe()))

    async def test_all_chunk_splits_mask_secret_and_keep_public_styles(self):
        wire = "\x1b[31mésecret\x1b[34m-42🙂\x1b[0m!"
        for cut in range(len(wire) + 1):
            session = Session()
            session._secrets = ["secret-42"]
            session._text(wire[:cut])
            before = json.dumps(session.observe())
            self.assertNotIn("secret", before)
            session._text(wire[cut:])
            events = session.observe()["events"]
            text = "".join(event["text"] for event in events)
            self.assertEqual(text, "é[REDACTED]🙂!")
            expanded = []
            for event in events:
                styles = [{} for _ in event["text"]]
                for span in event["style_spans"]:
                    self.assertLessEqual(span["end"], len(event["text"]))
                    attrs = {key: value for key, value in span.items() if key not in ("start", "end")}
                    styles[span["start"]:span["end"]] = [attrs] * (span["end"] - span["start"])
                expanded.extend(styles)
            self.assertEqual(expanded, [{"fg": "red"}] + [{}] * 10 + [{"fg": "blue"}, {}])

    async def test_style_observation_deepcopy_and_retention(self):
        session = Session(max_events=3, max_chars=1024)
        for index in range(8):
            session._text(f"\x1b[31mrow{index}\x1b[0m")
        snapshot = session.observe()
        self.assertTrue(snapshot["truncated"])
        self.assertEqual(len(snapshot["events"]), 3)
        snapshot["events"][0]["style_spans"][0]["fg"] = "tampered"
        snapshot["events"][1]["style_spans"].append({"start": 0, "end": 9999, "fg": "tampered"})
        self.assertNotIn("tampered", json.dumps(session.observe()))
        session._text("\x1b[32m" + "x" * 20000)
        self.assertLessEqual(session._size, session._max_chars)
        self.assertTrue(session.observe()["truncated"])
        self.assertTrue(session.observe()["events"])
        for event in session.observe()["events"]:
            self.assertTrue(all(0 <= span["start"] < span["end"] <= len(event["text"])
                                for span in event.get("style_spans", [])))

    async def test_dense_style_events_retain_latest_text_with_rebased_spans(self):
        session = Session(max_events=512, max_chars=1024)
        wire = "".join(f"\x1b[{31 + (index % 2)}m{index % 10}" for index in range(200))
        session._text(wire + "\x1b[35mEND")
        observed = session.observe()
        self.assertTrue(observed["truncated"])
        self.assertTrue(observed["events"])
        text = "".join(event["text"] for event in observed["events"])
        self.assertTrue(text.endswith("END"))
        self.assertLessEqual(session._size, 1024)
        last = observed["events"][-1]
        self.assertEqual(last["style_spans"][-1], {"start": len(last["text"]) - 3,
                                                 "end": len(last["text"]), "fg": "magenta"})

    async def test_reconnect_resets_style_and_eof_masks_pending_style(self):
        await self.emit(b"\x1b[31mRed")
        self.assertEqual(self.session.observe()["events"][-1]["style_spans"][0]["fg"], "red")
        self.session._secrets.append("unfinished-secret")
        await self.emit(b"unfinished-")
        await self.session.close()
        last_text = [event for event in self.session.observe()["events"] if event["kind"] == "text"][-1]
        self.assertEqual(last_text["text"], "[REDACTED]")
        self.assertEqual(last_text["style_spans"], [])
        self.assertEqual(self.session._pending_styles, ())
        cursor = self.session.status()["cursor"]
        await self.session.connect("127.0.0.1", self.server.sockets[0].getsockname()[1])
        await self.emit(b"Default")
        text_event = [event for event in self.session.observe(cursor)["events"] if event["kind"] == "text"][-1]
        self.assertEqual(text_event["text"], "Default")
        self.assertEqual(text_event["style_spans"], [])

    async def test_eof_disconnect_and_no_replay(self):
        pending = asyncio.create_task(self.session.send("look", timeout=1))
        await self.commands.get()
        self.peers[-1].close()
        result = await pending
        self.assertEqual(result["reason"], "disconnected")
        self.assertFalse(self.session.status()["connected"])
        with self.assertRaises(ConnectionError):
            await self.session.send("look")
        self.assertTrue(self.commands.empty())
        await self.session.close()
        await self.session.close()

    async def test_control_and_size_validation(self):
        for command in ("n\ns", "n\rs", "\x1bhelp", "\t", "x" * 501, "n\u2028s"):
            with self.assertRaises(ValueError):
                await self.session.send(command)
        for timeout in (0, -1, float("nan"), float("inf")):
            with self.assertRaises(ValueError):
                await self.session.send("look", timeout=timeout)
        self.assertTrue(self.commands.empty())

    async def test_fragmented_gmcp_and_protocol_observation(self):
        from telnetlib3.telopt import IAC, WILL, SB, SE, GMCP
        await self.emit(IAC + WILL + GMCP)
        payload = IAC + SB + GMCP + b'Char.Vitals {"hp":42}' + IAC + SE
        await self.emit(payload[:7])
        await self.emit(payload[7:])
        await self.wait_until(lambda: any(e["kind"] == "protocol" for e in self.session.observe()["events"]))
        events = [e for e in self.session.observe()["events"] if e["kind"] == "protocol"]
        self.assertEqual(events[-1]["protocol"], "GMCP")
        self.assertIn('"hp": 42', events[-1]["data"])

    async def test_tls_default_validation_requested(self):
        isolated = Session()
        with patch("mudgpt.session.telnetlib3.open_connection", new_callable=AsyncMock) as call:
            call.side_effect = ConnectionError("test stub")
            with self.assertRaises(ConnectionError):
                await isolated.connect("localhost", 12345, tls=True)
            self.assertIs(call.call_args.kwargs["ssl"], True)
            self.assertEqual(call.call_args.kwargs["send_environ"], [])
        self.assertFalse(isolated.status()["connecting"])

    async def test_write_then_drain_failure_quarantines_without_replay(self):
        original_write = self.session._writer.write
        writes = []

        def capture(command):
            writes.append(command)
            original_write(command)

        self.session._writer.write = capture
        self.session._writer.drain = AsyncMock(side_effect=ConnectionError("synthetic failure"))
        result = await self.session.send("north")
        self.assertEqual(writes, ["north\r\n"])
        self.assertEqual(result["reason"], "transport_error")
        self.assertEqual(result["delivery"], "unknown")
        self.assertFalse(result["command_complete"])
        self.assertEqual(result["command_id"], "1:1")
        self.assertFalse(self.session.status()["connected"])
        self.assertEqual(self.session.status()["last_command"]["delivery"], "unknown")
        with self.assertRaises(ConnectionError):
            await self.session.send("north")

    async def test_generation_survives_close_and_advances_on_reconnect(self):
        first = self.session.status()["generation"]
        await self.session.close()
        self.assertEqual(self.session.status()["generation"], first)
        await self.session.connect("127.0.0.1", self.server.sockets[0].getsockname()[1])
        self.assertEqual(self.session.status()["generation"], first + 1)
        self.assertEqual(self.session.observe()["generation"], first + 1)

    async def test_stalled_drain_is_bounded_and_delivery_remains_unknown(self):
        stalled = asyncio.Event()
        self.session._writer.drain = stalled.wait
        result = await asyncio.wait_for(self.session.send("north", timeout=0.05), 2)
        self.assertEqual(result["reason"], "transport_error")
        self.assertEqual(result["delivery"], "unknown")
        self.assertFalse(result["command_complete"])
        self.assertFalse(self.session.status()["connected"])
        self.assertEqual(await self.commands.get(), b"north\r\n")
        self.assertTrue(self.commands.empty())
        with self.assertRaises(ConnectionError):
            await self.session.send("north")

    async def test_cancelled_send_disconnects_and_retains_uncertainty(self):
        pending = asyncio.create_task(self.session.send("north", timeout=1))
        await self.commands.get()
        pending.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await pending
        self.assertFalse(self.session.status()["connected"])
        self.assertEqual(self.session.status()["last_command"]["delivery"], "unknown")


if __name__ == "__main__":
    unittest.main()
