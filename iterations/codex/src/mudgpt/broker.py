"""Loopback-only broker. MCP adapters and CLI calls share the same connection."""
import asyncio
from contextlib import contextmanager
import hmac
import json
import os
from pathlib import Path
import secrets
import threading
import urllib.error
import urllib.request
from http.client import HTTPException
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from .session import Session


def default_control():
    return Path(os.environ.get("MUDGPT_CONTROL", ".private/control.json")).resolve()


def request(payload, control=None):
    config = json.loads(Path(control or default_control()).read_text(encoding="utf-8"))
    port = int(config["port"])
    if not 1 <= port <= 65535:
        raise ValueError("invalid local broker port")
    req = urllib.request.Request(
        f"http://127.0.0.1:{port}/rpc", json.dumps(payload).encode(),
        {"Content-Type": "application/json", "Authorization": f"Bearer {config['token']}"},
        method="POST",
    )
    # Do not route loopback credentials through environment-configured proxies.
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(req, timeout=30) as response:
            result = json.load(response)
    except urllib.error.HTTPError as exc:
        try:
            result = json.load(exc)
        except (ValueError, OSError):
            raise RuntimeError(f"broker HTTP {exc.code}; outcome unknown for mutations; inspect status before retrying") from None
        finally:
            exc.close()
    except (OSError, HTTPException) as exc:
        raise RuntimeError("Broker transport failed; outcome unknown for mutations. "
                           "Do not replay a command. Reconnect to the broker and inspect status/observations.") from exc
    if "error" in result:
        raise RuntimeError(result["error"])
    return result


@contextmanager
def _control_lock(control):
    """One broker per control path; OS releases this lock on process exit/crash."""
    control.parent.mkdir(parents=True, exist_ok=True)
    # Keep the lock inode: unlinking it permits two different files to be locked.
    lock = open(control.with_name(control.name + ".lock"), "a+b")
    try:
        if os.name == "nt":
            import msvcrt
            if os.fstat(lock.fileno()).st_size == 0:
                lock.write(b"\0")
                lock.flush()
            lock.seek(0)
            try:
                msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
            except OSError:
                raise RuntimeError("a broker already holds this control file lock") from None
        else:
            import fcntl
            try:
                fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError:
                raise RuntimeError("a broker already holds this control file lock") from None
        yield
    finally:
        lock.close()


def serve(control=None, profile=None):
    control = Path(control or default_control()).resolve()
    with _control_lock(control):
        _serve_locked(control, profile)


def _serve_locked(control, profile):
    if control.exists():
        try:
            request({"op": "status"}, control)
        except (OSError, ValueError, RuntimeError):
            pass
        else:
            raise RuntimeError("a broker is already running for this control file")
    settings = json.loads(Path(profile).read_text(encoding="utf-8-sig")) if profile else {}
    allowed = {"host", "port", "tls", "prompt_pattern"}
    if set(settings) - allowed:
        raise ValueError("profile supports only host, port, tls and prompt_pattern; no credentials")
    loop = asyncio.new_event_loop()
    worker = threading.Thread(target=loop.run_forever, daemon=True)
    worker.start()
    # asyncio primitives bind when first used, inside the broker loop.
    session = Session(prompt_pattern=settings.get("prompt_pattern"))
    token = secrets.token_hex(32)

    async def dispatch(data):
        op = data.get("op")
        if op == "status":
            return session.status()
        if op == "observe":
            after = data.get("after", 0)
            if type(after) is not int or after < 0:
                raise ValueError("after must be a nonnegative integer")
            return session.observe(after, expected_broker_id=data.get("expected_broker_id"))
        if op == "claim":
            return session.claim(data.get("owner", "human"), takeover=data.get("takeover", False),
                                 lease=data.get("lease"))
        if op == "release":
            return session.release(data.get("owner", "human"), lease=data.get("lease"))
        owner = data.get("owner", "human")
        session.check_controller(owner, data.get("lease"))
        if op == "connect":
            host = data.get("host") or settings.get("host")
            port = data.get("port") or settings.get("port")
            tls = data.get("tls", settings.get("tls", False))
            if not isinstance(host, str) or not host or len(host) > 253:
                raise ValueError("a host is required")
            if type(port) is not int or not 1 <= port <= 65535:
                raise ValueError("port must be 1..65535")
            if type(tls) is not bool:
                raise ValueError("tls must be true or false")
            return await session.connect(host, port, tls=tls)
        if op == "send":
            timeout = data.get("timeout", 3)
            quiet = data.get("quiet", 0.3)
            if type(timeout) not in (int, float) or not 0.1 <= timeout <= 15:
                raise ValueError("timeout must be 0.1..15 seconds")
            if type(quiet) not in (int, float) or not 0.05 <= quiet <= timeout:
                raise ValueError("quiet must be 0.05..timeout seconds")
            return await session.send(data.get("command"), owner=owner, timeout=timeout,
                                      quiet=quiet, secret=data.get("secret", False), lease=data.get("lease"),
                                      expected_broker_id=data.get("expected_broker_id"),
                                      expected_generation=data.get("expected_generation"),
                                      expected_cursor=data.get("expected_cursor"),
                                      expires_at=data.get("expires_at"))
        if op in ("close", "stop"):
            await session.close()
            if op == "stop":
                threading.Thread(target=server.shutdown, daemon=True).start()
            return session.status()
        raise ValueError("unknown operation")

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass  # Never write request bodies, authorization or commands to logs.

        def do_POST(self):
            if self.path != "/rpc":
                self.reply(404, {"error": "not found"})
                return
            auth = self.headers.get("Authorization", "")
            if self.headers.get("Origin") or not hmac.compare_digest(auth, f"Bearer {token}"):
                self.reply(403, {"error": "local broker authentication required"})
                return
            future = None
            try:
                self.connection.settimeout(5)
                size = int(self.headers.get("Content-Length", 0))
                if not 0 < size <= 8192:
                    raise ValueError("request must be 1..8192 bytes")
                data = json.loads(self.rfile.read(size))
                if not isinstance(data, dict):
                    raise ValueError("object required")
                future = asyncio.run_coroutine_threadsafe(dispatch(data), loop)
                self.reply(200, future.result(timeout=25))
            except Exception as exc:
                if future and not future.done():
                    future.cancel()
                self.reply(400, {"error": str(exc) or type(exc).__name__})

        def reply(self, code, value):
            encoded = json.dumps(value, ensure_ascii=True).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(encoded)))
            self.end_headers()
            try:
                self.wfile.write(encoded)
            except OSError:
                pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server.daemon_threads = True
    control.parent.mkdir(parents=True, exist_ok=True)
    # POSIX chmod is enforced there; Windows uses the user's existing folder ACL.
    control.write_text(json.dumps({"port": server.server_port, "token": token,
                                    "pid": os.getpid()}), encoding="utf-8")
    os.chmod(control, 0o600)
    print(f"MudGPT broker ready; control file: {control}", flush=True)
    try:
        server.serve_forever(poll_interval=0.1)
    finally:
        server.server_close()
        asyncio.run_coroutine_threadsafe(session.close(), loop).result(timeout=5)
        loop.call_soon_threadsafe(loop.stop)
        worker.join(timeout=5)
        loop.close()
        if control.exists():
            saved = json.loads(control.read_text(encoding="utf-8"))
            if saved.get("token") == token:
                control.unlink()

