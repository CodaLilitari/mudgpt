"""Portable, explicitly driven actor client. Login remains a local operator step."""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import hashlib
import json
import math
import os
from pathlib import Path
import re
import sqlite3
import sys
import tempfile
import time
import unicodedata

from .broker import request
from .decision_journal import DecisionJournal
from .errors import BrokerRequestError
from .observations import join_text_events, omit_contact_lines, render


@contextmanager
def exclusive(path):
    """One actor invocation at a time, independent of the broker control lock."""
    with open(path, "a+b") as lock:
        os.chmod(path, 0o600)
        if os.name == "nt":
            import msvcrt
            if os.fstat(lock.fileno()).st_size == 0:
                lock.write(b"\0")
                lock.flush()
            lock.seek(0)
            msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield


def atomic_json(path, value):
    temp = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent,
                                         prefix=path.name + ".", suffix=".tmp", delete=False) as out:
            temp = Path(out.name)
            os.chmod(temp, 0o600)
            json.dump(value, out, ensure_ascii=True, allow_nan=False)
            out.flush()
            os.fsync(out.fileno())
        os.replace(temp, path)
    finally:
        if temp is not None and temp.exists():
            temp.unlink()


def _safe_metadata(value):
    if isinstance(value, dict):
        return {k: _safe_metadata(v) for k, v in value.items()
                if k.lower() not in {"lease", "token", "password", "authorization", "secret"}}
    if isinstance(value, list):
        return [_safe_metadata(v) for v in value]
    return value


class ActorClient:
    def __init__(self, config_path, *, call=request, now_fn=time.time):
        path = Path(config_path).resolve()
        if path.stat().st_size > 16384:
            raise ValueError("Actor configuration exceeds 16 KiB")
        cfg = json.loads(path.read_text(encoding="utf-8-sig"))
        allowed = {"actor", "control", "state_dir", "profile", "max_ttl_seconds"}
        if not isinstance(cfg, dict) or set(cfg) - allowed:
            raise ValueError("Actor configuration has unsupported fields; no credentials belong here")
        self.actor = cfg.get("actor")
        if (not isinstance(self.actor, str) or self.actor == "human"
                or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,79}", self.actor)):
            raise ValueError("Actor must be a non-human simple name of 1-80 characters")
        if not isinstance(cfg.get("control"), str) or not cfg["control"].strip():
            raise ValueError("A control-file path is required")
        self.control = (path.parent / cfg["control"]).resolve()
        self.profile = cfg.get("profile", "lok")
        if self.profile not in {"lok", "none"}:
            raise ValueError("Observation profile must be lok or none")
        self.ttl = cfg.get("max_ttl_seconds", 120)
        if type(self.ttl) not in (int, float) or not math.isfinite(self.ttl) or not 1 <= self.ttl <= 300:
            raise ValueError("max_ttl_seconds must be finite and between 1 and 300")
        if "state_dir" in cfg:
            if not isinstance(cfg["state_dir"], str) or not cfg["state_dir"].strip():
                raise ValueError("state_dir must be a nonempty path")
            self.state_dir = (path.parent / cfg["state_dir"]).resolve()
        else:
            base = os.environ.get("LOCALAPPDATA") if os.name == "nt" else os.environ.get("XDG_STATE_HOME")
            base = Path(base) if base else Path.home() / ".local" / "state"
            suffix = hashlib.sha256(str(self.control).encode()).hexdigest()[:12]
            self.state_dir = (base / "MUDweave" / "actors" / f"{self.actor}-{suffix}").resolve()
        if any((p / ".git").exists() for p in (self.state_dir, *self.state_dir.parents)):
            raise ValueError("Private actor state must be outside every Git checkout")
        self.state_dir.mkdir(parents=True, exist_ok=True)
        os.chmod(self.state_dir, 0o700)
        self.state_path = self.state_dir / "actor-state.json"
        self.journal_path = self.state_dir / "decisions.sqlite3"
        self.call, self.now = call, now_fn
        self.binding = {"actor": self.actor, "control": str(self.control)}

    def _state(self):
        if not self.state_path.exists():
            return {"binding": self.binding}
        state = json.loads(self.state_path.read_text(encoding="utf-8"))
        if not isinstance(state, dict) or state.get("binding") != self.binding:
            raise ValueError("Actor state belongs to another configuration; use its original state directory")
        return state

    def _present(self, op, result):
        keys = ("broker_id", "generation", "cursor", "oldest", "truncated", "owner", "lease_epoch",
                "connected", "connecting", "busy", "reason", "reason_code", "error_code", "delivery",
                "command_id", "command_complete", "send_attempted", "capabilities")
        receipt = {k: result[k] for k in keys if k in result}
        if isinstance(result.get("last_command"), dict):
            receipt["last_command"] = {k: result["last_command"][k] for k in
                                      ("command_id", "delivery", "command_complete") if k in result["last_command"]}
        document = omit_contact_lines(join_text_events(result.get("events", [])))
        output = {"schema_version": 1, "operation": op, "actor": self.actor,
                  "received_at": self.now(), "receipt": receipt, **document,
                  "annotated": render(document, "annotated"),
                  "style_offset_unit": "Unicode code points; end exclusive"}
        if self.profile == "lok" and "events" in result:
            from .lok_scene import scene_from_observation
            # Interpret exactly the filtered document, retaining its color offsets.
            visible = {k: result[k] for k in ("broker_id", "generation", "cursor", "oldest", "truncated")
                       if k in result}
            visible["events"] = [{"kind": "text", **document}]
            output["scene"] = scene_from_observation(visible)
        for key in ("original_decision", "evaluation_cursor", "predicate_evidence", "room_verification"):
            if key in result:
                output[key] = _safe_metadata(result[key])
        if isinstance(result.get("intervening_observation"), dict):
            output["intervening_observation"] = self._present("intervening", result["intervening_observation"])
        if (op in {"observe", "send", "retreat"} and not result.get("truncated")
                and (op == "observe" or result.get("delivery") == "transport_accepted")
                and all(k in receipt for k in ("broker_id", "generation", "cursor", "lease_epoch"))):
            output["next_decision"] = {"broker": receipt["broker_id"], "generation": receipt["generation"],
                                       "cursor": receipt["cursor"], "lease_epoch": receipt["lease_epoch"],
                                       "expires_at": self.now() + self.ttl}
        return output

    def _authored(self, op, fields):
        required = {"decision_id", "broker", "generation", "cursor", "lease_epoch", "expires_at"}
        allowed = required | ({"command"} if op == "send" else {"enemy_status", "allow_afk_combat"})
        if set(fields) - allowed or not required <= set(fields):
            raise ValueError("An exact original decision ID, broker, generation, cursor, lease epoch and expiry are required")
        ident = fields["decision_id"]
        if not isinstance(ident, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}", ident):
            raise ValueError("decision_id must be 1-128 simple identifier characters")
        if not isinstance(fields["broker"], str) or not fields["broker"]:
            raise ValueError("Original broker identity is required")
        for name in ("generation", "cursor", "lease_epoch"):
            if type(fields[name]) is not int or fields[name] < 0:
                raise ValueError(f"{name} must be a nonnegative integer")
        expiry = fields["expires_at"]
        if type(expiry) not in (int, float) or not math.isfinite(expiry):
            raise ValueError("Decision expiry must be finite")
        command = fields.get("command") if op == "send" else fields.get("enemy_status")
        limit = 500 if op == "send" else 150
        if (not isinstance(command, str) or len(command) > limit or (op == "retreat" and not command)
                or any(unicodedata.category(c).startswith("C") or c in "\u2028\u2029" for c in command)):
            raise ValueError("Supply one bounded command or exact enemy-status line, without controls")
        if op == "retreat" and type(fields.get("allow_afk_combat", False)) is not bool:
            raise ValueError("allow_afk_combat must be a boolean")
        return {"schema_version": 1, "operation": op, **self.binding, **fields}

    def _mutate(self, op, fields, state):
        authored = self._authored(op, fields)
        pending = {"schema_version": 1, "operation": op, "actor": self.actor,
                   "phase": "admitted", "receipt": {"delivery": "not_sent", "command_complete": False},
                   "send_attempted": False}

        def storage_refusal(error, journal=None):
            output = self._present(op, {"broker_id": fields["broker"], "generation": fields["generation"],
                "cursor": fields["cursor"], "lease_epoch": fields["lease_epoch"], "delivery": "not_sent",
                "command_complete": False, "reason_code": "private_storage_unavailable"})
            output.update(decision_id=fields["decision_id"], phase="local_storage_failure",
                          send_attempted=False, request_attempted=False, new_send=False, duplicate=False,
                          journal_error=type(error).__name__)
            if journal is not None:
                try:
                    journal.save(fields["decision_id"], output)
                except (OSError, sqlite3.Error):
                    pass  # An existing admission remains return-only.
            return output

        try:
            journal = DecisionJournal(self.journal_path)
        except (OSError, sqlite3.Error) as error:
            return storage_refusal(error)
        with journal:
            try:
                fresh, saved = journal.admit(fields["decision_id"], authored, pending)
            except (OSError, sqlite3.Error) as error:
                return storage_refusal(error)
            if not fresh:
                return {**saved, "duplicate": True, "new_send": False}
            try:
                if not self.now() < fields["expires_at"] <= self.now() + self.ttl:
                    raise ValueError("Decision expired or exceeds the configured TTL; nothing sent")
                if state.get("broker_id") != fields["broker"] or not state.get("lease"):
                    raise ValueError("Claim this broker through the actor client before sending")
                if state.get("needs_observation"):
                    raise ValueError("Previous outcome requires a complete fresh observation; do not replay it")
                if state.get("lease_epoch") != fields["lease_epoch"]:
                    raise ValueError("Original lease epoch no longer matches this actor claim")
                capability = "conditional_actions_v1" if op == "retreat" else "guarded_send_v1"
                if capability not in state.get("capabilities", []):
                    raise ValueError(f"Claimed broker does not advertise {capability}; nothing sent")
            except ValueError:
                try:
                    journal.save(fields["decision_id"], {**pending, "phase": "local_refusal"})
                except (OSError, sqlite3.Error) as error:
                    return storage_refusal(error)
                raise
            payload = {"op": "send" if op == "send" else "conditional", "owner": self.actor,
                       "lease": state["lease"], "expected_broker_id": fields["broker"],
                       "expected_generation": fields["generation"], "expected_cursor": fields["cursor"],
                       "expected_lease_epoch": fields["lease_epoch"], "expires_at": fields["expires_at"]}
            if op == "send":
                payload.update(command=fields["command"], timeout=3, quiet=0.3)
            else:
                payload.update(action="lok_retreat_v1", decision_id=fields["decision_id"],
                               condition={"enemy_status": fields["enemy_status"],
                                          "allow_afk_combat": fields.get("allow_afk_combat", False)})
            state["needs_observation"] = True
            try:
                atomic_json(self.state_path, state)
                pending.update(phase="send_pending", send_attempted=True,
                               receipt={"delivery": "unknown", "command_complete": False})
                journal.save(fields["decision_id"], pending)
            except (OSError, sqlite3.Error) as error:
                # This branch precedes the broker call, so delivery is known.
                # A persisted observation gate may remain until a fresh observe.
                return storage_refusal(error, journal)
            try:
                result = self.call(payload, self.control)
                if (not isinstance(result, dict) or result.get("broker_id") != fields["broker"]
                        or result.get("delivery") not in {"transport_accepted", "not_sent", "unknown"}):
                    raise RuntimeError("Invalid mutation response")
            except (OSError, ValueError, RuntimeError) as error:
                known = isinstance(error, BrokerRequestError) and error.delivery == "not_sent"
                result = {"broker_id": fields["broker"], "generation": fields["generation"],
                          "cursor": fields["cursor"], "lease_epoch": fields["lease_epoch"],
                          "delivery": "not_sent" if known else "unknown", "command_complete": False,
                          "reason_code": error.code if known else "request_outcome_unknown"}
            output = self._present(op, result)
            output.update(decision_id=fields["decision_id"], phase="returned",
                          request_attempted=True, duplicate=False,
                          new_send=result.get("delivery") != "not_sent")
            output["send_attempted"] = result.get("send_attempted", result.get("delivery") != "not_sent")
            try:
                journal.save(fields["decision_id"], output)
            except (OSError, sqlite3.Error) as error:
                # Preserve the receipt actually received. The durable pending row
                # stays uncertain and cannot be resumed after a process restart.
                output.update(journal_error=type(error).__name__, requires_observation=True)
                output.pop("next_decision", None)
                return output
            state["needs_observation"] = result.get("delivery") not in {"transport_accepted", "not_sent"}
            if not result.get("truncated") and result.get("delivery") == "transport_accepted":
                state["observation_cursor"] = result.get("cursor", state.get("observation_cursor", 0))
                state["generation"] = result.get("generation")
            try:
                atomic_json(self.state_path, state)
            except OSError as error:
                output.update(state_error=type(error).__name__, requires_observation=True)
                output.pop("next_decision", None)
            return output

    def execute(self, op, **fields):
        if op not in {"status", "claim", "observe", "send", "retreat", "release"}:
            raise ValueError("Unsupported actor operation")
        with exclusive(self.state_dir / "actor.lock"):
            state = self._state()
            if op in {"send", "retreat"}:
                output = self._mutate(op, fields, state)
            else:
                payload = {"op": op}
                if op == "observe":
                    if set(fields) - {"after", "broker"}:
                        raise ValueError("Unsupported observation fields")
                    after = fields.get("after", state.get("observation_cursor", 0))
                    identity = fields.get("broker", state.get("broker_id"))
                    if type(after) is not int or after < 0 or (after and not identity):
                        raise ValueError("A nonnegative cursor and its broker identity are required")
                    payload.update(after=after)
                    if identity:
                        payload["expected_broker_id"] = identity
                elif fields:
                    raise ValueError("This operation takes no additional fields")
                if op in {"claim", "release"}:
                    payload["owner"] = self.actor
                    if state.get("lease"):
                        payload["lease"] = state["lease"]
                    elif op == "release":
                        raise ValueError("No confirmed actor lease to release")
                result = self.call(payload, self.control)
                if not isinstance(result, dict):
                    raise RuntimeError("Broker returned no usable response; inspect state")
                if op == "claim":
                    if (result.get("owner") != self.actor or not result.get("lease")
                            or not result.get("broker_id") or type(result.get("lease_epoch")) is not int):
                        raise RuntimeError("Claim not confirmed; do not guess or release ownership")
                    state = {"binding": self.binding, "needs_observation": state.get("needs_observation", False),
                             **{k: result[k] for k in
                             ("broker_id", "lease", "lease_epoch", "generation", "capabilities") if k in result}}
                    atomic_json(self.state_path, state)
                elif op == "release":
                    if result.get("owner") is not None:
                        raise RuntimeError("Release not confirmed; inspect ownership")
                    state = {"binding": self.binding}
                    atomic_json(self.state_path, state)
                elif op == "observe":
                    if state.get("broker_id") and result.get("broker_id") != state["broker_id"]:
                        raise RuntimeError("Observation belongs to a different broker; no checkpoint advanced")
                    if not result.get("truncated"):
                        state.update(observation_cursor=result["cursor"], generation=result["generation"],
                                     broker_id=result["broker_id"], needs_observation=False)
                        atomic_json(self.state_path, state)
                output = self._present(op, result)
            path = self.state_dir / "observations.jsonl"
            try:
                with path.open("a", encoding="utf-8") as stream:
                    os.chmod(path, 0o600)
                    stream.write(json.dumps(output, ensure_ascii=True, allow_nan=False) + "\n")
            except OSError as error:
                output["observation_journal_error"] = type(error).__name__
            return output


def parser():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--config", required=True, help="Portable actor configuration, without credentials")
    p.add_argument("--view", choices=("json", "annotated"), default="annotated")
    commands = p.add_subparsers(dest="op", required=True)
    for op in ("status", "claim", "release"):
        commands.add_parser(op)
    observe = commands.add_parser("observe")
    observe.add_argument("--after", type=int)
    observe.add_argument("--broker")
    for op in ("send", "retreat"):
        command = commands.add_parser(op)
        for field in ("decision-id", "broker"):
            command.add_argument("--" + field, required=True)
        for field in ("generation", "cursor", "lease-epoch"):
            command.add_argument("--" + field, required=True, type=int)
        command.add_argument("--expires-at", required=True, type=float)
        if op == "send":
            command.add_argument("command", nargs="?")
            command.add_argument("--stdin", action="store_true", help="One non-secret line, including blank Return")
        else:
            command.add_argument("--enemy-status", required=True)
            command.add_argument("--allow-afk-combat", action="store_true")
    return p


def main(argv=None):
    args = parser().parse_args(argv)
    fields = {k: v for k, v in vars(args).items() if k not in {"op", "config", "view", "stdin"} and v is not None}
    try:
        if args.op == "send" and args.stdin:
            if args.command is not None:
                raise ValueError("Use a positional command or stdin, not both")
            fields["command"] = sys.stdin.read(502).removesuffix("\n").removesuffix("\r")
        output = ActorClient(args.config).execute(args.op, **fields)
        if args.view == "json":
            print(json.dumps(output, ensure_ascii=True))
        else:
            print(output.get("annotated", ""))
            print("ACTOR_RECEIPT " + json.dumps({k: v for k, v in output.items()
                                               if k not in {"text", "style_spans", "annotated"}}, ensure_ascii=True))
        return 0 if output.get("receipt", {}).get("delivery") not in {"unknown", "not_sent"} else 2
    except (OSError, ValueError, RuntimeError) as error:
        print(json.dumps({"error": str(error), "notice": "Inspect state; never automatically replay a command."}), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
