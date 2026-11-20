"""Delivery classifications use typed pre-write evidence, never message guesses."""
import time
import unittest
from unittest.mock import AsyncMock, Mock

from mudgpt.session import Session
from mudgpt.errors import BrokerRequestError, error_payload, is_prewrite_error


class StructuredErrors(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.s = Session()
        self.s._connected = True
        self.s._writer = Mock(drain=AsyncMock())
        self.guard = {"expected_broker_id": self.s.status()["broker_id"],
                      "expected_generation": self.s.status()["generation"],
                      "expected_cursor": self.s.status()["cursor"]}

    async def test_cursor_refusal_is_typed_and_has_no_write(self):
        self.s._add("text", text="A synthetic bell rings.\n")
        with self.assertRaises(RuntimeError) as caught:
            await self.s.send("look", **self.guard)
        payload = error_payload(caught.exception)
        self.assertTrue(is_prewrite_error(payload))
        self.assertEqual(payload["error_code"], "cursor_changed")
        self.assertEqual(payload["delivery"], "not_sent")
        self.s._writer.write.assert_not_called()
        self.assertFalse(any(e["kind"] == "send_attempt" for e in self.s.observe()["events"]))

    async def test_expired_original_decision_is_not_sent(self):
        with self.assertRaises(RuntimeError) as caught:
            await self.s.send("look", expires_at=time.time()-1, **self.guard)
        self.assertEqual(error_payload(caught.exception)["error_code"], "decision_expired")
        self.s._writer.write.assert_not_called()

    async def test_original_lease_epoch_is_checked(self):
        with self.assertRaises(RuntimeError) as caught:
            await self.s.send("look", expected_lease_epoch=999, **self.guard)
        self.assertEqual(error_payload(caught.exception)["error_code"], "lease_epoch_changed")
        self.s._writer.write.assert_not_called()

    async def test_invalid_command_is_typed_before_write(self):
        with self.assertRaises(ValueError) as caught:
            await self.s.send("look\nquit", **self.guard)
        self.assertEqual(error_payload(caught.exception)["delivery"], "not_sent")
        self.assertEqual(error_payload(caught.exception)["error_code"], "invalid_request")
        self.s._writer.write.assert_not_called()

    def test_untyped_exception_is_never_promoted_by_english_text(self):
        p = error_payload(RuntimeError("cursor changed; nothing sent"))
        self.assertEqual(p["delivery"], "unknown")
        self.assertFalse(is_prewrite_error(p))

    def test_unrecognized_or_malformed_payload_stays_unknown(self):
        for p in ({"error":"no", "delivery":"not_sent"},
                  {"error":"no", "error_schema":1, "error_code":"invented", "delivery":"not_sent"},
                  {"error":"no", "error_schema":True, "error_code":"cursor_changed", "delivery":"not_sent"},
                  {"error":"no", "error_schema":1, "error_code":"cursor_changed", "delivery":"unknown"}):
            self.assertFalse(is_prewrite_error(p))
            self.assertEqual(BrokerRequestError(p).delivery, "unknown")

    async def test_drain_failure_remains_unknown_after_a_write(self):
        writer = self.s._writer
        self.s._writer.drain.side_effect = OSError("synthetic transport failure")
        self.s._writer.wait_closed = AsyncMock()
        receipt = await self.s.send("look", **self.guard)
        self.assertEqual(receipt["delivery"], "unknown")
        self.assertFalse(receipt["command_complete"])
        writer.write.assert_called_once()


if __name__ == "__main__":
    unittest.main()
