"""Explicit conditional retreat with durable, at-most-once admission.

Ordinary Session.send remains strict. Only the allowlisted action below may use
an evaluation cursor after checking intervening evidence. No arbitrary command,
automatic retry, deadline extension, or inferred room is accepted here.
"""
from __future__ import annotations

import asyncio
from copy import deepcopy
import math
import re
import sqlite3
import time
import unicodedata

from .decision_journal import DecisionConflict, DecisionJournal
from .errors import error_payload, is_prewrite_error
from .lok_scene import INPUT_MODE, ROOM_LINE, scene_from_observation
from .observations import join_text_events


_REQUIRED = {"action", "decision_id", "owner", "lease", "expected_broker_id",
             "expected_generation", "expected_cursor", "expected_lease_epoch", "expires_at", "condition"}
_ALLOWED = _REQUIRED | {"op", "schema_version"}
_RESULT_KEYS = {"broker_id", "generation", "cursor", "lease_epoch", "oldest", "truncated", "events",
                "command_id", "reason", "delivery", "command_complete"}


def _plain(value, maximum):
    return (isinstance(value, str) and 0 < len(value) <= maximum
            and not any(unicodedata.category(char).startswith("C") or char in "\u2028\u2029" for char in value))


def _authored(request):
    if not isinstance(request, dict) or not _REQUIRED <= request.keys() or request.keys() - _ALLOWED:
        raise ValueError("unsupported conditional request fields")
    if request.get("op", "conditional") != "conditional" or request.get("schema_version", 1) != 1:
        raise ValueError("unsupported conditional schema")
    if type(request.get("schema_version", 1)) is not int or request["action"] != "lok_retreat_v1":
        raise ValueError("unsupported action")
    if not _plain(request["decision_id"], 128) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:-]*", request["decision_id"]):
        raise ValueError("invalid decision ID")
    if not _plain(request["owner"], 80) or not _plain(request["expected_broker_id"], 128):
        raise ValueError("invalid controller or broker")
    if not _plain(request["lease"], 256):
        raise ValueError("missing controller lease")
    for key in ("expected_generation", "expected_cursor", "expected_lease_epoch"):
        if type(request[key]) is not int or request[key] < 0:
            raise ValueError("original observation counters must be nonnegative integers")
    if type(request["expires_at"]) not in (int, float) or not math.isfinite(request["expires_at"]):
        raise ValueError("decision expiry must be finite")
    condition = request["condition"]
    if (not isinstance(condition, dict) or "enemy_status" not in condition
            or condition.keys() - {"enemy_status", "allow_afk_combat"}
            or not _plain(condition["enemy_status"], 150)
            or type(condition.get("allow_afk_combat", False)) is not bool):
        raise ValueError("invalid explicit combat condition")
    authored = {key: deepcopy(request[key]) for key in _REQUIRED - {"lease", "condition"}}
    authored.update(schema_version=1, condition={"enemy_status": condition["enemy_status"],
                                               "allow_afk_combat": condition.get("allow_afk_combat", False)})
    return authored


def _receipt(status, authored=None):
    return {"broker_id": status.get("broker_id"), "generation": status.get("generation"),
            "cursor": status.get("cursor"), "lease_epoch": status.get("lease_epoch"), "command_complete": False,
            "delivery": "not_sent", "send_attempted": False, "reason_code": "admitted",
            "phase": "admitted", "original_decision": authored,
            "evaluation_cursor": None, "predicate_evidence": None,
            "intervening_observation": None, "events": [], "truncated": False}


def _context_changed(observation):
    if any(event.get("kind") in {"connected", "disconnected", "send_attempt", "send_uncertain"}
           for event in observation["events"]):
        return True
    text = join_text_events(observation["events"])["text"]
    return any(ROOM_LINE.fullmatch(line.strip()) or INPUT_MODE.match(line.strip())
               or line.strip() == "[Hit Return to continue]" for line in text.splitlines())


async def send_conditional(session, request_dict, journal_path):
    """Evaluate one authored conditional decision and perform at most one write.

    Call on the Session's event-loop thread. SQLite is opened here, rather than
    on the HTTP request thread. There is no suspension between evaluation and
    the ordinary Session.send prewrite guards; its first suspension follows the
    writer.write. This protects processed broker state, not unseen server bytes.
    """
    status = session.status()
    try:
        authored = _authored(request_dict)
    except (TypeError, ValueError):
        result = _receipt(status)
        result.update(reason_code="invalid_request", phase="finished")
        return result
    receipt = _receipt(status, authored)
    decision_id = authored["decision_id"]
    dispatch_started = False

    try:
        with DecisionJournal(journal_path) as journal:
            try:
                admitted, saved = journal.admit(decision_id, authored, receipt)
            except DecisionConflict:
                receipt.update(reason_code="decision_conflict", phase="finished")
                return receipt
            if not admitted:
                return {**saved, "duplicate": True, "new_send": False}

            def save():
                journal.save(decision_id, receipt)

            def refuse(code):
                receipt.update(delivery="not_sent", send_attempted=False, reason_code=code, phase="finished")
                save()
                return receipt

            # Re-read ordinary state locally; the authored tuple is never changed.
            status = session.status()
            if status["broker_id"] != authored["expected_broker_id"]:
                return refuse("broker_changed")
            if status["generation"] != authored["expected_generation"]:
                return refuse("generation_changed")
            if status["owner"] != authored["owner"]:
                return refuse("controller_mismatch")
            if status["lease_epoch"] != authored["expected_lease_epoch"]:
                return refuse("lease_epoch_changed")
            try:
                session.check_controller(authored["owner"], request_dict["lease"])
            except Exception as error:
                classified = error_payload(error)
                return refuse(classified["error_code"] if is_prewrite_error(classified) else "controller_unverified")
            if not status["connected"] or status["connecting"]:
                return refuse("not_connected")
            if status["busy"]:
                return refuse("command_in_flight")
            if time.time() >= authored["expires_at"]:
                return refuse("decision_expired")
            if (status.get("last_command") or {}).get("delivery") == "unknown":
                return refuse("previous_command_unknown")
            if authored["expected_cursor"] > status["cursor"]:
                return refuse("cursor_changed")

            intervening = session.observe(authored["expected_cursor"], expected_broker_id=authored["expected_broker_id"])
            receipt["intervening_observation"] = intervening
            receipt["evaluation_cursor"] = intervening["cursor"]
            if intervening["truncated"]:
                return refuse("observation_truncated")
            if _context_changed(intervening):
                return refuse("intervening_context_changed")
            # Parse the current retained tail as well as returning the complete
            # intervening delta. An unchanged current prompt needs no new tick.
            retained = session.observe(max(0, intervening["oldest"] - 1),
                                       expected_broker_id=authored["expected_broker_id"])
            scene = scene_from_observation(retained)
            condition = authored["condition"]
            matched = scene["enemy_status"] == condition["enemy_status"]
            numeric = scene["prompt"]["kind"] == "combat"
            afk = (condition["allow_afk_combat"] and scene["prompt"]["kind"] == "afk"
                   and scene["afk_combat_exchange"] is not None)
            if not (scene["prompt"]["complete"] and matched and (numeric or afk)):
                return refuse("condition_not_met")
            receipt["predicate_evidence"] = {
                "enemy_status": scene["enemy_status"],
                "enemy_status_evidence": scene["evidence"].get("enemy_status"),
                "prompt": scene["prompt"], "afk_combat_exchange": scene["afk_combat_exchange"],
                "source": scene["source"], "room_verified": False}
            if time.time() >= authored["expires_at"]:
                return refuse("decision_expired")

            # Persist uncertainty before entering the one allowed send attempt.
            # A crash here leaves an admitted ID that can only return this row.
            receipt.update(delivery="unknown", send_attempted=True, phase="send_pending", reason_code="send_pending")
            save()
            # The durable pending row conservatively records possible dispatch
            # across a process stop. In this live call, retain the actual boundary
            # so a failed checkpoint before entry is definitely not sent.
            dispatch_started = True
            try:
                result = await session.send("flee", owner=authored["owner"], lease=request_dict["lease"],
                    expected_broker_id=authored["expected_broker_id"],
                    expected_generation=authored["expected_generation"],
                    expected_cursor=receipt["evaluation_cursor"],
                    expected_lease_epoch=authored["expected_lease_epoch"],
                    expires_at=authored["expires_at"])
            except asyncio.CancelledError:
                # The durable send_pending row is deliberately left uncertain.
                raise
            except Exception as error:
                classified = error_payload(error)
                if is_prewrite_error(classified):
                    return refuse(classified["error_code"])
                receipt.update(reason_code="send_outcome_unknown", phase="finished")
            else:
                receipt.update({key: value for key, value in result.items() if key in _RESULT_KEYS})
                if receipt.get("delivery") != "transport_accepted":
                    receipt["delivery"] = "unknown"
                receipt.update(reason_code="transport_accepted" if receipt["delivery"] == "transport_accepted"
                               else "send_outcome_unknown", phase="finished")
            save()
            return receipt
    except (OSError, sqlite3.Error, ValueError) as error:
        # Admission/storage failure before dispatch prevents a write. After a
        # send, preserve the result already observed and never replay it.
        if not dispatch_started:
            receipt.update(delivery="not_sent", send_attempted=False,
                           reason_code="journal_or_evidence_unavailable", phase="finished")
        receipt["journal_or_evidence_error"] = type(error).__name__
        return receipt
