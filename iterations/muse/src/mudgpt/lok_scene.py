"""Small, evidence-only interpretation of LoK-shaped text observations.

This module never sends commands or treats an old room as a current location.
The source text and color spans remain authoritative. Unsupported formats stay
unknown rather than becoming inferred game state.
"""
from __future__ import annotations

import re

from .observations import join_text_events


ROOM_LINE = re.compile(r"^(?P<title>.+?)\s*\[Room\s+(?P<id>\d{1,10})\]\s*$", re.IGNORECASE)
INPUT_MODE = re.compile(r"^(?:Description|String) editor\b", re.IGNORECASE)
COMBAT_END = re.compile(r"\bEnemy:\s*(?:100|[1-9]?\d)%foe\s*>")
EXIT_TOKEN = re.compile(r"(?P<before>[\[(]?)(?P<direction>north|east|south|west|up|down)"
                        r"(?P<closed>\(closed\)|\[closed\])?(?P<after>[\])]?)", re.IGNORECASE)


def _lines(text):
    """Nonempty display lines with offsets in the returned text, not byte offsets."""
    result = []
    for match in re.finditer(r"[^\r\n]+", text):
        raw = match.group()
        value = raw.strip()
        if value:
            start = match.start() + len(raw) - len(raw.lstrip())
            result.append({"start": start, "end": start + len(value), "text": value})
    return result


def _afk_exchange(lines, enemy_status):
    if len(lines) < 4 or lines[-1]["text"] != "<AFK>" or not enemy_status:
        return None
    name = re.fullmatch(r"(.+) is in perfect health\.", enemy_status)
    if not name:
        return None
    enemy = name.group(1)
    escaped = re.escape(enemy)
    outgoing, incoming = lines[-4], lines[-3]
    if not re.fullmatch(escaped + r" is unaffected by your [A-Za-z -]+!", outgoing["text"]):
        return None
    missed = re.fullmatch(escaped + r"'s [A-Za-z -]+ misses you\. \[0\]", incoming["text"])
    defended = re.fullmatch(r"You (?:block|parry|dodge) " + escaped
                            + r"'s attack(?: with your shield)?\.", incoming["text"], re.IGNORECASE)
    if not (missed or defended):
        return None
    return {"enemy": enemy, "outgoing": outgoing, "incoming": incoming}


def scene_from_observation(observation, max_chars=16384):
    """Return bounded display facts with exact source evidence and explicit unknowns.

    Offsets address Unicode code points in ``text``. ``source.text_start`` maps
    them back to the combined original text. A room is merely observed in this
    window; it is never certified as the character's present room.
    """
    if type(max_chars) is not int or not 64 <= max_chars <= 65536:
        raise ValueError("max_chars must be an integer from 64 to 65536")
    if not isinstance(observation, dict) or not isinstance(observation.get("events", []), list):
        raise ValueError("observation must contain an event list")
    events = observation.get("events", [])
    if any(not isinstance(event, dict) or
           (event.get("kind") == "text" and not isinstance(event.get("text", ""), str))
           for event in events):
        raise ValueError("invalid text observation")
    document = join_text_events(events)
    original = document["text"]
    offset = max(0, len(original) - max_chars)
    text = original[offset:]
    spans = []
    for span in document["style_spans"]:
        start, end = span.get("start"), span.get("end")
        if type(start) is not int or type(end) is not int or not 0 <= start < end <= len(original):
            raise ValueError("invalid source color span")
        left, right = max(start, offset), min(end, len(original))
        if left < right:
            spans.append({**span, "start": left - offset, "end": right - offset})

    source = {key: observation.get(key) for key in ("broker_id", "generation", "cursor", "oldest")}
    source.update(truncated=bool(observation.get("truncated")), text_start=offset,
                  text_truncated=bool(offset))
    scene = {"schema_version": 1, "profile": "lok_v1", "source": source,
             "text": text, "style_spans": spans,
             "style_offset_unit": "Unicode code points in text; end exclusive",
             "prompt": {"kind": "unknown", "complete": False, "evidence": None},
             "resources": {"hp": None, "mana": None, "move": None},
             "enemy_status": None, "afk_combat_exchange": None,
             "room": None, "exits": None, "evidence": {}}
    lines = _lines(text)
    # Do not use a clipped partial first line as a room, exit or combat fact.
    if offset and original[offset - 1] not in "\r\n":
        first_break = re.search(r"[\r\n]", text)
        lines = [line for line in lines if first_break and line["start"] > first_break.start()]
    for line in lines:
        room = ROOM_LINE.fullmatch(line["text"])
        if room:
            scene["room"] = {"id": int(room.group("id")), "title": room.group("title").strip(),
                             "freshness": "observed_in_window", "evidence": line}
        value = line["text"]
        if value.startswith("[Exits:") and value.endswith("]"):
            inner = value[7:-1]
            exits = []
            # Only complete direction tokens in the displayed list are facts.
            for match in EXIT_TOKEN.finditer(inner):
                before, after = inner[:match.start()], inner[match.end():]
                if (before and not before[-1].isspace()) or (after and not after[0].isspace()):
                    continue
                token = match.group()
                start = line["start"] + 7 + match.start()
                closed = bool(match.group("closed") or
                              (match.group("before") == "(" and match.group("after") == ")") or
                              (match.group("before") == "[" and match.group("after") == "]"))
                exits.append({"direction": match.group("direction").lower(), "closed": closed,
                              "evidence": {"start": start, "end": start + len(token), "text": token}})
            scene["exits"] = exits
            scene["evidence"]["exits"] = line
    if not lines:
        return scene
    last = lines[-1]
    value = last["text"]
    kind = "unknown"
    if value == "[Hit Return to continue]":
        kind = "pager"
    elif INPUT_MODE.match(value):
        kind = "editor"
    elif value == "<AFK>":
        kind = "afk"
    elif re.fullmatch(r"<[^<>\r\n]+>", value):
        for field, suffix in (("hp", "hp"), ("mana", "m"), ("move", "mv")):
            match = re.search(r"(?<![A-Za-z0-9])(\d{1,10})(?:/(\d{1,10}))?"
                              + suffix + r"(?=\s|>|$)", value)
            if match:
                scene["resources"][field] = {"current": int(match.group(1)),
                                             "maximum": int(match.group(2)) if match.group(2) else None}
        if any(item is not None for item in scene["resources"].values()):
            kind = "combat" if COMBAT_END.search(value) else "resources"
    if kind != "unknown":
        scene["prompt"] = {"kind": kind, "complete": True, "evidence": last}
    if kind in ("combat", "afk") and len(lines) >= 2:
        previous = lines[-2]
        # This remains an exact adjacent display line, not a health inference.
        if (len(previous["text"]) <= 200
                and re.fullmatch(r".+ (?:is|has|looks) .+[.!]", previous["text"])):
            scene["enemy_status"] = previous["text"]
            scene["evidence"]["enemy_status"] = previous
        if kind == "afk":
            scene["afk_combat_exchange"] = _afk_exchange(lines, scene["enemy_status"])
    return scene
