"""MUDweave's stdlib-only, check-and-notify updater. Never installs or executes.

Distribution settings live beside this file. Preferences, metadata and explicitly
requested downloads live only in a guarded per-user cache, outside Git trees.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import http.client
import json
import math
import os
from pathlib import Path
import re
import stat
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

sys.dont_write_bytecode = True
ORIGIN = "https://gitlab.com"
PROJECT_ID = 87773838
PROJECT_PATH = "codalilitari/mudgpt"
ITERATIONS = ("codex", "claude", "muse", "collection")
DAY = 86400
MAX_JSON = 512 * 1024
MAX_ARCHIVE = 128 * 1024 * 1024
TIMEOUT = 8
INSTALL_DIR = Path(__file__).absolute().parent
_THREAD_LOCK = threading.Lock()
_THREADS = {}


class UpdateError(Exception):
    """Only fixed public error codes may cross the API boundary."""
    def __init__(self, code):
        self.code = code


def _need(ok, code="invalid_metadata"):
    if not ok:
        raise UpdateError(code)


def _version(value):
    _need(isinstance(value, str) and len(value) <= 32 and
          re.fullmatch(r"(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)", value))
    return tuple(map(int, value.split(".")))


def _timestamp(value):
    _need(isinstance(value, str) and len(value) < 80)
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        _need(parsed.tzinfo is not None)
        return parsed.timestamp()
    except (ValueError, OverflowError):
        raise UpdateError("invalid_metadata") from None


def _iso(value):
    return datetime.fromtimestamp(value, timezone.utc).isoformat().replace("+00:00", "Z")


def _json_bytes(raw):
    _need(len(raw) <= MAX_JSON)
    def unique(items):
        result = {}
        for key, value in items:
            _need(key not in result)
            result[key] = value
        return result
    try:
        value = json.loads(raw.decode("utf-8"), object_pairs_hook=unique,
                           parse_constant=lambda _: (_ for _ in ()).throw(UpdateError("invalid_metadata")))
    except (ValueError, UnicodeError, RecursionError):
        raise UpdateError("invalid_metadata") from None
    _need(isinstance(value, dict))
    return value


def load_settings(settings=None):
    try:
        if settings is None:
            path = INSTALL_DIR / "mudweave-update.json"
            _need(not path.is_symlink() and path.stat().st_size <= 4096, "configuration_error")
            settings = _json_bytes(path.read_bytes())
        _need(isinstance(settings, dict), "configuration_error")
        _need(type(settings.get("schema_version")) is int and settings["schema_version"] == 1, "configuration_error")
        _need(type(settings.get("project_id")) is int and settings["project_id"] == PROJECT_ID, "configuration_error")
        _need(settings.get("iteration") in ITERATIONS, "configuration_error")
        _version(settings.get("collection_version"))
        _need(type(settings.get("enabled", True)) is bool, "configuration_error")
        return {key: settings[key] for key in ("schema_version", "project_id", "iteration", "collection_version")} | {
            "enabled": settings.get("enabled", True)}
    except (OSError, UpdateError, TypeError, KeyError):
        raise UpdateError("configuration_error") from None


def _guard(path, *, directory=False):
    path = Path(path)
    _need(path.is_absolute() and ".." not in path.parts, "cache_unavailable")
    for part in (path, *path.parents):
        _need(not part.is_symlink() and not getattr(part, "is_junction", lambda: False)(), "cache_unavailable")
        try:
            info = part.lstat()
            _need(not (getattr(info, "st_file_attributes", 0) & 0x400), "cache_unavailable")
        except FileNotFoundError:
            pass
        marker = part / ".git"
        _need(not marker.exists() and not marker.is_symlink(), "cache_unavailable")
    _need(path.resolve() == path, "cache_unavailable")
    if path.exists():
        if directory:
            _need(path.is_dir(), "cache_unavailable")
        else:
            _need(path.is_file() and path.stat().st_nlink == 1, "cache_unavailable")
    return path


def _cache_root(settings):
    if os.name == "nt":
        candidate = os.environ.get("LOCALAPPDATA")
        base = Path(candidate) if candidate and Path(candidate).is_absolute() else Path.home() / "AppData/Local"
    else:
        candidate = os.environ.get("XDG_CACHE_HOME")
        base = Path(candidate) if candidate and Path(candidate).is_absolute() else Path.home() / ".cache"
    identity = hashlib.sha256((str(INSTALL_DIR.resolve()) + "\0" + settings["iteration"]).encode()).hexdigest()[:24]
    root = _guard(base / "MUDweave/updates" / identity, directory=True)
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    return _guard(root, directory=True)


def _read(root, name):
    path = _guard(root / name)
    if not path.exists():
        return {}
    try:
        with path.open("rb") as stream:
            return _json_bytes(stream.read(MAX_JSON + 1))
    except (UpdateError, OSError):
        return {}


def _write(root, name, value):
    _guard(root, directory=True)
    target = _guard(root / name)
    raw = json.dumps(value, separators=(",", ":"), allow_nan=False).encode()
    _need(len(raw) <= MAX_JSON, "cache_unavailable")
    fd, temp = tempfile.mkstemp(prefix=".update-", dir=root)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        _guard(target)
        os.replace(temp, target)
    finally:
        if os.path.exists(temp):
            os.unlink(temp)


@contextmanager
def _lock(root):
    path = _guard(root / "check.lock")
    fd = os.open(path, os.O_CREAT | os.O_RDWR | getattr(os, "O_NOFOLLOW", 0), 0o600)
    acquired = False
    try:
        _need(stat.S_ISREG(os.fstat(fd).st_mode) and os.fstat(fd).st_nlink == 1, "cache_unavailable")
        _guard(path)
        if os.fstat(fd).st_size == 0:
            os.write(fd, b"0")
        os.lseek(fd, 0, os.SEEK_SET)
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            acquired = True
        except OSError:
            pass
        yield acquired
    finally:
        if acquired:
            os.lseek(fd, 0, os.SEEK_SET)
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)


def _origin_url(url):
    _need(isinstance(url, str) and len(url) <= 2048 and not any(ord(c) < 33 for c in url))
    try:
        parsed = urllib.parse.urlsplit(url)
        _need(parsed.scheme == "https" and parsed.netloc == "gitlab.com" and not parsed.fragment)
    except ValueError:
        raise UpdateError("invalid_metadata") from None
    return parsed


def _asset_url(url, name):
    parsed = _origin_url(url)
    try:
        path = urllib.parse.unquote(parsed.path, errors="strict")
    except UnicodeError:
        raise UpdateError("invalid_metadata") from None
    _need(not parsed.query and "%" not in path and ".." not in path and "\\" not in path)
    upload = (rf"(?:/{re.escape(PROJECT_PATH)}/-/uploads|/-/project/{PROJECT_ID}/uploads)"
              rf"/[0-9a-f]{{32}}/{re.escape(name)}")
    generic = (rf"/api/v4/projects/(?:{PROJECT_ID}|{re.escape(PROJECT_PATH)})/packages/generic/"
               rf"(?:mudweave|mudgpt)(?:-(?:codex|claude|muse|collection|clients))?/"
               rf"v?(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)/{re.escape(name)}")
    _need(re.fullmatch(upload, path) is not None or re.fullmatch(generic, path) is not None)
    return url


class _Redirect(urllib.request.HTTPRedirectHandler):
    max_redirections = 3
    max_repeats = 1
    def __init__(self, asset_name=None):
        self.asset_name = asset_name

    def redirect_request(self, request, fp, code, msg, headers, newurl):
        _origin_url(newurl)
        if self.asset_name is not None:
            _asset_url(newurl, self.asset_name)
        return super().redirect_request(request, fp, code, msg, headers, newurl)


def _open(url, asset_name=None):
    _origin_url(url)
    if asset_name:
        _asset_url(url, asset_name)
    # No environment proxies, authorization headers, cookies or credential lookup.
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), _Redirect(asset_name))
    request = urllib.request.Request(url, headers={"Accept": "application/json" if not asset_name else "application/octet-stream",
                                                   "Accept-Encoding": "identity", "User-Agent": "MUDweave-Updater/1"})
    try:
        response = opener.open(request, timeout=TIMEOUT)
        try:
            _origin_url(response.geturl())
            if asset_name:
                _asset_url(response.geturl(), asset_name)
        except UpdateError:
            response.close()
            raise
        return response
    except urllib.error.HTTPError as error:
        code = "rate_limited" if error.code == 429 else "release_unavailable" if error.code == 404 else "offline"
        error.close()
        raise UpdateError(code) from None
    except (urllib.error.URLError, OSError, TimeoutError, http.client.HTTPException):
        raise UpdateError("offline") from None


def _fetch_json(url):
    try:
        with _open(url) as response:
            raw, deadline = bytearray(), time.monotonic() + 20
            read = getattr(response, "read1", response.read)
            while True:
                _need(time.monotonic() < deadline, "offline")
                chunk = read(min(65536, MAX_JSON + 1 - len(raw)))
                if not chunk:
                    break
                raw.extend(chunk)
                _need(len(raw) <= MAX_JSON)
        return _json_bytes(raw)
    except (OSError, TimeoutError, http.client.HTTPException):
        raise UpdateError("offline") from None


def _asset(row):
    _need(isinstance(row, dict))
    iteration, name = row.get("iteration"), row.get("name", row.get("filename"))
    _need(iteration in ITERATIONS and isinstance(name, str) and len(name) <= 180 and ".." not in name)
    _need(re.fullmatch(r"(?:mudweave|mudgpt)-[A-Za-z0-9._-]+\.zip", name) is not None)
    tokens = name.lower().split("-")
    _need(iteration in tokens or iteration == "collection" and "clients" in tokens)
    _version(row.get("version"))
    _need(type(row.get("size_bytes")) is int and 0 < row["size_bytes"] <= MAX_ARCHIVE)
    _need(isinstance(row.get("sha256"), str) and re.fullmatch(r"[0-9a-f]{64}", row["sha256"]) is not None)
    return {"iteration": iteration, "name": name, "version": row["version"], "size_bytes": row["size_bytes"], "sha256": row["sha256"]}


def _release(settings, now):
    release = _fetch_json(f"{ORIGIN}/api/v4/projects/{PROJECT_ID}/releases/permalink/latest")
    _need(isinstance(release, dict))
    tag = release.get("tag_name")
    _need(isinstance(tag, str) and len(tag) <= 40)
    latest = tag[1:] if tag.startswith("v") else tag
    try:
        _version(latest)
    except UpdateError:
        raise UpdateError("ignored_release") from None
    released_at = _timestamp(release.get("released_at"))
    if release.get("upcoming_release") is True or released_at > now + 5:
        raise UpdateError("ignored_release")
    commit = release.get("commit", {}).get("id")
    _need(isinstance(commit, str) and re.fullmatch(r"[0-9a-f]{40}|[0-9a-f]{64}", commit) is not None)
    manifest = _fetch_json(f"{ORIGIN}/api/v4/projects/{PROJECT_ID}/repository/files/downloads.json/raw?ref={commit}")
    _need(manifest.get("collection_version") == latest)
    rows = manifest.get("assets")
    _need(isinstance(rows, list) and 0 < len(rows) <= 16)
    assets = [_asset(row) for row in rows]
    _need(len({row["iteration"] for row in assets}) == len(assets))
    selected = [row for row in assets if row["iteration"] == settings["iteration"]]
    if not selected and settings["iteration"] == "collection":
        complete = manifest.get("complete_collection", {})
        if isinstance(complete, dict) and {"sha256", "size_bytes", "version"}.issubset(complete):
            selected = [_asset(dict(complete, iteration="collection"))]
    _need(bool(selected) or settings["iteration"] == "collection")
    links = release.get("assets", {}).get("links")
    _need(isinstance(links, list) and len(links) <= 64)
    # A legacy portfolio URL in downloads.json is deliberately never used.
    validate = selected or assets
    for row in validate:
        matches = [link for link in links if isinstance(link, dict) and link.get("name") == row["name"]]
        _need(len(matches) == 1)
        row["url"] = _asset_url(matches[0].get("url"), row["name"])
    return {"latest": latest, "release_url": f"{ORIGIN}/{PROJECT_PATH}/-/releases/{tag}",
            "commit": commit, "released_at": _iso(released_at), "asset": selected[0] if selected else None}


def _result(settings, status, now=None, *, cache_hit=False, cache_fresh=False, enabled=True, **extra):
    return {"schema_version": 1, "current": settings["collection_version"], "iteration": settings["iteration"],
            "latest": None, "update_available": False, "status": status, "release_url": None,
            "checked_at": _iso(now) if now is not None else None, "cache_hit": cache_hit,
            "cache_fresh": cache_fresh, "enabled": enabled, **extra}


def _comparison(latest, current):
    newer, installed = _version(latest), _version(current)
    return "update_available" if newer > installed else "ahead" if newer < installed else "up_to_date"


def _cached(settings, state, now):
    saved = state.get("result")
    try:
        _need(isinstance(saved, dict) and saved.get("status") in ("update_available", "up_to_date", "ahead"))
        checked = _timestamp(saved.get("checked_at"))
        _need(0 <= now - checked < DAY and saved.get("iteration") == settings["iteration"])
        latest = saved.get("latest")
        _version(latest)
        _need(_timestamp(saved.get("released_at")) <= checked + 5)
        _need(saved.get("release_url") in (f"{ORIGIN}/{PROJECT_PATH}/-/releases/{latest}", f"{ORIGIN}/{PROJECT_PATH}/-/releases/v{latest}"))
        _need(re.fullmatch(r"[0-9a-f]{40}|[0-9a-f]{64}", saved.get("commit", "")) is not None)
        asset = saved.get("asset")
        if asset is not None:
            sanitized = _asset(asset)
            _need(sanitized["iteration"] == settings["iteration"])
            sanitized["url"] = _asset_url(asset.get("url"), sanitized["name"])
            asset = sanitized
        else:
            _need(settings["iteration"] == "collection")
        comparison = _comparison(latest, settings["collection_version"])
        return _result(settings, comparison, checked,
                       cache_hit=True, cache_fresh=True, latest=latest, update_available=comparison == "update_available",
                       release_url=saved["release_url"], released_at=saved["released_at"], commit=saved["commit"], asset=asset)
    except (UpdateError, TypeError, KeyError):
        return None


def _enabled(settings, root):
    preference = _read(root, "preferences.json").get("enabled", settings["enabled"])
    _need(type(preference) is bool, "cache_unavailable")
    return preference


def _backoff(state, now):
    attempted, retry = state.get("last_attempt_at"), state.get("retry_at")
    return (type(attempted) in (int, float) and type(retry) in (int, float) and
            math.isfinite(attempted) and math.isfinite(retry) and 0 <= now - attempted < DAY and now < retry <= attempted + DAY)


def check(settings=None, force=False):
    """Check metadata only; failures are bounded, sanitized JSON results."""
    try:
        settings = load_settings(settings)
    except UpdateError:
        return {"schema_version": 1, "current": None, "latest": None, "update_available": False,
                "status": "configuration_error", "release_url": None, "checked_at": None,
                "cache_hit": False, "cache_fresh": False}
    try:
        root = _cache_root(settings)
        with _lock(root) as acquired:
            if not acquired:
                return _result(settings, "cache_busy")
            enabled = _enabled(settings, root)
            if not enabled and not force:
                return _result(settings, "disabled", enabled=False)
            state = _read(root, "state.json")
            now = time.time()
            cached = _cached(settings, state, now)
            if not force and cached:
                cached["enabled"] = enabled
                return cached
            if not force and _backoff(state, now):
                return _result(settings, "backoff", cache_hit=True, enabled=enabled)
            try:
                found = _release(settings, now)
                comparison = _comparison(found["latest"], settings["collection_version"])
                result = _result(settings, comparison, now,
                                 cache_fresh=True, enabled=enabled, update_available=comparison == "update_available", **found)
                failures = 0
                retry = now + DAY
            except (UpdateError, OSError, ValueError, TypeError, KeyError, AttributeError, RecursionError) as error:
                code = error.code if isinstance(error, UpdateError) else "offline" if isinstance(error, OSError) else "invalid_metadata"
                result = _result(settings, code, now, enabled=enabled)
                previous = state.get("failures", 0)
                failures = min(previous + 1, 8) if type(previous) is int and 0 <= previous <= 8 else 1
                retry = now + min(300 * 2 ** (failures - 1), 21600)
            _write(root, "state.json", {"result": result, "failures": failures, "last_attempt_at": now,
                                         "retry_at": retry, "notified_version": state.get("notified_version")})
            return result
    except (UpdateError, OSError, ValueError, TypeError):
        return _result(settings, "cache_unavailable")


def status(settings=None):
    """Inspect local metadata only; never turns stale evidence into up-to-date."""
    settings = load_settings(settings)
    try:
        root = _cache_root(settings)
        enabled = _enabled(settings, root)
        if not enabled:
            return _result(settings, "disabled", enabled=False)
        state = _read(root, "state.json")
        cached = _cached(settings, state, time.time())
        return cached or _result(settings, "stale" if state else "not_checked", cache_hit=bool(state))
    except (UpdateError, OSError, ValueError, TypeError):
        return _result(settings, "cache_unavailable")


def configure(settings=None, enabled=True):
    settings = load_settings(settings)
    _need(type(enabled) is bool, "configuration_error")
    try:
        root = _cache_root(settings)
        with _lock(root) as acquired:
            if not acquired:
                return _result(settings, "cache_busy")
            _write(root, "preferences.json", {"schema_version": 1, "enabled": enabled})
        return _result(settings, "configured", enabled=enabled)
    except (UpdateError, OSError, ValueError, TypeError):
        return _result(settings, "cache_unavailable")


def _notify_once(settings, result, notify):
    if not notify or not result.get("update_available") or not result.get("cache_fresh"):
        return
    root = _cache_root(settings)
    with _lock(root) as acquired:
        if not acquired:
            return
        state = _read(root, "state.json")
        cached = _cached(settings, state, time.time())
        if not _enabled(settings, root) or not cached or not cached["update_available"] or cached["latest"] != result["latest"]:
            return
        if state.get("notified_version") == result["latest"]:
            return
        state["notified_version"] = result["latest"]
        _write(root, "state.json", state)
    notify(result)


def _default_notify(result):
    print(f"MUDweave {result['latest']} is available (installed collection {result['current']}). "
          f"{result['release_url']} — download only when requested.", file=sys.stderr)


def start_check(settings=None, notify=None):
    """Return immediately; a daemon checks at most daily and never downloads."""
    key = str(INSTALL_DIR) + repr(settings)
    def worker():
        try:
            resolved = load_settings(settings)
            _notify_once(resolved, check(resolved), _default_notify if notify is None else notify)
        except Exception:
            # Optional notifications must never disrupt a runtime or its stdout.
            pass
        finally:
            with _THREAD_LOCK:
                _THREADS.pop(key, None)
    with _THREAD_LOCK:
        if key in _THREADS:
            return _THREADS[key]
        thread = threading.Thread(target=worker, name="mudweave-update-check", daemon=True)
        _THREADS[key] = thread
        thread.start()
    return thread


def _matches(path, asset):
    _guard(path)
    if path.stat().st_size != asset["size_bytes"]:
        return False
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest() == asset["sha256"]


def download(settings=None):
    """Explicit request only: download a verified archive without unpacking it."""
    settings = load_settings(settings)
    result = dict(check(settings, force=True), cleanup_pending=False)
    if result["status"] not in ("update_available", "up_to_date"):
        return result
    asset = result.get("asset")
    if asset is None:
        return dict(result, status="download_unsupported", update_available=result["update_available"],
                    detail="Select --iteration codex, claude, or muse; the collection has no verified archive metadata.")
    temporary = None
    outcome = None
    try:
        root = _cache_root(settings)
        with _lock(root) as acquired:
            if not acquired:
                return dict(result, status="cache_busy")
            folder = _guard(root / "downloads", directory=True)
            folder.mkdir(exist_ok=True, mode=0o700)
            destination = _guard(folder / (asset["sha256"] + "-" + asset["name"]))
            if destination.exists():
                _need(_matches(destination, asset), "download_conflict")
                return dict(result, status="downloaded", download_path=str(destination), download_cached=True)
            fd, temporary = tempfile.mkstemp(prefix=".archive-", suffix=".part", dir=folder)
            digest, count, deadline = hashlib.sha256(), 0, time.monotonic() + 120
            with os.fdopen(fd, "wb") as stream, _open(asset["url"], asset["name"]) as response:
                read = getattr(response, "read1", response.read)
                while True:
                    _need(time.monotonic() < deadline, "download_timeout")
                    chunk = read(min(65536, asset["size_bytes"] - count + 1))
                    if not chunk:
                        break
                    count += len(chunk)
                    _need(count <= asset["size_bytes"], "download_integrity_error")
                    digest.update(chunk)
                    stream.write(chunk)
                _need(count == asset["size_bytes"] and digest.hexdigest() == asset["sha256"], "download_integrity_error")
                stream.flush()
                os.fsync(stream.fileno())
            _guard(destination)
            try:
                # Exclusive publication: never overwrite an existing different file.
                os.link(temporary, destination)
            except FileExistsError:
                _need(_matches(destination, asset), "download_conflict")
            try:
                os.unlink(temporary)
            except FileNotFoundError:
                pass
            temporary = None
            _need(_matches(destination, asset), "download_integrity_error")
            outcome = dict(result, status="downloaded", download_path=str(destination), download_cached=False)
            return outcome
    except (UpdateError, OSError, ValueError, TypeError, http.client.HTTPException) as error:
        code = error.code if isinstance(error, UpdateError) else "offline" if isinstance(error, http.client.HTTPException) else "download_failed"
        outcome = dict(result, status=code)
        return outcome
    finally:
        if temporary:
            try:
                os.unlink(temporary)
            except FileNotFoundError:
                pass
            except OSError:
                # Preserve the original sanitized result. Do not claim that a
                # denied cleanup succeeded or disclose its path/error text.
                if outcome is not None:
                    outcome["cleanup_pending"] = True


def main(settings=None, argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--iteration", choices=ITERATIONS)
    sub = parser.add_subparsers(dest="command")
    for name in ("check", "download", "status", "config"):
        child = sub.add_parser(name)
        child.add_argument("--json", action="store_true", default=argparse.SUPPRESS)
        child.add_argument("--iteration", choices=ITERATIONS, default=argparse.SUPPRESS)
        if name == "check":
            child.add_argument("--force", action="store_true", help="Check now, including a one-time check when automatic checks are disabled.")
        if name == "config":
            child.add_argument("setting", choices=("enabled",))
            child.add_argument("value", choices=("on", "off"))
    args = parser.parse_args(argv)
    try:
        resolved = load_settings(settings)
        if args.iteration:
            resolved["iteration"] = args.iteration
        if args.command == "check":
            result = check(resolved, force=args.force)
        elif args.command == "download":
            result = download(resolved)
        elif args.command == "config":
            result = configure(resolved, args.value == "on")
        else:
            result = status(resolved)
    except UpdateError:
        result = {"schema_version": 1, "status": "configuration_error", "update_available": False}
    if args.json:
        print(json.dumps(result, sort_keys=True))
    else:
        print("MUDweave updates: " + result["status"])
        for key in ("current", "latest", "release_url", "download_path", "detail"):
            if result.get(key):
                print(f"{key}: {result[key]}")
        if result.get("cleanup_pending"):
            print("cleanup_pending: true (a partial download could not be removed)")
    if result["status"] == "configuration_error":
        return 2
    return int(args.command == "download" and result["status"] != "downloaded")


if __name__ == "__main__":
    raise SystemExit(main())
