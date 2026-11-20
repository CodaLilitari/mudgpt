"""Local observation helper. Never use for passwords; use CLI --secret --stdin.

Saves selected game observations privately and prints readable text for review.
"""
import json
import math
import os
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from mudgpt.broker import request
from mudgpt.observations import join_text_events, omit_contact_lines, render

def receipt_of(result):
    return {key: result.get(key) for key in (
        "reason", "delivery", "command_id", "command_complete",
        "broker_id", "cursor", "generation", "lease_epoch", "truncated")}


def guards_of(observation):
    guards = {"expected_broker_id": observation["broker_id"],
              "expected_generation": observation["generation"],
              "expected_cursor": observation["cursor"]}
    if "lease_epoch" in observation:
        guards["expected_lease_epoch"] = observation["lease_epoch"]
    return guards


def _checkpoint_path(dest):
    return dest.with_name("observation-checkpoint.json")


def _tail_entries(dest):
    """Recover complete entries with bounded I/O after an interrupted append."""
    if not dest.exists():
        return []
    with dest.open("rb") as stream:
        size = stream.seek(0, os.SEEK_END)
        offset = max(0, size - 65536)
        stream.seek(offset)
        data = stream.read(65536)
    lines = data.split(b"\n")
    if offset:
        lines = lines[1:]  # Its beginning may lie outside this bounded window.
    # Only newline-terminated records are durable append checkpoints.
    return lines[:-1]


def _cursor(entry, current):
    if not isinstance(entry, dict) or not isinstance(entry.get("receipt"), dict):
        return 0
    receipt = entry["receipt"]
    saved = entry.get("observation_cursor", receipt.get("cursor"))
    if (receipt.get("broker_id") == current["broker_id"]
            and receipt.get("generation", current["generation"]) == current["generation"]
            and type(saved) is int and 0 <= saved <= current["cursor"]):
        return saved
    return 0


def _after(dest, current):
    checkpoint = _checkpoint_path(dest)
    try:
        if checkpoint.stat().st_size <= 4096:
            entry = json.loads(checkpoint.read_text(encoding="utf-8"))
            if entry.get("journal_size") == dest.stat().st_size:
                return _cursor(entry, current)
    except (OSError, ValueError, TypeError, AttributeError):
        pass
    for line in reversed(_tail_entries(dest)):
        try:
            entry = json.loads(line)
        except (ValueError, UnicodeError):
            continue
        if isinstance(entry, dict) and isinstance(entry.get("receipt"), dict):
            return _cursor(entry, current)
    return 0


def _append(dest, entry):
    # Preserve an interrupted tail, but never merge it with the next JSON record.
    missing_newline = False
    if dest.exists() and dest.stat().st_size:
        with dest.open("rb") as stream:
            stream.seek(-1, os.SEEK_END)
            missing_newline = stream.read(1) != b"\n"
    with dest.open("a", encoding="utf-8") as output:
        os.chmod(dest, 0o600)
        if missing_newline:
            output.write("\n")
        output.write(json.dumps(entry, ensure_ascii=False, allow_nan=False) + "\n")
        output.flush()
        os.fsync(output.fileno())
    checkpoint = _checkpoint_path(dest)
    saved = {"receipt": {k: entry["receipt"].get(k) for k in ("broker_id", "generation")},
             "observation_cursor": entry["observation_cursor"], "journal_size": dest.stat().st_size}
    temp = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=dest.parent,
                                         prefix="checkpoint-", suffix=".tmp", delete=False) as out:
            temp = Path(out.name)
            os.chmod(temp, 0o600)
            json.dump(saved, out, allow_nan=False)
            out.flush()
            os.fsync(out.fileno())
        os.replace(temp, checkpoint)
    except OSError:
        # The complete journal remains the bounded fallback; never resend.
        print("Observation saved; compact checkpoint unavailable. Bounded recovery will be used.", file=sys.stderr)
    finally:
        if temp is not None and temp.exists():
            temp.unlink()


def _status(control):
    current = request({"op": "status"}, control)
    if not current.get("broker_id"):
        raise RuntimeError("Broker has no lifetime identifier; restart it before using this helper")
    return current


def prepare(control, *, ttl=120, view="plain"):
    """Observe without sending and return the original decision guards."""
    if type(ttl) not in (int, float) or not math.isfinite(ttl) or not 1 <= ttl <= 300:
        raise ValueError("Decision TTL must be finite and between 1 and 300 seconds")
    dest = Path(control).parent / "observations.jsonl"
    current = _status(control)
    observed = request({"op": "observe", "after": _after(dest, current),
                        "expected_broker_id": current["broker_id"]}, control)
    if observed.get("broker_id") != current["broker_id"]:
        raise RuntimeError("Observation belongs to a different broker; checkpoint unchanged")
    styled = omit_contact_lines(join_text_events(observed.get("events", [])))
    entry = {"at": datetime.now(timezone.utc).isoformat(), "operation": "observe", "command": None,
             **styled, "receipt": receipt_of(observed), "observation_cursor": observed["cursor"]}
    if (not observed.get("truncated") and isinstance(observed.get("broker_id"), str)
            and all(type(observed.get(k)) is int and observed[k] >= 0
                    for k in ("generation", "cursor", "lease_epoch"))):
        entry["next_decision"] = {"broker": observed["broker_id"], "generation": observed["generation"],
                                  "cursor": observed["cursor"], "lease_epoch": observed["lease_epoch"],
                                  "expires_at": time.time() + ttl}
    _append(dest, entry)
    print(render(styled, view))
    print(json.dumps({k: entry[k] for k in ("receipt", "next_decision") if k in entry}))
    return entry


def observe_only(control, *, view="plain"):
    return prepare(control, view=view)


def run_decision(control, command, decision, *, view="plain"):
    """Send one AI-authored command using its original, unrefreshed observation."""
    required = {"broker", "generation", "cursor", "lease_epoch", "expires_at"}
    if not isinstance(decision, dict) or set(decision) != required:
        raise ValueError("Supply the exact next_decision from prepare")
    if not isinstance(decision["broker"], str) or not decision["broker"]:
        raise ValueError("Original broker identity is required")
    if any(type(decision[k]) is not int or decision[k] < 0 for k in ("generation", "cursor", "lease_epoch")):
        raise ValueError("Original decision counters must be nonnegative integers")
    expiry = decision["expires_at"]
    if type(expiry) not in (int, float) or not math.isfinite(expiry) or not time.time() < expiry <= time.time() + 300:
        raise ValueError("Decision expired or exceeds maximum TTL; nothing sent")
    return _run(control, command, view=view, decision=dict(decision))


def run(control, command, pages=False, view="plain"):
    """Immediate human command; AI callers should use prepare/run_decision."""
    return _run(control, command, pages=pages, view=view)


def _run(control, command, pages=False, view="plain", decision=None):
    dest = Path(control).parent / "observations.jsonl"
    current = _status(control) if decision is None else {
        "broker_id": decision["broker"], **{k: decision[k] for k in ("generation", "cursor", "lease_epoch")}}
    after = _after(dest, current)
    expiry = {} if decision is None else {"expires_at": decision["expires_at"]}
    result = request({"op": "send", "command": command, "owner": "human",
                      **guards_of(current), **expiry}, control)
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
    if decision is not None:
        entry["original_decision"] = decision
    _append(dest, entry)
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
