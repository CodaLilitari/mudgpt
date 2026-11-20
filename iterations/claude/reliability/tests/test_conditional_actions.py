"""Real Session + durable SQLite with an in-memory writer; no game/network."""
import asyncio
from contextlib import closing
import copy
import json
from pathlib import Path
import sqlite3
import tempfile
import time
import unittest
from unittest.mock import patch

from mudgpt.session import Session

try:
    from mudgpt.conditional_actions import send_conditional
except ModuleNotFoundError:
    send_conditional = None


ENEMY = "A clockwork form is in perfect health."
COMBAT = ENEMY + "\n<91/120hp 22/80m 40/55mv 7xp Enemy: 100%foe> "
AFK_COMBAT = ("A clockwork form is unaffected by your pierce!\n"
              "A clockwork form's slash misses you. [0]\n" + ENEMY + "\n<AFK> ")


class RecordingWriter:
    """Replace only the external wire; Session state/guards/writes remain real."""
    def __init__(self, session):
        self.session = session
        self.writes = []
        self.failure = None

    def write(self, value):
        self.writes.append(value)
        if self.failure is None:
            self.session._text("You retreat from the exercise.\n<91hp 22m 40mv> ")

    async def drain(self):
        if self.failure is not None:
            raise self.failure

    def close(self):
        pass

    async def wait_closed(self):
        pass


class ConditionalTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.journal = Path(self.tmp.name) / "decisions.sqlite3"
        self.session = Session(prompt_pattern=r"<[^>\r\n]+>")
        self.session._connected = True
        self.session._generation = 7
        self.writer = RecordingWriter(self.session)
        self.session._writer = self.writer
        self.session.release("human")
        claimed = self.session.claim("fixture_actor")
        self.session._text(COMBAT)
        observed = self.session.observe()
        self.request = {
            "op": "conditional", "action": "lok_retreat_v1", "decision_id": "fixture-decision-001",
            "owner": "fixture_actor", "lease": claimed["lease"],
            "expected_broker_id": observed["broker_id"], "expected_generation": 7,
            "expected_cursor": observed["cursor"], "expected_lease_epoch": claimed["lease_epoch"],
            "expires_at": time.time() + 60,
            "condition": {"enemy_status": ENEMY, "allow_afk_combat": False},
        }

    async def invoke(self, request=None):
        self.assertIsNotNone(send_conditional, "The planned conditional action is not implemented yet")
        return await send_conditional(self.session, request or self.request, self.journal)

    def refusal(self, result, code=None):
        self.assertEqual(result["delivery"], "not_sent")
        self.assertFalse(result["send_attempted"])
        if code:
            self.assertEqual(result["reason_code"], code)
        self.assertEqual(self.writer.writes, [])

    async def test_ambient_combat_does_not_block_the_explicit_conditional_flee(self):
        original = copy.deepcopy(self.request)
        self.session._text("\n" + COMBAT)
        evaluation_cursor = self.session.status()["cursor"]
        result = await self.invoke()
        self.assertEqual(self.writer.writes, ["flee\r\n"])
        self.assertEqual(result["delivery"], "transport_accepted")
        self.assertFalse(result["command_complete"])
        self.assertEqual(result["evaluation_cursor"], evaluation_cursor)
        self.assertEqual(result["original_decision"]["expected_cursor"], original["expected_cursor"])
        self.assertEqual(result["original_decision"]["expires_at"], original["expires_at"])
        self.assertEqual(result["original_decision"]["expected_lease_epoch"], original["expected_lease_epoch"])
        self.assertEqual(result["predicate_evidence"]["enemy_status"], ENEMY)
        self.assertTrue(result["intervening_observation"]["events"])
        self.assertEqual(self.request, original)

    async def test_current_prompt_at_original_cursor_is_sufficient_without_new_ticks(self):
        result = await self.invoke()
        self.assertEqual(self.writer.writes, ["flee\r\n"])
        self.assertEqual(result["intervening_observation"]["events"], [])

    async def test_prompt_concatenated_enemy_line_is_not_guessed_as_complete_evidence(self):
        self.session._text(COMBAT)
        self.refusal(await self.invoke(), "condition_not_met")

    async def test_strict_send_still_rejects_a_stale_cursor(self):
        self.session._text(COMBAT)
        with self.assertRaises(RuntimeError):
            await self.session.send("flee", owner=self.request["owner"], lease=self.request["lease"],
                expected_broker_id=self.request["expected_broker_id"], expected_generation=7,
                expected_cursor=self.request["expected_cursor"], expires_at=self.request["expires_at"])
        self.assertEqual(self.writer.writes, [])

    async def test_afk_exchange_requires_explicit_opt_in(self):
        self.session._text("\n" + AFK_COMBAT)
        refused = await self.invoke()
        self.refusal(refused, "condition_not_met")
        allowed = copy.deepcopy(self.request)
        allowed["decision_id"] = "fixture-decision-002"
        allowed["condition"]["allow_afk_combat"] = True
        result = await self.invoke(allowed)
        self.assertEqual(result["delivery"], "transport_accepted")
        self.assertEqual(self.writer.writes, ["flee\r\n"])

    async def test_bare_afk_or_changed_enemy_cannot_authorize_flee(self):
        self.request["condition"]["allow_afk_combat"] = True
        for index, text in enumerate((ENEMY + "\n<AFK> ",
                "A painted cube is in perfect health.\n<91hp 22m 40mv Enemy: 100%foe> ")):
            self.request["decision_id"] = f"refuse-prompt-{index}"
            self.session._text("\n" + text)
            self.refusal(await self.invoke(), "condition_not_met")

    async def test_pager_editor_and_partial_tail_are_not_actionable(self):
        cases = (("[Hit Return to continue]", "intervening_context_changed"),
                 ("Description editor: finish with ~.", "intervening_context_changed"),
                 ("<91/120hp 2", "condition_not_met"))
        for index, (text, code) in enumerate(cases):
            self.request["decision_id"] = f"refuse-input-mode-{index}"
            self.request["expected_cursor"] = self.session.status()["cursor"]
            self.session._text("\n" + text)
            self.refusal(await self.invoke(), code)

    async def test_room_change_and_intervening_command_are_refused(self):
        self.session._text("\nAnother Test Chamber [Room 1202]\n" + COMBAT)
        self.refusal(await self.invoke(), "intervening_context_changed")
        self.request["decision_id"] = "intervening-command"
        self.request["expected_cursor"] = self.session.status()["cursor"]
        self.session._add("send_attempt", command_id="synthetic-other-command")
        self.refusal(await self.invoke(), "intervening_context_changed")

    async def test_connection_owner_epoch_and_expiry_mismatches_write_nothing(self):
        cases = [("expected_broker_id", "other-lifetime", "broker_changed"),
                 ("expected_generation", 8, "generation_changed"),
                 ("expected_lease_epoch", 999, "lease_epoch_changed"),
                 ("owner", "another_actor", "controller_mismatch"),
                 ("lease", "not-the-claim", "lease_mismatch"),
                 ("expires_at", time.time() - 1, "decision_expired")]
        for index, (field, value, code) in enumerate(cases):
            changed = copy.deepcopy(self.request)
            changed["decision_id"] = f"different-context-{index}"
            changed[field] = value
            self.refusal(await self.invoke(changed), code)

    async def test_truncated_original_interval_is_not_ignored(self):
        for _ in range(600):
            self.session._text("synthetic ambient\n")
        self.session._text(COMBAT)
        self.refusal(await self.invoke(), "observation_truncated")

    async def test_duplicate_returns_receipt_without_a_second_write(self):
        first = await self.invoke()
        second = await self.invoke()
        self.assertTrue(second["duplicate"])
        self.assertEqual(second["command_id"], first["command_id"])
        self.assertEqual(second["delivery"], "transport_accepted")
        self.assertEqual(self.writer.writes, ["flee\r\n"])

    async def test_reused_id_with_changed_authored_condition_conflicts(self):
        await self.invoke()
        changed = copy.deepcopy(self.request)
        changed["condition"]["enemy_status"] = "A painted cube is in perfect health."
        result = await self.invoke(changed)
        self.assertEqual(result["delivery"], "not_sent")
        self.assertEqual(result["reason_code"], "decision_conflict")
        self.assertEqual(self.writer.writes, ["flee\r\n"])

    async def test_uncertain_write_and_duplicate_never_retry(self):
        self.writer.failure = OSError("synthetic wire ended after write")
        first = await self.invoke()
        self.assertEqual(first["delivery"], "unknown")
        self.assertTrue(first["send_attempted"])
        second = await self.invoke()
        self.assertTrue(second["duplicate"])
        self.assertEqual(second["delivery"], "unknown")
        self.assertEqual(self.writer.writes, ["flee\r\n"])

    async def test_cancelled_pending_decision_is_not_resumed(self):
        self.writer.failure = asyncio.CancelledError()
        with self.assertRaises(asyncio.CancelledError):
            await self.invoke()
        second = await self.invoke()
        self.assertTrue(second["duplicate"])
        self.assertEqual(second["delivery"], "unknown")
        self.assertEqual(self.writer.writes, ["flee\r\n"])

    async def test_persisted_authored_packet_excludes_lease_and_binds_epoch(self):
        result = await self.invoke()
        with closing(sqlite3.connect(self.journal)) as database:
            authored, receipt = database.execute("SELECT decision, receipt FROM relay_decisions").fetchone()
        self.assertNotIn(self.request["lease"], authored + receipt + json.dumps(result))
        saved = json.loads(authored)
        self.assertEqual(saved["owner"], "fixture_actor")
        self.assertEqual(saved["expected_lease_epoch"], self.request["expected_lease_epoch"])

    async def test_expiry_is_rechecked_after_evidence_evaluation(self):
        self.assertIsNotNone(send_conditional, "The planned conditional action is not implemented yet")
        from mudgpt import conditional_actions
        real_parser = conditional_actions.scene_from_observation
        clock = [1000.0]
        self.request["expires_at"] = 1001.0
        def late_scene(value):
            result = real_parser(value)
            clock[0] = 1002.0
            return result
        with patch("mudgpt.conditional_actions.scene_from_observation", side_effect=late_scene), \
                patch("mudgpt.conditional_actions.time.time", side_effect=lambda: clock[0]):
            self.refusal(await self.invoke(), "decision_expired")

    async def test_unknown_action_and_arbitrary_command_field_are_refused(self):
        for changes in ({"action": "arbitrary_send"}, {"command": "north"}, {"expected_lease_epoch": True}):
            changed = {**self.request, **changes}
            self.refusal(await self.invoke(changed), "invalid_request")

    async def test_checkpoint_failure_before_dispatch_is_definitely_not_sent(self):
        self.assertIsNotNone(send_conditional, "The planned conditional action is not implemented yet")
        with patch("mudgpt.conditional_actions.DecisionJournal.save", side_effect=OSError("synthetic checkpoint unavailable")):
            result = await self.invoke()
        self.refusal(result)
        self.assertIn("journal_or_evidence_error", result)

    async def test_receipt_write_failure_after_dispatch_preserves_acceptance(self):
        self.assertIsNotNone(send_conditional, "The planned conditional action is not implemented yet")
        from mudgpt.conditional_actions import DecisionJournal
        original_save = DecisionJournal.save
        writes = [0]
        def fail_second(journal, decision_id, receipt):
            writes[0] += 1
            if writes[0] == 2:
                raise OSError("synthetic final receipt unavailable")
            return original_save(journal, decision_id, receipt)
        with patch("mudgpt.conditional_actions.DecisionJournal.save", new=fail_second):
            result = await self.invoke()
        self.assertEqual(result["delivery"], "transport_accepted")
        self.assertTrue(result["send_attempted"])
        self.assertIn("journal_or_evidence_error", result)
        duplicate = await self.invoke()
        self.assertTrue(duplicate["duplicate"])
        self.assertEqual(duplicate["delivery"], "unknown")
        self.assertEqual(self.writer.writes, ["flee\r\n"])


if __name__ == "__main__":
    unittest.main()
