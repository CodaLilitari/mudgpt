"""Carry one authored decision through MCP with durable, non-retrying admission.

Use after local login and human release. Supply the observation tuple and expiry
from the original decision, never a refreshed tuple invented at dispatch.
The journal is private and must be reused across invocations and restarts.
"""
import argparse
import asyncio
from contextlib import asynccontextmanager
from datetime import datetime, timezone
import json
import math
from pathlib import Path
import re
import sys
import time
import unicodedata

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from mudgpt.decision_journal import DecisionJournal
from mudgpt.observations import join_text_events, omit_contact_lines


def stamp():
    return datetime.now(timezone.utc).isoformat()


def data(result):
    if result.is_error:
        raise RuntimeError(" ".join(c.text for c in result.content if c.type == "text"))
    value = result.structured_content
    if value is None:
        value = json.loads("".join(c.text for c in result.content if c.type == "text"))
    if not isinstance(value, dict):
        raise ValueError("MCP returned no object; mutation outcome may be unknown")
    return value


def validate(decision):
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}", decision["decision_id"]):
        raise ValueError("decision-id must be 1-128 simple identifier characters")
    for name in ("actor", "source", "control", "expected_broker_id"):
        if not isinstance(decision[name], str) or not decision[name].strip():
            raise ValueError(f"{name} must be a nonempty string")
    if decision["actor"] == "human" or len(decision["actor"]) > 80:
        raise ValueError("actor must be a non-human controller name of at most 80 characters")
    for name in ("expected_generation", "expected_cursor"):
        if type(decision[name]) is not int or decision[name] < 0:
            raise ValueError(f"{name} must be a nonnegative integer")
    expiry = decision["expires_at"]
    if type(expiry) not in (int, float) or not math.isfinite(expiry):
        raise ValueError("expires-at must be finite POSIX seconds")
    command = decision["command"]
    if not isinstance(command, str) or len(command) > 500:
        raise ValueError("command must be a string of at most 500 characters")
    if any(unicodedata.category(c).startswith("C") or c in "\u2028\u2029" for c in command):
        raise ValueError("command cannot contain controls or line separators")


@asynccontextmanager
async def mcp_client(control):
    params = StdioServerParameters(command=sys.executable,
        args=["-m", "mudgpt", "--control", control, "mcp"])
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write, read_timeout_seconds=20) as client:
            yield client


def safe_observation(observation):
    # Join split text before removing named contact lines. This is deliberately
    # not a universal privacy filter: review game content before sharing it.
    styled = omit_contact_lines(join_text_events(observation.get("events", [])))
    return {**observation, **styled,
            "command_complete": False,
            "style_offset_unit": "Unicode code points in observation.text; end exclusive",
            "events": [{k: v for k, v in event.items() if k not in ("text", "style_spans")}
                       for event in observation.get("events", [])]}


async def relay(decision, journal, client_factory=mcp_client):
    validate(decision)
    receipt = {"at": stamp(), "updated_at": stamp(), **decision, "courier": "Coda",
               "source_conversation": decision["source"], "phase": "admitted",
               "send_attempted": False, "delivery": "not_sent", "command_complete": False,
               "lease_epoch": None, "observation": None, "release_error": None,
               "provenance": "Command attributed by the local courier; no remote socket access or identity proof implied."}
    admitted, saved = journal.admit(decision["decision_id"], decision, receipt)
    if not admitted:
        # Includes interrupted/pending decisions and decisions now past expiry.
        # Never reopen MCP, reacquire control, or refresh the decision's tuple.
        return {**saved, "duplicate": True, "new_send": False,
                "duplicate_note": "Returning the durable checkpoint; no operation retried. Pending outcomes require inspection."}

    def checkpoint(phase):
        receipt["phase"], receipt["updated_at"] = phase, stamp()
        journal.save(decision["decision_id"], receipt)

    def reject(reason):
        receipt["rejection"] = reason

    try:
        if time.time() >= decision["expires_at"]:
            reject("Decision expired before dispatch; nothing sent")
        else:
            async with client_factory(decision["control"]) as client:
                await client.initialize()
                status = data(await client.call_tool("mud_status", {}))
                if "guarded_send_v1" not in status.get("capabilities", []):
                    reject("Broker does not advertise guarded_send_v1; nothing sent")
                elif any(status.get(key) != decision[f"expected_{key}"]
                         for key in ("broker_id", "generation", "cursor")):
                    reject("Original decision observation no longer matches the broker; nothing sent")
                elif time.time() >= decision["expires_at"]:
                    reject("Decision expired before claim; nothing sent")
                else:
                    claimed = False
                    try:
                        checkpoint("claim_pending")
                        try:
                            claim = data(await client.call_tool("mud_claim", {"owner": decision["actor"]}))
                        except Exception as error:
                            receipt["claim_error"] = str(error)
                        else:
                            claimed = True
                            receipt["lease_epoch"] = claim.get("lease_epoch")
                            if time.time() >= decision["expires_at"]:
                                reject("Decision expired after claim; nothing sent")
                            else:
                                # Commit uncertainty BEFORE the mutation. Death between
                                # this commit and the response never permits a retry.
                                receipt.update(send_attempted=True, delivery="unknown")
                                checkpoint("send_pending")
                                try:
                                    observed = data(await client.call_tool("mud_send", {
                                        "owner": decision["actor"], "command": decision["command"],
                                        "timeout": 4,
                                        **{key: decision[key] for key in (
                                            "expected_broker_id", "expected_generation",
                                            "expected_cursor", "expires_at")}}))
                                    # Keep the broker's receipt before formatting or cleanup.
                                    receipt["send_receipt"] = {key: observed.get(key) for key in (
                                        "broker_id", "generation", "cursor", "command_id",
                                        "reason", "delivery", "truncated")}
                                    receipt["delivery"] = observed.get("delivery", "unknown")
                                    receipt["observation"] = safe_observation(observed)
                                    checkpoint("send_returned")
                                except Exception as error:
                                    # Even a tool error after dispatch is conservative: the
                                    # string alone is not a reliable delivery classification.
                                    receipt["send_error"] = str(error)
                    finally:
                        # This adapter holds the exact claim lease in memory. A failed
                        # or unconfirmed claim does not authorize any release attempt.
                        if claimed:
                            try:
                                released = data(await client.call_tool("mud_release", {"owner": decision["actor"]}))
                                receipt["released"] = released.get("owner") is None
                            except Exception as error:
                                receipt["release_error"] = str(error)
    except asyncio.CancelledError:
        receipt["courier_error"] = "Courier cancelled; inspect the durable checkpoint; never automatically retry"
        raise
    except Exception as error:
        receipt["courier_error"] = str(error)
    finally:
        try:
            checkpoint("finished")
        except Exception as error:
            # The earlier admission/pending record remains the restart boundary.
            # The returned in-memory receipt may be more complete than that row.
            receipt["journal_error"] = str(error)
    return receipt


async def main(args):
    decision = {key: getattr(args, key) for key in (
        "decision_id", "actor", "source", "command", "expected_broker_id",
        "expected_generation", "expected_cursor", "expires_at")}
    decision["control"] = str(Path(args.control).resolve())
    with DecisionJournal(args.journal) as journal:
        receipt = await relay(decision, journal)
    if args.output:
        try:
            target = Path(args.output)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(json.dumps(receipt, indent=2, ensure_ascii=False), encoding="utf-8")
        except OSError as error:
            receipt["export_error"] = str(error)
    print(json.dumps(receipt, indent=2, ensure_ascii=True))
    return receipt


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--control", required=True)
    parser.add_argument("--actor", required=True)
    parser.add_argument("--source", required=True, help="saved conversation reference for attribution")
    parser.add_argument("--decision-id", required=True, help="stable ID assigned once to this authored decision")
    parser.add_argument("--journal", required=True, help="stable private SQLite path; reuse it across every invocation")
    parser.add_argument("--expected-broker-id", required=True)
    parser.add_argument("--expected-generation", required=True, type=int)
    parser.add_argument("--expected-cursor", required=True, type=int)
    parser.add_argument("--expires-at", required=True, type=float, help="original decision deadline in POSIX seconds")
    parser.add_argument("--output")
    parser.add_argument("command")
    try:
        result = asyncio.run(main(parser.parse_args()))
        if (result.get("delivery") != "transport_accepted" or result.get("rejection")
                or any(result.get(key) for key in (
                    "claim_error", "send_error", "release_error", "courier_error", "journal_error", "export_error"))):
            raise SystemExit(1)
    except Exception as error:
        print(json.dumps({"error": str(error), "note": "No automatic retry; inspect the stable decision journal."}), file=sys.stderr)
        raise SystemExit(1) from None

