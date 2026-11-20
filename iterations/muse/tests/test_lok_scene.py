"""Synthetic LoK-shaped observations; no private world text or live service."""
import copy
import unittest

try:
    from mudgpt.lok_scene import scene_from_observation
except ModuleNotFoundError:
    scene_from_observation = None


def observation(*pieces, truncated=False):
    events = []
    for index, piece in enumerate(pieces, 1):
        if isinstance(piece, str):
            piece = {"text": piece, "style_spans": []}
        events.append({"kind": "text", "seq": index, "generation": 7, **piece})
    return {"broker_id": "synthetic-lifetime", "generation": 7,
            "cursor": len(events), "oldest": 1, "truncated": truncated, "events": events}


class SceneTests(unittest.TestCase):
    def scene(self, value, **kwargs):
        self.assertIsNotNone(scene_from_observation, "The planned evidence parser is not implemented yet")
        return scene_from_observation(value, **kwargs)

    def test_split_combat_prompt_preserves_resources_and_color_evidence(self):
        text = "A clockwork form is in perfect health.\n<91/120hp 22/80m 40/55mv 7xp Enemy: 100%foe> "
        value = observation({"text": text[:31], "style_spans": [{"start": 2, "end": 16, "fg": "cyan"}]}, text[31:])
        original = copy.deepcopy(value)
        scene = self.scene(value)
        self.assertEqual(scene["prompt"]["kind"], "combat")
        self.assertTrue(scene["prompt"]["complete"])
        self.assertEqual(scene["resources"], {"hp": {"current": 91, "maximum": 120},
                                            "mana": {"current": 22, "maximum": 80},
                                            "move": {"current": 40, "maximum": 55}})
        self.assertEqual(scene["enemy_status"], "A clockwork form is in perfect health.")
        self.assertEqual(scene["text"], text)
        self.assertEqual(scene["style_spans"], [{"start": 2, "end": 16, "fg": "cyan"}])
        evidence = scene["prompt"]["evidence"]
        self.assertEqual(scene["text"][evidence["start"]:evidence["end"]], evidence["text"])
        self.assertEqual(value, original)

    def test_resource_prompt_without_maxima_keeps_maxima_unknown(self):
        scene = self.scene(observation("<11hp 12m 13mv> "))
        self.assertEqual(scene["prompt"]["kind"], "resources")
        self.assertEqual(scene["resources"]["hp"], {"current": 11, "maximum": None})
        self.assertIsNone(scene["enemy_status"])

    def test_afk_is_not_automatically_combat(self):
        scene = self.scene(observation("<AFK> "))
        self.assertEqual(scene["prompt"]["kind"], "afk")
        self.assertIsNone(scene["afk_combat_exchange"])
        self.assertEqual(scene["resources"], {"hp": None, "mana": None, "move": None})

    def test_fresh_afk_exchange_has_named_evidence(self):
        scene = self.scene(observation(
            "A clockwork form is unaffected by your pierce!\n"
            "You block a clockwork form's attack with your shield.\n"
            "A clockwork form is in perfect health.\n<AFK> "))
        self.assertEqual(scene["afk_combat_exchange"]["enemy"], "A clockwork form")
        self.assertIn("unaffected", scene["afk_combat_exchange"]["outgoing"]["text"])
        self.assertIn("block", scene["afk_combat_exchange"]["incoming"]["text"])

    def test_displayed_room_and_closed_exit_are_evidence_not_invented_routes(self):
        scene = self.scene(observation(
            "Test Chamber [Room 1201]\nAn invented empty chamber.\n[Exits: north (east) up]\n<10hp 20m 30mv> "))
        self.assertEqual(scene["room"]["id"], 1201)
        self.assertEqual(scene["room"]["title"], "Test Chamber")
        self.assertEqual([(item["direction"], item["closed"]) for item in scene["exits"]],
                         [("north", False), ("east", True), ("up", False)])
        self.assertNotIn("south", [item["direction"] for item in scene["exits"]])
        self.assertEqual(scene["room"]["freshness"], "observed_in_window")

    def test_partial_new_prompt_does_not_reuse_an_old_complete_prompt(self):
        scene = self.scene(observation("<10hp 20m 30mv> \nA clockwork form is in perfect health.\n<9/10hp 2"))
        self.assertFalse(scene["prompt"]["complete"])
        self.assertEqual(scene["prompt"]["kind"], "unknown")
        self.assertEqual(scene["resources"], {"hp": None, "mana": None, "move": None})

    def test_pager_and_editor_are_not_combat_prompts(self):
        for text, kind in (("[Hit Return to continue]", "pager"),
                           ("Description editor: finish with ~.", "editor")):
            with self.subTest(kind=kind):
                self.assertEqual(self.scene(observation(text))["prompt"]["kind"], kind)

    def test_empty_window_does_not_invent_a_room_or_resources(self):
        scene = self.scene(observation())
        self.assertIsNone(scene["room"])
        self.assertIsNone(scene["exits"])
        self.assertIsNone(scene["enemy_status"])
        self.assertFalse(scene["prompt"]["complete"])

    def test_window_bound_clips_spans_without_losing_source_offset(self):
        text = "x" * 200 + "\n<10hp 20m 30mv> "
        value = observation({"text": text, "style_spans": [{"start": 190, "end": 205, "fg": "green"}]})
        scene = self.scene(value, max_chars=64)
        offset = len(text) - 64
        self.assertEqual(len(scene["text"]), 64)
        self.assertEqual(scene["source"]["text_start"], offset)
        self.assertTrue(scene["source"]["text_truncated"])
        self.assertEqual(scene["style_spans"][0]["start"], 190 - offset)
        self.assertEqual(scene["source"]["broker_id"], "synthetic-lifetime")

    def test_retained_history_truncation_is_exposed(self):
        self.assertTrue(self.scene(observation("<1hp 2m 3mv>", truncated=True))["source"]["truncated"])

    def test_clipping_inside_spaces_does_not_promote_a_partial_enemy_line(self):
        text = ("An unfinished display prefix" + " " * 200
                + "A clockwork form is in perfect health.\n<10hp 20m 30mv Enemy: 100%foe> ")
        scene = self.scene(observation(text), max_chars=128)
        self.assertTrue(scene["source"]["text_truncated"])
        self.assertEqual(scene["prompt"]["kind"], "combat")
        self.assertIsNone(scene["enemy_status"])


if __name__ == "__main__":
    unittest.main()
