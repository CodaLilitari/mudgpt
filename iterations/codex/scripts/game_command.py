"""Local observation helper. Never use for passwords; use CLI --secret --stdin.

Saves selected game observations privately and prints readable text for review.
"""
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from mudgpt.broker import request
from mudgpt.observations import join_text_events, omit_contact_lines, render

def receipt_of(result):
    return {key: result.get(key) for key in (
        "reason", "delivery", "command_id", "command_complete",
        "broker_id", "cursor", "generation", "truncated")}


def guards_of(observation):
    return {"expected_broker_id": observation["broker_id"],
            "expected_generation": observation["generation"],
            "expected_cursor": observation["cursor"]}


def run(control, command, pages=False, view="plain"):
    dest = Path(control).parent / "observations.jsonl"
    current = request({"op": "status"}, control)
    # Older brokers cannot identify their cursor namespace. Stop before sending,
    # rather than silently treating a stored integer as a current observation.
    if not current.get("broker_id"):
        raise RuntimeError("Broker has no lifetime identifier; restart it before using this helper")
    after = 0
    if dest.exists():
        lines = dest.read_text(encoding="utf-8").splitlines()
        if lines:
            try:
                previous = json.loads(lines[-1])
                receipt = previous["receipt"]
                saved = previous.get("observation_cursor", receipt.get("cursor"))
                if (receipt.get("broker_id") == current["broker_id"]
                        and type(saved) is int and 0 <= saved <= current["cursor"]):
                    after = saved
            except (ValueError, KeyError, TypeError, AttributeError):
                pass  # An unusable private checkpoint never authorizes a replay.
    result = request({"op": "send", "command": command, "owner": "human",
                      **guards_of(current)}, control)
    original = result
    start = next((event["seq"] for event in result.get("events", [])
                  if event["kind"] == "send_attempt"), current["cursor"] + 1)
    events = list(result.get("events", []))
    pager_receipts, errors = [], {}
    # Explicit opt-in: only an observed pager marker authorizes a blank command.
    if pages:
        for _ in range(12):
            # An earlier page's marker must never authorize repeated blank sends
            # when the latest continuation produced no new text.
            latest_text = join_text_events(result.get("events", []))["text"]
            if (result.get("delivery") != "transport_accepted"
                    or result.get("reason") not in ("quiet", "prompt")
                    or not latest_text.rstrip().endswith("[Hit Return to continue]")):
                break
            try:
                result = request({"op": "send", "command": "", "owner": "human",
                                  **guards_of(result)}, control)
            except (OSError, ValueError, RuntimeError) as exc:
                errors["pager_error"] = str(exc)
                break  # Never repeat an uncertain pager send.
            pager_receipts.append(receipt_of(result))
            events.extend(result.get("events", []))
    styled = omit_contact_lines(join_text_events(events))
    ambient_styled = join_text_events([])
    observed_cursor = result["cursor"]
    truncated = bool(original.get("truncated"))
    try:
        observed = request({"op": "observe", "after": after,
                            "expected_broker_id": current["broker_id"]}, control)
        ambient_styled = omit_contact_lines(join_text_events(
            event for event in observed["events"] if event["seq"] < start))
        truncated = truncated or observed["truncated"]
        # Only advance past events actually carried in the receipts. Output that
        # arrived after the final send must remain ambient for the next command.
    except (OSError, ValueError, RuntimeError) as exc:
        errors["observation_error"] = str(exc)
    receipt = receipt_of(original)
    receipt["truncated"] = truncated
    entry = {"at": datetime.now(timezone.utc).isoformat(), "command": command,
             "ambient_before": ambient_styled["text"],
             "ambient_style_spans": ambient_styled["style_spans"],
             "text": styled["text"], "style_spans": styled["style_spans"],
             "style_offset_unit": "Unicode code points in each corresponding text field; end exclusive",
             "receipt": receipt, "pager_receipts": pager_receipts,
             "observation_cursor": observed_cursor, **errors}
    with dest.open("a", encoding="utf-8") as output:
        output.write(json.dumps(entry, ensure_ascii=False) + "\n")
    if ambient_styled["text"]:
        print("[Unsolicited output since the preceding command]\n" + render(ambient_styled, view))
    print(render(styled, view))
    print(json.dumps({**receipt, "pager_receipts": pager_receipts, **errors}))
    if errors:
        print("The command receipt was saved. Inspect fresh observations; do not automatically resend.",
              file=sys.stderr)
    return entry


if __name__ == "__main__":
    control, command = sys.argv[1:3]
    view = "ansi" if "--ansi" in sys.argv[3:] else "annotated" if "--annotated" in sys.argv[3:] else "plain"
    run(control, command, pages="--pages" in sys.argv[3:], view=view)
