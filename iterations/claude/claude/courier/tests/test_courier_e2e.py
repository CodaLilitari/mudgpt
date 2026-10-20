"""End-to-end checks for the mailbox courier against a synthetic, LoK-shaped MUD (loopback only).

Run from anywhere, on Linux or WSL (the restart scenario uses pgrep):

    MUDGPT_REPO=/path/to/MudGPT  MUDGPT_PYTHON=/path/to/MudGPT/.venv/bin/python \
        python3 claude/courier/tests/test_courier_e2e.py

MUDGPT_REPO defaults to the repository that contains this file; MUDGPT_PYTHON defaults to
that repo's .venv interpreter (Python 3.11+ with MudGPT's dependencies). Each scenario builds
a throwaway courier home in a temporary folder: a copy of the courier, the repo's src/, a
profile pointing at the fixture, and a fixture-only password. Nothing touches a real game,
a real profile or real credentials. Exit status is 0 only if every check passes.
"""
import json
import os
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path

TESTS = Path(__file__).resolve().parent
COURIER = TESTS.parent
REPO = Path(os.environ.get("MUDGPT_REPO") or COURIER.parents[1]).resolve()
_venv = REPO / ".venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
PY = os.environ.get("MUDGPT_PYTHON") or (str(_venv) if _venv.exists() else sys.executable)
FIXTURE_PASSWORD = "Kx-fixtureOnly-7q2Z"   # throwaway; exists only in this test
PROMPT = r"(?:<\d+hp/\d+hp/\d+m/\d+m/\d+mv/\d+mv>|<\d+hp \d+m \d+mv>)\s*$"
results = []


def check(name, ok, detail=""):
    results.append(bool(ok))
    print(f"{'PASS' if ok else 'FAIL'}  {name}" + (f"  [{detail}]" if detail else ""), flush=True)


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def make_home(port):
    home = Path(tempfile.mkdtemp(prefix="courier-home-"))
    for name in ("mailbox_courier.py", "krasna.py"):
        shutil.copy2(COURIER / name, home / name)
    shutil.copytree(REPO / "src", home / "src", ignore=shutil.ignore_patterns("__pycache__", "*.egg-info"))
    (home / "profile.json").write_text(json.dumps(
        {"host": "127.0.0.1", "port": port, "tls": False, "prompt_pattern": PROMPT}), encoding="utf-8")
    (home / "credentials.txt").write_text(
        f"# fixture-only test credentials\ncharacter=Testkestra\npassword={FIXTURE_PASSWORD}\n", encoding="utf-8")
    return home


def start_fixture(port, log):
    proc = subprocess.Popen([sys.executable, "-I", str(TESTS / "fake_lok.py"), str(port), str(log)],
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    proc.stdout.readline()  # "fake LoK listening <port>"
    return proc


class Home:
    """One courier home plus helpers to talk to it the way Red does."""

    def __init__(self, port, control=None):
        self.dir = make_home(port)
        self.env = {**os.environ}
        self.env.pop("KRASNA_CONTROL", None)
        if control:
            self.env["KRASNA_CONTROL"] = str(control)
        self.control = Path(control) if control else self.dir / "control.json"
        self.box = self.dir / "mailbox"

    def krasna(self, *args, wait=20):
        out = subprocess.run([PY, str(self.dir / "krasna.py"), *args, "--wait", str(wait)],
                             capture_output=True, text=True, timeout=wait + 15, env=self.env)
        lines = out.stdout.strip().splitlines()
        meta = json.loads(lines[-1]) if lines and lines[-1].startswith("{") else {}
        return out.stdout, meta

    def cli(self, *args):
        env = {**self.env, "PYTHONPATH": str(self.dir / "src")}
        return subprocess.run([PY, "-m", "mudgpt", "--control", str(self.control), *args],
                              capture_output=True, text=True, env=env, cwd=str(self.dir))

    def start_courier(self, logname):
        log = self.dir / logname
        proc = subprocess.Popen([PY, str(self.dir / "mailbox_courier.py")], cwd=str(self.dir), env=self.env,
                                stdout=open(log, "w"), stderr=subprocess.STDOUT)
        end = time.time() + 20
        while time.time() < end and "courier is up" not in log.read_text():
            time.sleep(0.2)
        return proc, log

    def drop(self, req, name=None):
        inbox = self.box / "in"
        inbox.mkdir(parents=True, exist_ok=True)
        path = inbox / (name or f"{req.get('id', 'x')}.json")
        part = path.with_suffix(".part")
        part.write_text(json.dumps(req) if isinstance(req, dict) else req, encoding="utf-8")
        os.rename(part, path)

    def wait_response(self, rid, suffix="", timeout=10):
        target = self.box / "out" / f"res-{rid}{suffix}.json"
        end = time.time() + timeout
        while time.time() < end:
            if target.exists():
                return json.loads(target.read_text(encoding="utf-8"))
            time.sleep(0.2)
        return None


def scenario_mailbox_rules():
    print("== mailbox rules ==")
    port = free_port()
    home = Home(port)
    cmdlog = home.dir / "fixture-commands.log"
    fixture = start_fixture(port, cmdlog)
    courier, log = home.start_courier("courier.log")
    try:
        check("courier starts and launches its broker", "courier is up" in log.read_text())
        home.krasna("connect")
        time.sleep(1.5)
        text, meta = home.krasna("observe")
        check("connect + observe shows the greeting", "By what name" in text, f"cursor {meta.get('cursor')}")
        text, meta = home.krasna("claim")
        check("claim hands control to 'red'", '"owner": "red"' in text)
        for step in ("Testkestra", "y"):
            text, meta = home.krasna("send", step)
        check("name + confirm reach the password prompt", "password" in text.lower())
        home.krasna("secret")
        text, meta = home.krasna("secret")
        check("password twice via secret_ref", meta.get("sent") is True and "Hit Return" in text)
        text, meta = home.krasna("send", "")
        cursor = meta["receipt"]["cursor"]
        check("pager -> first room", "Entrance Hall" in text, f"cursor {cursor}")

        time.sleep(7)  # the fixture's hooded stranger arrives ~6 s after the room
        text, meta = home.krasna("send", "look sign", "--if-cursor", str(cursor))
        check("stale decision is refused, nothing sent", meta.get("sent") is False and "hooded figure" in text)
        text, meta = home.krasna("send", "look sign", "--if-cursor", str(meta["cursor"]))
        check("fresh decision is carried", meta.get("sent") is True and "The sign reads" in text)

        home.drop({"id": "expired-001", "op": "send", "command": "north", "created": time.time() - 300, "ttl": 120})
        response = home.wait_response("expired-001")
        check("expired request is refused, nothing sent",
              response and not response["ok"] and "expired" in response["error"])
        home.drop({"id": "expired-001", "op": "send", "command": "north", "created": time.time(), "ttl": 120},
                  name="replay-attempt.json")
        response = home.wait_response("expired-001", ".duplicate")
        check("re-dropped id is refused as duplicate", response and "duplicate" in response["error"])
        home.drop("{not json", name="garbage.json")
        time.sleep(1)
        check("malformed request is parked, not run", (home.box / "in" / "done" / "garbage.json.rejected").exists())

        takeover = home.cli("claim", "human", "--takeover")
        check("the human can take over from a terminal",
              takeover.returncode == 0 and '"owner": "human"' in takeover.stdout)
        text, meta = home.krasna("send", "north")
        check("after takeover, red's send is refused",
              meta.get("ok") is False and "does not own" in meta.get("error", "").lower(), meta.get("error", ""))
        text, meta = home.krasna("claim")
        check("after takeover, red cannot re-claim on her own",
              meta.get("ok") is False and "taken over" in meta.get("error", ""), meta.get("error", ""))
        release = home.cli("release", "human")
        text, meta = home.krasna("claim")
        check("after the human releases, red can claim again", release.returncode == 0 and '"owner": "red"' in text)
        text, meta = home.krasna("send", "look")
        check("and her next command is carried", meta.get("sent") is True and "Entrance Hall" in text)

        haystacks = {p.name: p.read_text(encoding="utf-8", errors="replace")
                     for p in list((home.box / "out").glob("*.json")) + list((home.box / "in" / "done").glob("*"))
                     + [home.box / "ledger.jsonl", log, home.dir / "broker.log"] if p.exists()}
        leaks = [name for name, body in haystacks.items() if FIXTURE_PASSWORD in body]
        check("password absent from mailbox, ledger and logs", not leaks, f"{len(haystacks)} files; leaks={leaks}")
        received = cmdlog.read_text(encoding="utf-8").splitlines()
        expected = ["Testkestra", "y", "<password>", "<password>", "", "look sign", "look"]
        check("fixture received exactly the carried commands, once each", received == expected, f"{received}")
    finally:
        courier.send_signal(signal.SIGINT)
        try:
            courier.wait(timeout=12)
        except subprocess.TimeoutExpired:
            courier.kill()
        fixture.terminate()
        fixture.wait(timeout=5)
    time.sleep(0.5)
    check("Ctrl+C stops the broker and removes its control file", not home.control.exists())
    shutil.rmtree(home.dir, ignore_errors=True)


def scenario_restart_and_attach():
    print("== courier restart, attach, takeover ==")
    port_a, port_b = free_port(), free_port()
    control_dir = Path(tempfile.mkdtemp(prefix="courier-ctl-"))
    home = Home(port_a, control=control_dir / "control.json")
    log_a, log_b = home.dir / "fixture-a.log", home.dir / "fixture-b.log"
    fixtures = [start_fixture(port_a, log_a), start_fixture(port_b, log_b)]

    def broker_pids():
        out = subprocess.run(["pgrep", "-f", f"mudgpt --control {home.control} serve"], capture_output=True, text=True)
        return out.stdout.split()

    def set_port(port):
        profile = json.loads((home.dir / "profile.json").read_text())
        profile["port"] = port
        (home.dir / "profile.json").write_text(json.dumps(profile))

    try:
        c1, log1 = home.start_courier("courier-1.log")
        check("courier 1 starts its own broker", "broker pid" in log1.read_text())
        home.krasna("connect")
        time.sleep(1.2)
        text, meta = home.krasna("observe")
        check("connected to server A", "By what name" in text)
        text, meta = home.krasna("claim")
        check("red holds control", '"owner": "red"' in text)

        c1.send_signal(signal.SIGKILL)
        c1.wait(5)
        time.sleep(0.5)
        check("broker survives the killed courier", bool(broker_pids()) and home.cli("status").returncode == 0,
              f"broker pids {broker_pids()}")

        set_port(port_b)
        c2, log2 = home.start_courier("courier-2.log")
        body = log2.read_text()
        check("courier 2 attaches instead of failing on the lock", "attaching" in body)
        check("courier 2 recovers Red's orphaned claim", "recovered Red's control" in body)
        text, meta = home.krasna("claim")
        check("red can claim again", '"owner": "red"' in text)
        home.krasna("close")
        text, meta = home.krasna("connect")
        check("connect reads the fresh profile (server B)", meta.get("ok") and str(port_b) in text)
        time.sleep(1.2)
        home.krasna("observe")
        home.krasna("send", "Testkestra")
        a = log_a.read_text() if log_a.exists() else ""
        b = log_b.read_text() if log_b.exists() else ""
        check("the name went to server B only", "Testkestra" in b and "Testkestra" not in a)

        check("the human takes over", home.cli("claim", "human", "--takeover").returncode == 0)
        c2.send_signal(signal.SIGKILL)
        c2.wait(5)
        c3, log3 = home.start_courier("courier-3.log")
        text, meta = home.krasna("claim")
        check("after restart, red still waits for the human",
              meta.get("ok") is False and "taken over" in meta.get("error", ""), meta.get("error", ""))
        home.cli("release", "human")
        text, meta = home.krasna("claim")
        check("after the human releases, red claims again", '"owner": "red"' in text)

        c3.send_signal(signal.SIGINT)
        c3.wait(12)
        time.sleep(0.5)
        check("Ctrl+C stops the attached broker too", not broker_pids() and not home.control.exists(),
              f"pids={broker_pids()} control_exists={home.control.exists()}")
    finally:
        for proc in fixtures:
            proc.terminate()
        for pid in broker_pids():
            os.kill(int(pid), signal.SIGKILL)
    shutil.rmtree(home.dir, ignore_errors=True)
    shutil.rmtree(control_dir, ignore_errors=True)


if __name__ == "__main__":
    print(f"repo {REPO}\npython {PY}")
    scenario_mailbox_rules()
    scenario_restart_and_attach()
    print(f"{sum(results)}/{len(results)} checks passed")
    sys.exit(0 if results and all(results) else 1)
