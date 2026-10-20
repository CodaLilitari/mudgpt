import json
import unittest

from mudgpt.observations import join_text_events, omit_contact_lines, render


class ObservationViews(unittest.TestCase):
    def test_join_split_contact_line_preserves_following_color_and_unicode_offsets(self):
        events = [
            {'kind': 'text', 'text': 'Room \U0001f319\nEma',
             'style_spans': [{'start': 0, 'end': 6, 'fg': 'green'}]},
            {'kind': 'text', 'text': 'il: private@example.test\nA red door',
             'style_spans': [{'start': 0, 'end': 23, 'fg': 'yellow'},
                             {'start': 25, 'end': 35, 'fg': 'red', 'bold': True}]},
        ]
        joined = join_text_events(events)
        result = omit_contact_lines(joined)
        self.assertEqual(result['text'], 'Room \U0001f319\n[Private contact field omitted]\nA red door')
        self.assertNotIn('private@example.test', json.dumps(result))
        final = result['style_spans'][-1]
        self.assertEqual(result['text'][final['start']:final['end']], 'A red door')
        self.assertEqual(final['fg'], 'red')
        result['style_spans'][0]['fg'] = 'blue'
        self.assertEqual(events[0]['style_spans'][0]['fg'], 'green')

    def test_plain_ansi_and_named_color_views(self):
        document = {'text': 'dark red blue plain', 'style_spans': [
            {'start': 0, 'end': 4, 'fg': 'black', 'bold': True},
            {'start': 5, 'end': 8, 'fg': 'ansi256:196'},
            {'start': 9, 'end': 13, 'fg': '#0000ff', 'underline': True},
        ]}
        self.assertEqual(render(document), document['text'])
        annotated = render(document, 'annotated')
        self.assertIn('[fg=black,bold]dark[/style]', annotated)
        self.assertIn('[fg=ansi256:196]red[/style]', annotated)
        ansi = render(document, 'ansi')
        self.assertIn('\x1b[1;30mdark\x1b[0m', ansi)
        self.assertIn('\x1b[38;5;196mred\x1b[0m', ansi)
        self.assertIn('\x1b[4;38;2;0;0;255mblue\x1b[0m', ansi)
        self.assertTrue(ansi.endswith('\x1b[0m'))

    def test_renderer_does_not_replay_terminal_controls_or_untrusted_style_values(self):
        document = {'text': '\x1b]52;c;clipboard\x07word', 'style_spans': [
            {'start': 0, 'end': 4, 'fg': '\x1b[2J'},
        ]}
        result = render(document, 'ansi')
        self.assertNotIn('\x1b]', result)
        self.assertNotIn('\x07', result)
        self.assertNotIn('\x1b[2J', result)
        with self.assertRaises(ValueError):
            render({'text': 'x', 'style_spans': [{'start': 0, 'end': 2}]}, 'ansi')


if __name__ == '__main__':
    unittest.main()
