"""Release contracts across the real local broker and a synthetic game only."""
import time
import unittest
from test_broker import BrokerHarness


class ReleaseIntegrationTests(unittest.TestCase):
    def test_observe_preserves_epoch_and_conditional_capability(self):
        h = BrokerHarness()
        try:
            status = h.call(op="status")
            self.assertIn("conditional_actions_v1", status["capabilities"])
            obs = h.call(op="observe")
            self.assertEqual(obs["lease_epoch"], status["lease_epoch"])
            scene = h.call(op="scene", after=0)
            self.assertIn("scene", scene)
            self.assertEqual(scene["cursor"], obs["cursor"])
        finally:
            h.close()

    def test_conditional_refusal_is_durable_across_released_controller(self):
        h = BrokerHarness()
        try:
            h.connect()
            h.call(op="release", owner="human")
            claim = h.call(op="claim", owner="fixture-actor")
            obs = h.call(op="observe")
            packet = dict(op="conditional", action="lok_retreat_v1", decision_id="synthetic-decision-one",
                          owner="fixture-actor", lease=claim["lease"],
                          expected_broker_id=obs["broker_id"], expected_generation=obs["generation"],
                          expected_cursor=obs["cursor"], expected_lease_epoch=claim["lease_epoch"],
                          expires_at=time.time()+15, condition={"enemy_status":"A training dummy is in perfect health.","allow_afk_combat":False})
            first = h.call(**packet)
            self.assertFalse(first["send_attempted"])
            self.assertEqual(first["delivery"], "not_sent")
            h.call(op="release", owner="fixture-actor", lease=claim["lease"])
            again = h.call(**packet)
            self.assertTrue(again.pop("duplicate"))
            self.assertFalse(again.pop("new_send"))
            self.assertEqual(first, again)
            self.assertEqual(h.fake.commands, [])
        finally:
            h.close()
