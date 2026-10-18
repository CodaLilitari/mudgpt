"""Combine safe observations without losing color-to-word alignment."""
from __future__ import annotations

import re
import unicodedata


_COLORS = ['black', 'red', 'green', 'yellow', 'blue', 'magenta', 'cyan', 'white']
_FLAGS = {'bold': 1, 'faint': 2, 'italic': 3, 'underline': 4, 'blink': 5,
          'inverse': 7, 'conceal': 8, 'strike': 9}


def join_text_events(events):
    """Offsets use Python/Unicode code points, not UTF-16 or byte offsets."""
    parts, spans, offset = [], [], 0
    for event in events:
        if event.get('kind') != 'text':
            continue
        text = event.get('text', '')
        parts.append(text)
        for span in event.get('style_spans', []):
            spans.append({**span, 'start': span['start'] + offset, 'end': span['end'] + offset})
        offset += len(text)
    return {'text': ''.join(parts), 'style_spans': spans}


def replace_ranges(document, replacements):
    """Replace ordered nonoverlapping ranges; replacement text has default style."""
    text, original = document['text'], document.get('style_spans', [])
    parts, spans, cursor, output = [], [], 0, 0

    def copy_slice(start, end):
        nonlocal output
        parts.append(text[start:end])
        for span in original:
            left, right = max(start, span['start']), min(end, span['end'])
            if left < right:
                spans.append({**span, 'start': output + left - start, 'end': output + right - start})
        output += end - start

    for start, end, replacement in replacements:
        if not cursor <= start <= end <= len(text):
            raise ValueError('Replacement ranges must be ordered and within the text')
        copy_slice(cursor, start)
        parts.append(replacement)
        output += len(replacement)
        cursor = end
    copy_slice(cursor, len(text))
    return {'text': ''.join(parts), 'style_spans': spans}


def omit_contact_lines(document):
    """Apply the existing contact-line policy after joining split network events."""
    matches = re.finditer(r'(?im)^.*(?:email|e-mail).*$', document['text'])
    return replace_ranges(document, [(m.start(), m.end(), '[Private contact field omitted]') for m in matches])


def _color_code(value, foreground):
    base = 30 if foreground else 40
    if value in _COLORS:
        return [str(base + _COLORS.index(value))]
    if isinstance(value, str) and value.startswith('bright_') and value[7:] in _COLORS:
        return [str(base + 60 + _COLORS.index(value[7:]))]
    if isinstance(value, str) and re.fullmatch(r'ansi256:\d{1,3}', value):
        index = int(value.split(':')[1])
        if 0 <= index <= 255:
            return [str(base + 8), '5', str(index)]
    if isinstance(value, str) and re.fullmatch(r'#[0-9a-fA-F]{6}', value):
        return [str(base + 8), '2', *(str(int(value[n:n + 2], 16)) for n in (1, 3, 5))]
    return []


def _valid_style(span):
    style = {}
    for name in ('fg', 'bg'):
        if _color_code(span.get(name), name == 'fg'):
            style[name] = span[name]
    style.update({name: True for name in _FLAGS if span.get(name) is True})
    return style


def render(document, mode='plain'):
    """Render only whitelisted SGR, never replay server terminal control strings."""
    if mode not in {'plain', 'ansi', 'annotated'}:
        raise ValueError('Unknown observation view')
    text = document['text']

    def safe(value):
        return ''.join(c for c in value if c in '\n\t' or not unicodedata.category(c).startswith('C'))

    if mode == 'plain':
        return safe(text)
    parts, cursor = [], 0
    for span in document.get('style_spans', []):
        start, end = span.get('start'), span.get('end')
        if type(start) is not int or type(end) is not int or not cursor <= start < end <= len(text):
            raise ValueError('Invalid or overlapping style span')
        parts.append(safe(text[cursor:start]))
        style = _valid_style(span)
        piece = safe(text[start:end])
        if mode == 'ansi' and style:
            codes = [str(code) for name, code in _FLAGS.items() if style.get(name)]
            codes += _color_code(style.get('fg'), True) + _color_code(style.get('bg'), False)
            parts.append('\x1b[' + ';'.join(codes) + 'm' + piece + '\x1b[0m')
        elif mode == 'annotated' and style:
            label = ','.join(name if value is True else name + '=' + value for name, value in style.items())
            parts.append('[' + label + ']' + piece + '[/style]')
        else:
            parts.append(piece)
        cursor = end
    parts.append(safe(text[cursor:]))
    rendered = ''.join(parts)
    return '\x1b[0m' + rendered + '\x1b[0m' if mode == 'ansi' else rendered
