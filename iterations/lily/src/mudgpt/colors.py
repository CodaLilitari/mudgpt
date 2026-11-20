"""Incremental, non-executing ANSI SGR interpretation for safe text observations.

Spans are half-open Unicode code-point offsets into the associated plain text.
Only nondefault attributes are included; gaps have terminal-default styling.
Colors are sixteen named ANSI colors, ``ansi256:N``, or ``#rrggbb``. Bold is
separate from bright colors because a terminal's bold-to-bright policy varies.
No escape payload or duplicate text is included in public span metadata.
"""
from __future__ import annotations

from dataclasses import dataclass, fields, replace
import unicodedata


COLORS = ("black", "red", "green", "yellow", "blue", "magenta", "cyan", "white")


@dataclass(frozen=True, slots=True)
class Style:
    fg: str | None = None
    bg: str | None = None
    bold: bool = False
    faint: bool = False
    italic: bool = False
    underline: bool = False
    blink: bool = False
    inverse: bool = False
    conceal: bool = False
    strike: bool = False

    def attributes(self):
        return {field.name: getattr(self, field.name) for field in fields(self)
                if getattr(self, field.name) not in (False, None)}


DEFAULT_STYLE = Style()


@dataclass(frozen=True, slots=True)
class StyledText:
    text: str
    # Immutable style references per character simplify exact redaction/rebasing.
    # This internal representation is never serialized into an observation.
    styles: tuple[Style, ...]

    def __post_init__(self):
        if len(self.text) != len(self.styles):
            raise ValueError("Text and style lengths must match")

    @property
    def style_spans(self):
        spans = []
        start = 0
        while start < len(self.text):
            style = self.styles[start]
            end = start + 1
            while end < len(self.text) and self.styles[end] == style:
                end += 1
            if style != DEFAULT_STYLE:
                spans.append({"start": start, "end": end, **style.attributes()})
            start = end
        return spans


class ANSIStyleFilter:
    """Interpret only SGR; discard OSC/DCS/cursor controls without executing them.

    State and styles survive feed boundaries. A new instance resets the stream.
    Control strings retain no payload, and incomplete CSI parameter storage is
    capped at MAX_CSI_LENGTH even if a peer never terminates a sequence.
    """

    MAX_CSI_LENGTH = 256

    def __init__(self):
        self.state, self.newline = "text", ""
        self.style = DEFAULT_STYLE
        self._params = ""
        self._string_bel = False

    def feed(self, data):
        """Compatibility plain-text view; use feed_styled to retain styling."""
        return self.feed_styled(data).text

    @staticmethod
    def _number(value):
        if not value or len(value) > 8 or not value.isascii() or not value.isdecimal():
            return None
        return int(value)

    def _extended(self, target, mode, values):
        numbers = [self._number(value) for value in values]
        if any(value is None or not 0 <= value <= 255 for value in numbers):
            return
        if mode == "5" and len(numbers) == 1:
            self.style = replace(self.style, **{target: f"ansi256:{numbers[0]}"})
        elif mode == "2" and len(numbers) == 3:
            self.style = replace(self.style, **{target: "#" + "".join(f"{value:02x}" for value in numbers)})

    def _sgr(self, parameters):
        values = parameters.split(";")
        index = 0
        while index < len(values):
            raw = values[index]
            index += 1
            if ":" in raw:
                parts = raw.split(":")
                if parts[0] in ("38", "48") and len(parts) > 2:
                    target = "fg" if parts[0] == "38" else "bg"
                    color = parts[2:]
                    if parts[1] == "2" and len(color) == 4:
                        if color[0] not in ("", "0"):
                            continue
                        color = color[1:]
                    self._extended(target, parts[1], color)
                # Other colon variants are not claimed as supported styles.
                continue
            code = 0 if raw == "" else self._number(raw)
            if code == 0:
                self.style = DEFAULT_STYLE
            elif code in (38, 48):
                if index >= len(values):
                    return
                mode = values[index]
                count = 1 if mode == "5" else 3 if mode == "2" else 0
                index += 1
                if count == 0:
                    return
                self._extended("fg" if code == 38 else "bg", mode, values[index:index + count])
                index += count
            elif code is not None and 30 <= code <= 37:
                self.style = replace(self.style, fg=COLORS[code - 30])
            elif code is not None and 40 <= code <= 47:
                self.style = replace(self.style, bg=COLORS[code - 40])
            elif code is not None and 90 <= code <= 97:
                self.style = replace(self.style, fg="bright_" + COLORS[code - 90])
            elif code is not None and 100 <= code <= 107:
                self.style = replace(self.style, bg="bright_" + COLORS[code - 100])
            elif code == 39:
                self.style = replace(self.style, fg=None)
            elif code == 49:
                self.style = replace(self.style, bg=None)
            elif code == 22:
                self.style = replace(self.style, bold=False, faint=False)
            else:
                toggles = {1: ("bold", True), 2: ("faint", True), 3: ("italic", True),
                           4: ("underline", True), 5: ("blink", True), 6: ("blink", True),
                           7: ("inverse", True), 8: ("conceal", True), 9: ("strike", True),
                           23: ("italic", False), 24: ("underline", False),
                           25: ("blink", False), 27: ("inverse", False),
                           28: ("conceal", False), 29: ("strike", False)}
                if code in toggles:
                    key, enabled = toggles[code]
                    self.style = replace(self.style, **{key: enabled})

    def feed_styled(self, data):
        chars, styles = [], []
        for char in data:
            if self.state in ("string", "string_esc"):
                if (char == "\x9c" or (char == "\x07" and self._string_bel)
                        or (self.state == "string_esc" and char == "\\")):
                    self.state = "text"
                else:
                    self.state = "string_esc" if char == "\x1b" else "string"
                continue
            if char == "\x1b":
                self.state, self._params = "esc", ""
                continue
            if char == "\x9b":
                self.state, self._params = "csi", ""
                continue
            if char in ("\x90", "\x98", "\x9d", "\x9e", "\x9f"):
                self.state = "string"
                self._string_bel = char == "\x9d"
                continue
            if self.state == "esc":
                if char == "[":
                    self.state = "csi"
                elif char in "]P^_X":
                    self.state = "string"
                    self._string_bel = char == "]"
                elif " " <= char <= "/":
                    self.state = "esc_intermediate"
                else:
                    if char == "c":
                        self.style = DEFAULT_STYLE
                    self.state = "text"
                continue
            if self.state == "esc_intermediate":
                if "0" <= char <= "~":
                    self.state = "text"
                continue
            if self.state in ("csi", "csi_drop"):
                if "@" <= char <= "~":
                    if self.state == "csi" and char == "m":
                        self._sgr(self._params)
                    self.state, self._params = "text", ""
                elif self.state == "csi":
                    if len(self._params) < self.MAX_CSI_LENGTH and char in "0123456789;:":
                        self._params += char
                    else:
                        self.state, self._params = "csi_drop", ""
                continue
            if char in "\r\n":
                if self.newline and char != self.newline:
                    self.newline = ""
                    continue
                self.newline = char
                chars.append("\n")
                styles.append(self.style)
            elif char == "\t" or not unicodedata.category(char).startswith("C"):
                self.newline = ""
                chars.append(char)
                styles.append(self.style)
        return StyledText("".join(chars), tuple(styles))
