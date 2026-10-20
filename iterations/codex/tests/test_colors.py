"""Synthetic terminal streams; no network or private records."""
import unittest

from mudgpt.colors import ANSIStyleFilter


def collect(chunks):
    parser = ANSIStyleFilter()
    text, styles = [], []
    for chunk in chunks:
        result = parser.feed_styled(chunk)
        text.append(result.text)
        styles.extend(style.attributes() for style in result.styles)
        for span in result.style_spans:
            assert 0 <= span["start"] < span["end"] <= len(result.text)
            assert "text" not in span
    return "".join(text), styles


class ColorTests(unittest.TestCase):
    def test_every_split_preserves_styles_and_unicode_offsets(self):
        wire = "p\x1b[31;1mR🙂\x1b[22;44mé\x1b[39mC\x1b[49mD"
        expected = ("pR🙂éCD", [{}, {"fg": "red", "bold": True}, {"fg": "red", "bold": True},
                                  {"fg": "red", "bg": "blue"}, {"bg": "blue"}, {}])
        for cut in range(len(wire) + 1):
            with self.subTest(cut=cut):
                self.assertEqual(collect([wire[:cut], wire[cut:]]), expected)
        self.assertEqual(collect(list(wire)), expected)
        result = ANSIStyleFilter().feed_styled("é\x1b[31m🙂z\x1b[0m!")
        self.assertEqual(result.style_spans, [{"start": 1, "end": 3, "fg": "red"}])

    def test_sixteen_foreground_and_background_colors(self):
        names = ["black", "red", "green", "yellow", "blue", "magenta", "cyan", "white"]
        for number, name in enumerate(names):
            for bright, offset in ((False, 0), (True, 60)):
                expected_name = "bright_" + name if bright else name
                text, styles = collect([f"\x1b[{30 + number + offset};{40 + number + offset}mX"])
                self.assertEqual(text, "X")
                self.assertEqual(styles, [{"fg": expected_name, "bg": expected_name}])

    def test_indexed_rgb_and_colon_variants(self):
        wire = "\x1b[38;5;196;48;2;1;2;255mA\x1b[38:2::16:32:48mB\x1b[48:5:27mC"
        expected = ("ABC", [{"fg": "ansi256:196", "bg": "#0102ff"},
                             {"fg": "#102030", "bg": "#0102ff"},
                             {"fg": "#102030", "bg": "ansi256:27"}])
        for cut in range(len(wire) + 1):
            self.assertEqual(collect([wire[:cut], wire[cut:]]), expected)
        self.assertEqual(collect(["\x1b[31mA\x1b[38;2;999;0;0mB"]),
                         ("AB", [{"fg": "red"}, {"fg": "red"}]))

    def test_attribute_and_full_resets(self):
        wire = "\x1b[1;2;3;4;5;7;8;9mA\x1b[22;23;24;25;27;28;29mB\x1b[32;45mC\x1b[mD"
        self.assertEqual(collect([wire]), ("ABCD", [
            {"bold": True, "faint": True, "italic": True, "underline": True, "blink": True,
             "inverse": True, "conceal": True, "strike": True}, {},
            {"fg": "green", "bg": "magenta"}, {}]))

    def test_ignored_controls_literal_braces_and_newlines(self):
        wire = ("\x1b[32mA\x1b]8;;https://invalid.example/secret\x1b\\B"
                "\x1bPignored\x1b\\\x1b[2J\x1b[9;9HC\x1b(0D"
                "\r\nX\n\rY {Dliteral{x\x00")
        expected_text = "ABCD\nX\nY {Dliteral{x"
        for cut in range(len(wire) + 1):
            text, styles = collect([wire[:cut], wire[cut:]])
            self.assertEqual(text, expected_text)
            self.assertEqual(styles, [{"fg": "green"}] * len(text))
        self.assertEqual(collect(["\x9b31mR\x9dhidden\x9c\x9b0mZ"]), ("RZ", [{"fg": "red"}, {}]))

    def test_bell_terminates_osc_but_not_other_control_strings(self):
        for introducer in ("\x1bP", "\x1bX", "\x1b^", "\x1b_", "\x90", "\x98", "\x9e", "\x9f"):
            wire = "A" + introducer + "hidden\x07still-hidden\x1b\\B"
            for cut in range(len(wire) + 1):
                self.assertEqual(collect([wire[:cut], wire[cut:]]), ("AB", [{}, {}]))
        self.assertEqual(collect(["A\x1b]hidden\x07B"]), ("AB", [{}, {}]))

    def test_unterminated_controls_have_bounded_storage_and_recover(self):
        parser = ANSIStyleFilter()
        self.assertEqual(parser.feed("\x1b[" + "1;" * 100000), "")
        self.assertLessEqual(len(parser._params), parser.MAX_CSI_LENGTH)
        self.assertEqual(parser.feed_styled("mVisible").style_spans, [])
        self.assertEqual(parser.feed("\x1b]" + "hidden" * 100000), "")
        self.assertEqual(parser.feed("\x1b\\Done"), "Done")
        self.assertEqual(parser._params, "")


if __name__ == "__main__":
    unittest.main()
