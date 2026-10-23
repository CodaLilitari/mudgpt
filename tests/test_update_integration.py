"""Real launcher/SDK processes; synthetic updater transport; no game or broker.

Fixtures live outside this checkout. Set MUDWEAVE_TEST_STORAGE explicitly on
Linux/WSL; the Windows default is the project's designated external storage.
The exact current wrapper and updater are copied, never reimplemented. A loader
replaces only the updater's JSON transport so a blocked/offline check is
deterministic and cannot contact GitLab. HTTP response validation is a separate
unit-test responsibility.
"""
import asyncio
import base64
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

from mcp import ClientSession, StdioServerParameters
import mcp.client.stdio as sdk_stdio


ROOT = Path(__file__).resolve().parents[1]
CODEX_SOURCE = ROOT / "iterations" / "codex" / "src"
CHECKER = '''"""Load the real updater with a synthetic JSON transport."""
import importlib.util
import os
from pathlib import Path
import sys
import threading

root = Path(os.environ["MUDWEAVE_FIXTURE"])
spec = importlib.util.spec_from_file_location("fixture_actual_updater", root / "updater_actual.py")
actual = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = actual
spec.loader.exec_module(actual)

def fetch_json(url):
    mode = os.environ["MUDWEAVE_CHECK_MODE"]
    print("[fixture update] transport " + mode, file=sys.stderr, flush=True)
    (root / "check-entered").write_text(url, encoding="utf-8")
    if mode == "offline":
        raise OSError("synthetic offline transport")
    threading.Event().wait()  # Must not delay target startup or exit.

actual._fetch_json = fetch_json
real_check = actual.check
def observed_check(*args, **kwargs):
    try:
        return real_check(*args, **kwargs)
    finally:
        (root / "check-finished").write_text("finished", encoding="ascii")
actual.check = observed_check
start_check = actual.start_check
main = actual.main
'''
TARGET = '''import base64, json, os, pathlib, sys, time
root = pathlib.Path(os.environ["MUDWEAVE_FIXTURE"])
deadline = time.monotonic() + 6
while not (root / "check-entered").exists():
    if time.monotonic() >= deadline:
        raise RuntimeError("checker never started")
    time.sleep(.01)
body = {"argv": sys.argv[1:], "cwd": os.getcwd(),
        "executable": sys.executable, "sentinel": os.environ["FIXTURE_SENTINEL"],
        "stdin_base64": base64.b64encode(sys.stdin.buffer.read()).decode("ascii")}
sys.stdout.buffer.write(json.dumps(body, ensure_ascii=False).encode("utf-8") + b"\\n")
sys.stdout.buffer.flush()
sys.stderr.buffer.write(b"fixture target stderr\\n")
sys.stderr.buffer.flush()
raise SystemExit(23)
'''


def external_storage():
    destination = Path(os.environ.get(
        "MUDWEAVE_TEST_STORAGE", Path.home() / "Documents" / "MudGPT Build Storage"
    )).absolute()
    # Check each existing ancestor before creating anything. Resolve alone would
    # conceal an input junction/symlink that happens to end outside the checkout.
    for current in (destination, *destination.parents):
        if current.is_symlink():
            raise RuntimeError("test storage must not traverse a symlink")
        if current.exists():
            flags = getattr(current.lstat(), "st_file_attributes", 0)
            if flags & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0):
                raise RuntimeError("test storage must not traverse a reparse point")
        if (current / ".git").exists():
            raise RuntimeError("test storage must have no Git ancestor")
    destination.mkdir(parents=True, exist_ok=True)
    return destination


class UpdateLauncherIntegrationTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(
            prefix="mudweave launch integration ", dir=external_storage()
        )
        self.addCleanup(temporary.cleanup)
        self.home = Path(temporary.name) / "spaces and Unicode \u03bb \u65e5\u672c"
        self.home.mkdir()
        self.wrapper = self.home / "mudweave.py"
        shutil.copyfile(ROOT / "mudweave.py", self.wrapper)
        self.assertEqual(self.wrapper.read_bytes(), (ROOT / "mudweave.py").read_bytes())
        shutil.copyfile(ROOT / "mudweave_update.py", self.home / "updater_actual.py")
        self.assertEqual((self.home / "updater_actual.py").read_bytes(),
                         (ROOT / "mudweave_update.py").read_bytes())
        (self.home / "mudweave_update.py").write_text(CHECKER, encoding="utf-8")
        (self.home / "mudweave-update.json").write_text(json.dumps({
            "schema_version": 1, "project_id": 87773838, "iteration": "codex",
            "collection_version": "0.2.1", "enabled": True,
        }), encoding="utf-8")
        self.env = dict(os.environ, MUDWEAVE_FIXTURE=str(self.home),
                        MUDWEAVE_CHECK_MODE="blocked", PYTHONUTF8="1",
                        PYTHONDONTWRITEBYTECODE="1",
                        FIXTURE_SENTINEL="value with spaces \u03bb \u65e5\u672c")
        self.env["LOCALAPPDATA"] = self.env["XDG_CACHE_HOME"] = str(self.home / "cache")
        # Select the public Codex source explicitly, never another editable core.
        self.env["PYTHONPATH"] = str(CODEX_SOURCE)

    def test_real_run_preserves_arguments_streams_environment_and_nonzero_exit(self):
        target = self.home / "target with spaces \u03bb.py"
        target.write_text(TARGET, encoding="utf-8")
        arguments = ["one space", "\u03bb-\u65e5\u672c\u8a9e", "--literal", 'quote"d', "trailing\\"]
        input_bytes = b"input\x00bytes\r\n" + "Unicode \u03bb".encode("utf-8")
        result = subprocess.run(
            [sys.executable, str(self.wrapper), "run", "--", str(target), *arguments],
            input=input_bytes, capture_output=True, cwd=self.home, env=self.env,
            timeout=12,
        )
        self.assertEqual(result.returncode, 23, result.stderr.decode("utf-8"))
        # A stray updater banner on stdout makes this exact JSON parse fail.
        data = json.loads(result.stdout.decode("utf-8"))
        self.assertEqual(data["argv"], arguments)
        self.assertEqual(Path(data["cwd"]).resolve(), self.home.resolve())
        self.assertEqual(Path(data["executable"]).resolve(), Path(sys.executable).resolve())
        self.assertEqual(data["sentinel"], self.env["FIXTURE_SENTINEL"])
        self.assertEqual(base64.b64decode(data["stdin_base64"]), input_bytes)
        self.assertCountEqual(result.stderr.decode("utf-8").splitlines(),
                              ["fixture target stderr", "[fixture update] transport blocked"])
        self.assertTrue((self.home / "check-entered").exists())
        self.assertFalse((self.home / "check-finished").exists())

    def test_actual_codex_mcp_starts_while_checker_is_blocked_and_when_offline(self):
        for mode in ("blocked", "offline"):
            with self.subTest(transport=mode):
                for name in ("check-entered", "check-finished"):
                    (self.home / name).unlink(missing_ok=True)
                env = dict(self.env, MUDWEAVE_CHECK_MODE=mode)
                env["LOCALAPPDATA"] = env["XDG_CACHE_HOME"] = str(self.home / (mode + "-cache"))
                control = self.home / "absent broker control.json"
                stderr_path = self.home / (mode + "-mcp-stderr.txt")
                wire_lines = []
                original_parse = sdk_stdio._parse_line

                def inspect_line(line):
                    wire_lines.append(line)
                    return original_parse(line)

                async def scenario(stderr):
                    parameters = StdioServerParameters(
                        command=sys.executable,
                        args=[str(self.wrapper), "broker", "--control", str(control), "mcp"],
                        cwd=str(self.home), env=env,
                    )
                    async with sdk_stdio.stdio_client(parameters, errlog=stderr) as streams:
                        async with ClientSession(*streams, read_timeout_seconds=6) as client:
                            result = await client.initialize()
                            self.assertEqual(result.server_info.name, "MudGPT")
                            tools = await client.list_tools()
                            self.assertEqual({tool.name for tool in tools.tools}, {
                                "mud_status", "mud_observe", "mud_claim", "mud_release", "mud_send"
                            })
                            deadline = time.monotonic() + 2
                            while not (self.home / "check-entered").exists():
                                self.assertLess(time.monotonic(), deadline,
                                                "checker did not enter during MCP startup")
                                await asyncio.sleep(.01)
                            if mode == "blocked":
                                self.assertFalse((self.home / "check-finished").exists())
                            else:
                                while not (self.home / "check-finished").exists():
                                    self.assertLess(time.monotonic(), deadline,
                                                    "offline checker did not settle")
                                    await asyncio.sleep(.01)
                            # No tool call: initialization/listing requires no broker,
                            # control bearer, credentials, connection, or game command.
                            self.assertFalse(control.exists())

                with stderr_path.open("w", encoding="utf-8") as stderr:
                    # Instrument the installed SDK parser without replacing its
                    # validation or transport. Every observed stdout line is checked.
                    with patch.object(sdk_stdio, "_parse_line", side_effect=inspect_line):
                        asyncio.run(scenario(stderr))
                self.assertGreaterEqual(len(wire_lines), 2)
                for line in wire_lines:
                    message = json.loads(line)
                    self.assertEqual(message.get("jsonrpc"), "2.0", line)
                self.assertIn("[fixture update] transport " + mode,
                              stderr_path.read_text(encoding="utf-8"))
                self.assertEqual((self.home / "check-entered").read_text(encoding="utf-8"),
                                 "https://gitlab.com/api/v4/projects/87773838/releases/permalink/latest")
                self.assertFalse(control.exists())


if __name__ == "__main__":
    unittest.main()
