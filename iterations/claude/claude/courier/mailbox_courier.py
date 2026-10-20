"""Krasna's mailbox courier: one request file in, at most one MUD command out.

Red's self-contained MudGPT home: this folder carries its own copy of the
MudGPT package (src/), so Red can change her copy without touching Coda's.
The folder can be moved anywhere. Start it by double-clicking Start-Krasna.cmd.

It starts a private MudGPT broker for Red's character (control file in this
folder), then serves requests Red drops into mailbox/in/ through the shared
folder, answering in mailbox/out/. Rules it enforces:

* Each request id is appended to ledger.jsonl and the request file is moved
  out of the inbox BEFORE anything is sent, so no crash or restart can ever
  send the same request twice.
* A send may carry if_cursor. If the game produced anything after that cursor,
  nothing is sent; the new events come back instead, for a fresh decision.
* Every state-changing request carries an expiry (created + ttl). A request
  that sat in the inbox past it -- say, while this courier was not running --
  is refused, never executed late.
* The password is read from credentials.txt and sent through MudGPT's secret
  path. It never appears in a request, response, ledger or console line.
* Red claims control as "red". Andrei can take over at any moment by
  double-clicking Take-Over-Krasna.cmd in this folder.

Stop with Ctrl+C. Quit in-game first: closing the socket is not an in-game save.
"""
import json
import os
import re
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

if sys.version_info < (3, 11):
    sys.exit("MudGPT needs Python 3.11+ (asyncio timeouts differ on 3.10).")

HERE = Path(__file__).resolve().parent            # Red's MudGPT home (movable)
SOURCE = HERE / "src"                              # Red's own copy of the package
sys.path.insert(0, str(SOURCE))
from mudgpt.broker import request  # noqa: E402
from mudgpt.observations import join_text_events, omit_contact_lines, render  # noqa: E402

PROFILE, CREDS = HERE / "profile.json", HERE / "credentials.txt"
# Under WSL the launcher keeps the broker's control/lock file on the Linux disk
# (KRASNA_CONTROL), because file locks across the /mnt/c bridge are not reliable.
CONTROL = Path(os.environ["KRASNA_CONTROL"]) if os.environ.get("KRASNA_CONTROL") else HERE / "control.json"
TAKEOVER_HINT = ("double-click Take-Over-Krasna.cmd" if os.name == "nt"
                 else "run: bash take-over-krasna-wsl.sh (in this folder)")
RELEASE_HINT = "Release-To-Red.cmd" if os.name == "nt" else "bash release-to-red-wsl.sh"
BOX = HERE / "mailbox"
INBOX, OUTBOX = BOX / "in", BOX / "out"
DONE = INBOX / "done"
LEDGER = BOX / "ledger.jsonl"
ACTOR = "red"
REQUEST_ID = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
OPS = {"connect", "send", "observe", "status", "claim", "release", "close"}
READ_ONLY = {"observe", "status"}
MAX_TTL = 600  # seconds; a decision older than this is never carried


def stamp():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def log(message):
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {message}", flush=True)


def write_json_atomic(path, value):
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(temporary, path)


def ledger_ids():
    ids = set()
    if LEDGER.exists():
        for line in LEDGER.read_text(encoding="utf-8").splitlines():
            try:
                ids.add(json.loads(line)["id"])
            except (ValueError, KeyError, TypeError):
                pass
    return ids


def ledger_append(entry):
    with LEDGER.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(entry) + "\n")
        handle.flush()
        os.fsync(handle.fileno())


def read_password():
    for line in CREDS.read_text(encoding="utf-8").splitlines():
        if line.startswith("password="):
            value = line[len("password="):].strip()
            if value:
                return value
    raise ValueError("credentials.txt has no password= line")


def annotated(events):
    return render(omit_contact_lines(join_text_events(events)), "annotated")


def broker_status():
    try:
        return request({"op": "status"}, CONTROL)
    except Exception:
        return None


class Courier:
    def __init__(self, attached_status=None):
        self.lease = None          # held only in this process's memory
        self.claimed_before = False  # once Red has held control, "human" means a takeover
        self.last_cursor = 0       # last cursor reported to Red (this broker lifetime)
        self.seen = ledger_ids()
        if attached_status:
            owner, epoch = attached_status.get("owner"), attached_status.get("lease_epoch", 0)
            if owner == ACTOR:
                # A previous courier died holding Red's claim; its lease died with it.
                request({"op": "claim", "owner": "human", "takeover": True}, CONTROL)
                request({"op": "release", "owner": "human"}, CONTROL)
                log("recovered Red's control left behind by a previous courier")
            elif owner == "human" and epoch > 0:
                self.claimed_before = True  # Andrei holds control after a takeover: wait for him

    def call(self, **payload):
        return request(payload, CONTROL)

    def controller(self):
        return {"owner": ACTOR, "lease": self.lease} if self.lease else {"owner": "human"}

    def handle(self, req):
        op = req["op"]
        if op == "status":
            return {"status": self.call(op="status")}
        if op == "observe":
            after = req.get("after", self.last_cursor)
            observed = self.call(op="observe", after=after)
            self.last_cursor = observed["cursor"]
            return {"text": annotated(observed["events"]), "cursor": observed["cursor"],
                    "generation": observed["generation"], "truncated": observed["truncated"]}
        if op == "claim":
            if self.call(op="status")["owner"] == "human":
                if self.claimed_before:
                    raise PermissionError(f"Andrei has taken over; Red waits until he runs {RELEASE_HINT}")
                self.call(op="release", owner="human")  # first claim of this broker's life only
            claimed = self.call(op="claim", owner=ACTOR, lease=self.lease)
            self.lease = claimed.pop("lease")
            self.claimed_before = True
            return {"status": claimed}
        if op == "release":
            if not self.lease:
                raise ValueError("the courier holds no claim")
            released = self.call(op="release", owner=ACTOR, lease=self.lease)
            self.lease = None
            return {"status": released}
        if op == "connect":
            # Read the profile fresh, so switching servers never needs a restart.
            profile = json.loads(PROFILE.read_text(encoding="utf-8-sig"))
            target = {key: profile[key] for key in ("host", "port", "tls") if key in profile}
            return {"status": self.call(op="connect", **target, **self.controller()),
                    "target": f"{target.get('host')}:{target.get('port')}"}
        if op == "close":
            return {"status": self.call(op="close", **self.controller())}
        # op == "send"
        secret_ref = req.get("secret_ref")
        if secret_ref not in (None, "password"):
            raise ValueError("secret_ref must be 'password'")
        command = read_password() if secret_ref else req.get("command")
        if not isinstance(command, str):
            raise ValueError("send needs a command string")
        timeout = req.get("timeout", 5)
        quiet = req.get("quiet", min(0.5, timeout))
        if_cursor = req.get("if_cursor")
        if if_cursor is not None and (type(if_cursor) is not int or if_cursor < 0):
            raise ValueError("if_cursor must be a nonnegative integer")
        # One observe both collects ambient output and serves as the freshness check.
        seen_up_to = self.last_cursor if if_cursor is None else min(self.last_cursor, if_cursor)
        before = self.call(op="observe", after=seen_up_to)
        if if_cursor is not None and before["cursor"] != if_cursor:
            newer = [e for e in before["events"] if e["seq"] > if_cursor]
            self.last_cursor = before["cursor"]
            return {"sent": False, "stale": True, "cursor": before["cursor"],
                    "text": annotated(newer),
                    "note": "the game moved since your decision cursor; nothing was sent"}
        reference = self.last_cursor if if_cursor is None else if_cursor
        ambient = [e for e in before["events"] if e["seq"] > reference]
        result = self.call(op="send", command=command, timeout=timeout, quiet=quiet,
                           secret=bool(secret_ref), **self.controller())
        self.last_cursor = result["cursor"]
        response = {"sent": True, "text": annotated(result["events"]),
                    "receipt": {k: result.get(k) for k in ("reason", "delivery", "command_id",
                                                            "command_complete", "cursor",
                                                            "generation", "truncated")}}
        if not secret_ref:
            response["command"] = command
        if any(e["kind"] == "text" for e in ambient):
            response["ambient_before"] = annotated(ambient)
        return response


def process(courier, path):
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError:
        return  # not readable yet (e.g. a Windows sharing lock); leave it for the next poll
    try:
        req = json.loads(raw)
        rid = req.get("id") if isinstance(req, dict) else None
        if not isinstance(rid, str) or not REQUEST_ID.match(rid):
            raise ValueError("missing or malformed id")
        if req.get("op") not in OPS:
            raise ValueError("unknown op")
    except ValueError as exc:
        os.replace(path, DONE / (path.name + ".rejected"))
        log(f"rejected {path.name}: {exc}; nothing done")
        return
    target = OUTBOX / f"res-{rid}.json"
    if rid in courier.seen:
        duplicate = OUTBOX / f"res-{rid}.duplicate.json"
        write_json_atomic(duplicate, {"id": rid, "ok": False, "sent": False, "at": stamp(),
                                      "error": "duplicate request id: already processed once; nothing sent"})
        os.replace(path, DONE / (path.name + ".duplicate"))
        log(f"{rid} DUPLICATE id refused; nothing sent")
        return
    # Record and dequeue first: from here on this request can never run again.
    ledger_append({"id": rid, "op": req["op"], "at": stamp()})
    courier.seen.add(rid)
    os.replace(path, DONE / path.name)
    try:
        if req["op"] not in READ_ONLY:
            created, ttl = req.get("created"), req.get("ttl")
            if (type(created) not in (int, float) or type(ttl) not in (int, float)
                    or not 0 < ttl <= MAX_TTL):
                raise ValueError("state-changing requests need numeric created and ttl (0-600 s); nothing sent")
            age = time.time() - created
            if age > ttl:
                raise ValueError(f"expired: decided {age:.0f}s ago, ttl {ttl:.0f}s; nothing sent")
        response = {"id": rid, "op": req["op"], "ok": True, **courier.handle(req)}
    except Exception as exc:  # report, never retry
        response = {"id": rid, "op": req["op"], "ok": False, "error": str(exc) or type(exc).__name__}
        if req["op"] == "send" and "nothing sent" not in response["error"]:
            response["warning"] = "outcome may be unknown: observe before deciding again; never resend automatically"
    response["at"] = stamp()
    write_json_atomic(target, response)
    shown = " [password]" if req.get("secret_ref") else (f" {req.get('command')!r}" if req["op"] == "send" else "")
    if response["ok"] and response.get("receipt"):
        outcome = f"{response['receipt']['reason']}, {response['receipt']['command_id']}"
    elif response["ok"]:
        outcome = "stale, nothing sent" if response.get("stale") else "ok"
    else:
        outcome = "ERROR: " + response["error"]
    log(f"{req['op']}{shown} -> {outcome}")


def main():
    for folder in (INBOX, OUTBOX, DONE):
        folder.mkdir(parents=True, exist_ok=True)
    if not PROFILE.exists():
        sys.exit(f"missing {PROFILE}")
    existing = broker_status()
    broker = broker_log = None
    if existing is not None:
        # E.g. the courier window was closed: its broker lives in its own session and
        # survives. Attach to it instead of failing on its control-file lock.
        log("a broker for this control file is already running; attaching to it")
    else:
        environment = {**os.environ, "PYTHONPATH": str(SOURCE)}
        broker_log = open(HERE / "broker.log", "a", encoding="utf-8")
        isolation = ({"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP} if os.name == "nt"
                     else {"start_new_session": True})
        broker = subprocess.Popen(
            [sys.executable, "-m", "mudgpt", "--control", str(CONTROL), "serve", "--profile", str(PROFILE)],
            cwd=str(HERE), env=environment, stdout=broker_log, stderr=subprocess.STDOUT, **isolation)
        deadline = time.monotonic() + 20
        while True:
            if broker.poll() is not None:
                sys.exit("broker exited during startup; see broker.log beside this script")
            if broker_status() is not None:
                break
            if time.monotonic() > deadline:
                broker.terminate()
                sys.exit("broker did not become ready; see broker.log")
            time.sleep(0.2)
    courier = Courier(existing)
    where = "attached to a running broker" if broker is None else f"broker pid {broker.pid}"
    log(f"Krasna's mailbox courier is up ({where}). Watching {INBOX}")
    log(f"Ctrl+C stops it (quit in-game first). Take over anytime: {TAKEOVER_HINT}")
    try:
        last_check = time.monotonic()
        while True:
            if broker is not None and broker.poll() is not None:
                log("broker exited on its own; courier stopping")
                break
            if broker is None and time.monotonic() - last_check > 5:
                last_check = time.monotonic()
                if broker_status() is None:
                    log("the attached broker stopped answering; courier stopping")
                    break
            for path in sorted(INBOX.glob("*.json")):
                process(courier, path)
            time.sleep(0.25)
    except KeyboardInterrupt:
        log("Ctrl+C: stopping. This closes the game socket; it is not an in-game save.")
    finally:
        try:
            request({"op": "claim", "owner": "human", "takeover": True}, CONTROL)
            request({"op": "stop", "owner": "human"}, CONTROL)
        except Exception:
            pass
        if broker is not None:
            try:
                broker.wait(timeout=8)
            except subprocess.TimeoutExpired:
                broker.terminate()
        if broker_log is not None:
            broker_log.close()


if __name__ == "__main__":
    main()
