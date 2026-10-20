"""Red's side of the mailbox: write one request, wait for its answer. Never resends.

    python krasna.py connect | status | claim | release | close
    python krasna.py observe [--after N]
    python krasna.py send "look" [--if-cursor N] [--timeout S]
    python krasna.py send ""                      # bare Enter for pagers/menus
    python krasna.py secret                       # sends the password from credentials.txt
    python krasna.py wait <request-id>            # re-check an answer that was slow to arrive
    python krasna.py lock --holder LABEL [--minutes 60]   # one Red session at a time
    python krasna.py unlock --holder LABEL
    python krasna.py note "text"                  # append a timestamped line to journal.md
"""
import argparse
import json
import os
import secrets
import time
from pathlib import Path

HOME = Path(__file__).resolve().parent
BOX = HOME / "mailbox"
LOCK = HOME / "session.json"
JOURNAL = HOME / "journal.md"


def lock(holder, minutes):
    now = time.time()
    try:
        current = json.loads(LOCK.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        current = {}
    if current.get("holder") not in (None, holder) and current.get("until", 0) > now:
        left = (current["until"] - now) / 60
        print(f"LOCKED by {current['holder']} for another {left:.0f} min; do not play now.")
        raise SystemExit(3)
    LOCK.write_text(json.dumps({"holder": holder, "since": now, "until": now + minutes * 60,
                                "since_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now))}),
                    encoding="utf-8")
    print(f"LOCK ACQUIRED by {holder} for {minutes:.0f} min")


def unlock(holder):
    try:
        current = json.loads(LOCK.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        current = {}
    if current.get("holder") not in (None, holder):
        print(f"not unlocking: held by {current.get('holder')}")
        raise SystemExit(3)
    current.update({"holder": holder, "until": time.time()})
    LOCK.write_text(json.dumps(current), encoding="utf-8")
    print("UNLOCKED")


def note(text):
    stamp = time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime())
    with JOURNAL.open("a", encoding="utf-8") as handle:
        handle.write(f"- {stamp}: {text}\n")
    print("noted")


def show(response):
    if response.get("ambient_before"):
        print("--- arrived while I was deciding ---")
        print(response["ambient_before"].rstrip())
        print("--- response ---")
    if response.get("stale"):
        print(f"STALE: nothing sent. {response.get('note', '')}")
    if response.get("text"):
        print(response["text"].rstrip())
    if response.get("status"):
        print(json.dumps(response["status"]))
    meta = {k: response[k] for k in ("id", "op", "ok", "sent", "cursor", "target", "error", "warning")
            if k in response}
    if response.get("receipt"):
        meta["receipt"] = response["receipt"]
    print(json.dumps(meta))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("op", choices=["connect", "status", "claim", "release", "close",
                                       "observe", "send", "secret", "wait", "lock", "unlock", "note"])
    parser.add_argument("--holder")
    parser.add_argument("--minutes", type=float, default=60)
    parser.add_argument("arg", nargs="?")
    parser.add_argument("--if-cursor", type=int)
    parser.add_argument("--after", type=int)
    parser.add_argument("--timeout", type=float, default=5)
    parser.add_argument("--ttl", type=float, default=120, help="seconds this decision stays valid")
    parser.add_argument("--wait", type=float, default=40, help="seconds to wait for the courier")
    args = parser.parse_args()

    if args.op in ("lock", "unlock"):
        if not args.holder:
            parser.error("lock/unlock need --holder")
        return lock(args.holder, args.minutes) if args.op == "lock" else unlock(args.holder)
    if args.op == "note":
        if not args.arg:
            parser.error("note needs text")
        return note(args.arg)

    if args.op == "wait":
        rid = args.arg
    else:
        rid = time.strftime("%Y%m%dT%H%M%S") + "-" + secrets.token_hex(3)
        req = {"id": rid, "op": "send" if args.op == "secret" else args.op,
               "created": time.time(), "ttl": args.ttl}
        if args.op == "send":
            if args.arg is None:
                parser.error("send needs a command (use \"\" for a bare Enter)")
            req["command"] = args.arg
        if args.op == "secret":
            req["secret_ref"] = "password"
        if req["op"] == "send":
            req["timeout"] = args.timeout
            if args.if_cursor is not None:
                req["if_cursor"] = args.if_cursor
        if args.op == "observe" and args.after is not None:
            req["after"] = args.after
        inbox = BOX / "in"
        inbox.mkdir(parents=True, exist_ok=True)
        partial = inbox / f"{rid}.part"
        partial.write_text(json.dumps(req), encoding="utf-8")
        os.rename(partial, inbox / f"{rid}.json")

    target = BOX / "out" / f"res-{rid}.json"
    deadline = time.monotonic() + args.wait
    while time.monotonic() < deadline:
        if target.exists():
            try:
                show(json.loads(target.read_text(encoding="utf-8")))
                return
            except ValueError:
                pass
        time.sleep(0.25)
    print(f"NO ANSWER YET for {rid}. Do not resend; check with: krasna.py wait {rid}")


if __name__ == "__main__":
    main()
