"""In-process Muse reliability checks; no sockets, game, or private fixtures."""
import json
import sqlite3
import time
from unittest.mock import patch

from muse.adapter import Adapter, Journal, LocalBroker, UpstreamError
from test_adapter import AdapterFixture


class WireReplyBroker(LocalBroker):
    """Exercise LocalBroker's real decoder with only the HTTP wire replaced."""
    def __init__(self, baseline, reply, status=400):
        self.baseline, self.reply, self.status = baseline, reply, status

    def configuration(self):
        return self.baseline.configuration()

    def call(self, config, payload):
        if payload["op"] not in {"send", "conditional"}:
            return self.baseline.call(config, payload)
        self.baseline.calls.append(dict(payload))
        wire = self.reply if isinstance(self.reply, bytes) else json.dumps(self.reply).encode()
        status = self.status

        class Response:
            def __init__(self):
                self.status = status

            def read(self, size):
                return wire[:size]

        class Connection:
            sock = None

            def connect(self):
                pass

            def request(self, *args, **kwargs):
                pass

            def getresponse(self):
                return Response()

            def close(self):
                pass

        with patch("muse.adapter.http.client.HTTPConnection", return_value=Connection()):
            return super().call(config, payload)


class ReliabilityTests(AdapterFixture):
    def conditional(self, **changes):
        data = self.command()
        del data["command"]
        data.update(op="conditional", action="lok_retreat_v1", expected_lease_epoch=3,
                    condition={"enemy_status": "A practice sentinel is in perfect health.",
                               "allow_afk_combat": False})
        data.update(changes)
        return data

    def enable_conditional(self):
        self.broker.capabilities.append("conditional_actions_v1")
        self.claim()

    def test_versioned_prewrite_refusal_preserves_code_and_durable_not_sent(self):
        self.claim()
        error = {"error_schema": 1, "error_code": "cursor_changed", "delivery": "not_sent",
                 "error": "sensitive diagnostic must not be reflected"}
        self.adapter.broker = WireReplyBroker(self.broker, error)
        packet = self.command()
        code, response = self.call(packet)
        self.assertEqual(code, 409)
        self.assertEqual(response["receipt"]["outcome"], "not_sent")
        self.assertEqual(response["receipt"]["delivery"], "not_sent")
        self.assertEqual(response["receipt"]["error_code"], "cursor_changed")
        self.assertFalse(response["receipt"]["command_complete"])
        self.assertNotIn("sensitive diagnostic", json.dumps(response))
        code, duplicate = self.call(packet)
        self.assertEqual(code, 409)
        self.assertEqual(duplicate["receipt"], response["receipt"])
        self.assertEqual(sum(p["op"] == "send" for p in self.broker.calls), 1)

    def test_unversioned_malformed_or_unknown_error_stays_uncertain(self):
        self.claim()
        typed = {"error_schema": 1, "error_code": "cursor_changed", "delivery": "not_sent",
                 "error": "cursor changed before write; no command sent"}
        replies = [
            {"error": typed["error"]},
            {**typed, "error_schema": True},
            {**typed, "error_code": "future_unknown_code"},
            {**typed, "delivery": "unknown"},
            {**typed, "error": {"message": "refused"}},
            {key: value for key, value in typed.items() if key != "delivery"},
            b'{"error_schema":1,"error_schema":1,"error_code":"cursor_changed",'
            b'"delivery":"not_sent","error":"duplicate field"}',
            b'{"error":',
        ]
        for reply in replies:
            with self.subTest(reply=reply):
                self.adapter.broker = WireReplyBroker(self.broker, reply)
                packet = self.command()
                code, response = self.call(packet)
                self.assertEqual(code, 502)
                self.assertEqual(response["receipt"]["outcome"], "uncertain")
                self.assertNotIn("error_code", response["receipt"])
                self.assertEqual(self.call(packet)[1]["receipt"], response["receipt"])

    def test_conditional_keeps_original_decision_and_only_local_owner_and_lease(self):
        self.enable_conditional()
        packet = self.conditional()
        code, response = self.call(packet)
        self.assertEqual(code, 200)
        sent = self.broker.calls[-1]
        self.assertEqual(sent["op"], "conditional")
        self.assertEqual(sent["decision_id"], packet["command_id"])
        self.assertEqual(sent["owner"], "nua")
        self.assertEqual(sent["lease"], "private-lease-not-for-remote")
        for field in ("action", "condition", "expected_broker_id", "expected_generation",
                      "expected_cursor", "expected_lease_epoch", "expires_at"):
            self.assertEqual(sent[field], packet[field])
        self.assertNotIn("command", sent)
        self.assertNotIn("command_id", sent)
        self.assertNotIn("private-lease", json.dumps(response))
        self.assertEqual(response["receipt"]["outcome"], "broker_response")
        self.assertFalse(response["receipt"]["command_complete"])

    def test_conditional_reserves_before_forwarding_and_duplicate_never_repeats(self):
        self.enable_conditional()
        packet = self.conditional()
        original_call = self.broker.call

        def check_reserved(config, payload):
            if payload["op"] == "conditional":
                independent = Journal(self.root / "journal.sqlite")
                try:
                    self.assertEqual(independent.reserve(packet["command_id"], packet)["outcome"],
                                     "uncertain")
                finally:
                    independent.close()
            return original_call(config, payload)

        with patch.object(self.broker, "call", side_effect=check_reserved):
            code, first = self.call(packet)
        self.assertEqual(code, 200)
        code, again = self.call(packet)
        self.assertEqual(code, 409)
        self.assertEqual(again["receipt"], first["receipt"])
        self.assertEqual(sum(p["op"] == "conditional" for p in self.broker.calls), 1)

    def test_conditional_unknown_is_durable_across_adapter_restart(self):
        self.enable_conditional()
        packet = self.conditional()
        original_call = self.broker.call

        def lost_response(config, payload):
            result = original_call(config, payload)
            if payload["op"] == "conditional":
                raise UpstreamError("synthetic lost response")
            return result

        with patch.object(self.broker, "call", side_effect=lost_response):
            code, response = self.call(packet)
        self.assertEqual(code, 502)
        self.assertEqual(response["receipt"]["outcome"], "uncertain")
        other_journal = Journal(self.root / "journal.sqlite")
        try:
            restarted = Adapter(self.root / "control.json", self.credential, other_journal,
                                broker=self.broker)
            packet["expected_adapter_id"] = restarted.adapter_id
            code, again = restarted.process(packet, self.auth)
            self.assertEqual(code, 409)
            self.assertEqual(again["receipt"], response["receipt"])
        finally:
            other_journal.close()
        self.assertEqual(sum(p["op"] == "conditional" for p in self.broker.calls), 1)

    def test_conditional_requires_capability_and_owned_lease(self):
        self.claim()
        code, response = self.call(self.conditional())
        self.assertEqual(code, 409)
        self.assertEqual(response["receipt"]["outcome"], "not_forwarded")
        self.broker.capabilities.append("conditional_actions_v1")
        self.adapter.lease = None
        code, response = self.call(self.conditional())
        self.assertEqual(code, 409)
        self.assertEqual(response["receipt"]["outcome"], "not_forwarded")
        self.assertFalse(any(p["op"] == "conditional" for p in self.broker.calls))

    def test_conditional_rejects_widened_action_malformed_predicate_and_expired_guards(self):
        self.enable_conditional()
        packets = [self.conditional(**{key: value}) for key, value in (
            ("action", "arbitrary_command"), ("command", "flee"), ("owner", "human"),
            ("lease", "remote-lease"), ("expected_lease_epoch", True),
            ("expected_lease_epoch", -1), ("expected_cursor", True),
            ("expected_generation", True), ("expires_at", time.time() - 1),
            ("expires_at", time.time() + 60), ("expires_at", float("nan")),
            ("condition", {"enemy_status": "A sentinel", "allow_afk_combat": 1}),
            ("condition", {"enemy_status": "A sentinel\n<AFK>", "allow_afk_combat": True}),
            ("condition", {"enemy_status": "A sentinel"}),
            ("condition", {"enemy_status": "A sentinel", "allow_afk_combat": False, "command": "quit"}),
        )]
        missing_epoch = self.conditional()
        del missing_epoch["expected_lease_epoch"]
        packets.append(missing_epoch)
        self.broker.calls.clear()
        for packet in packets:
            with self.subTest(packet=packet):
                self.assertEqual(self.call(packet)[0], 400)
        self.assertEqual(self.broker.calls, [])

    def test_conditional_failed_reservation_never_reaches_broker(self):
        self.enable_conditional()
        self.broker.calls.clear()
        with patch.object(self.journal, "reserve", side_effect=sqlite3.OperationalError("fixture disk")):
            self.assertEqual(self.call(self.conditional())[0], 502)
        self.assertEqual(self.broker.calls, [])

    def test_conditional_preserves_normal_predicate_result_and_intervening_color(self):
        self.enable_conditional()
        original_call = self.broker.call
        evidence = {"events": [self.broker.event], "truncated": False}

        def predicate_refused(config, payload):
            result = original_call(config, payload)
            if payload["op"] == "conditional":
                result.update(delivery="not_sent", send_attempted=False,
                              reason_code="condition_not_met", intervening_observation=evidence)
            return result

        with patch.object(self.broker, "call", side_effect=predicate_refused):
            code, response = self.call(self.conditional())
        self.assertEqual(code, 200)
        self.assertEqual(response["result"]["delivery"], "not_sent")
        self.assertEqual(response["result"]["intervening_observation"], evidence)
        self.assertEqual(response["receipt"]["outcome"], "broker_response")

    def test_send_and_conditional_share_command_id_replay_barrier(self):
        self.enable_conditional()
        packet = self.command()
        code, first = self.call(packet)
        self.assertEqual(code, 200)
        code, again = self.call(self.conditional(command_id=packet["command_id"]))
        self.assertEqual(code, 409)
        self.assertEqual(again["receipt"], first["receipt"])
        self.assertFalse(any(p["op"] == "conditional" for p in self.broker.calls))
