"""Local observation helper. Never use for passwords; use CLI --secret --stdin.

Saves selected game observations privately and prints readable text for review.
"""
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from mudgpt.broker import request
from mudgpt.observations import join_text_events, omit_contact_lines, render

control, command = sys.argv[1:3]
dest = Path(control).parent / "observations.jsonl"
after = 0
if dest.exists():
    lines = dest.read_text(encoding="utf-8").splitlines()
    if lines:
        after = json.loads(lines[-1])["receipt"]["cursor"]
else:
    after = request({"op": "status"}, control)["cursor"]
result = request({"op": "send", "command": command, "owner": "human"}, control)
start = next((e["seq"] for e in result.get("events", []) if e["kind"] == "send_attempt"), after + 1)
events = list(result.get("events", []))
text = join_text_events(events)["text"]
# Explicit opt-in: only the observed pager marker authorizes an empty continuation.
if "--pages" in sys.argv[3:]:
    for _ in range(12):
        if not text.rstrip().endswith("[Hit Return to continue]"):
            break
        result = request({"op": "send", "command": "", "owner": "human"}, control)
        events.extend(result.get("events", []))
        text = join_text_events(events)["text"]
styled = omit_contact_lines(join_text_events(events))
text = styled["text"]
observed = request({"op": "observe", "after": after}, control)
ambient_styled = omit_contact_lines(join_text_events(e for e in observed["events"] if e["seq"] < start))
ambient = ambient_styled["text"]
receipt = {k: result.get(k) for k in ("reason", "delivery", "command_id", "cursor", "generation", "truncated")}
receipt["truncated"] = receipt["truncated"] or observed["truncated"]
entry = {"at": datetime.now(timezone.utc).isoformat(), "command": command,
         "ambient_before": ambient, "ambient_style_spans": ambient_styled["style_spans"],
         "text": text, "style_spans": styled["style_spans"],
         "style_offset_unit": "Unicode code points in each corresponding text field; end exclusive", "receipt": receipt}
with dest.open("a", encoding="utf-8") as output:
    output.write(json.dumps(entry, ensure_ascii=False) + "\n")
view = "ansi" if "--ansi" in sys.argv[3:] else "annotated" if "--annotated" in sys.argv[3:] else "plain"
if ambient:
    print("[Unsolicited output since the preceding command]\n" + render(ambient_styled, view))
print(render(styled, view))
print(json.dumps(receipt))
