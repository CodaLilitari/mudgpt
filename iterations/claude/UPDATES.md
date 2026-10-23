# GitLab update checks

MUDweave checks the public `codalilitari/mudgpt` GitLab release when started
through the update-aware launcher. Automatic checks notify only. They do not
download archives, install packages, switch environments, restart a process,
connect to a game or change controller ownership.

Each distribution carries its own launcher, checker and `mudweave-update.json`
identity. The collection version is separate from the client runtime version:
collection 0.3.0 retains Codex/Muse core 0.2.0 and Claude core 0.1.0. The new
launcher is a Coda-authored addition; Red's original files remain unchanged.

## Enable checks when launching

Use Python 3.11 or newer from the selected iteration's own environment. Run from
that iteration's directory. On Windows, replace `python` with the full path to
that environment's `Scripts/python.exe`; on Linux, use its `bin/python`.

For the Codex or Muse broker:

```sh
python mudweave.py broker --control /absolute/private/control.json serve --profile /absolute/private/profile.json
```

For the Codex MCP entry point:

```sh
python mudweave.py broker --control /absolute/private/control.json mcp
```

For the Muse host adapter, retain your existing private arguments:

```sh
python mudweave.py run -- -m muse.adapter serve --control /absolute/private/control.json --credential-file /absolute/private/adapter.json --journal /absolute/private/commands.sqlite --listen-ip HOST_TAILSCALE_IP --peer-ip RECIPIENT_TAILSCALE_IP
```

For Red's Claude courier, first prepare the private runtime home described in
the Claude README. Copy only the three new files `mudweave.py`,
`mudweave_update.py` and `mudweave-update.json` from the Claude distribution
beside the existing private `mailbox_courier.py`. Keep the existing interpreter
and `KRASNA_CONTROL` setting. The wrapper does not perform Red's first-run
environment setup; use an already configured private home.

From that private home in WSL, the original launcher uses these exact defaults:

```bash
KRASNA_CONTROL="$HOME/.krasna/control.json" "$HOME/.krasna/venv/bin/python" mudweave.py run -- mailbox_courier.py
```

For the native Windows layout, from the private home in PowerShell:

```powershell
& '.\.venv\Scripts\python.exe' '.\mudweave.py' run -- '.\mailbox_courier.py'
```

Native Windows retains an existing `KRASNA_CONTROL` environment override; without
one, the courier uses `control.json` beside itself. If the original Windows
launcher used its fallback interpreter, use that same interpreter's absolute path
instead. If you customized either platform's private control path, preserve that
exact override. Do not switch between WSL and native Windows state directories
when adopting the wrapper.

These commands pass the existing arguments, current directory, environment and
standard streams to a child using the same Python interpreter. Use the correct
environment and existing private configuration for that iteration. A wrapper
does not make a differently configured client interchangeable with it.

The launcher is intended for a foreground command or an MCP stdio host. If a
service manager controls it, configure that manager to stop the complete process
tree. Killing only the wrapper process can leave its client child running; the
launcher does not add a service supervisor or replace the client's shutdown path.

Existing `python -m mudgpt`, Muse commands and Red-authored launchers still work
as before and do not run the checker. Adopt the new launcher on the next normal
start; there is no need to interrupt a game to enable it. Short `broker send`,
`observe`, `status`, and other command operations do not start automatic checks.

## Behavior and privacy

The network check runs in a daemon thread after the client starts. It is
throttled to once per 24 hours using an external user cache and a separate
nonblocking updater lock. A short-lived process can exit before its background
check completes. A long-running client checks at launch, not continuously; an
explicit check can refresh while it remains running.

Notices go only to stderr, leaving JSON and MCP stdout unchanged. Some MCP hosts
show stderr only in their logs; use the explicit JSON check when a machine needs
the result. Offline operation, malformed metadata or a cache error does not stop
the client. A failed check is not evidence that the installed version is current.

Only public release metadata is requested. No game configuration, broker token,
password, key, player record, roleplay log, machine inventory or Git credentials
are collected. The updater neither runs Git nor watches directories. Cache and
download storage live outside repositories. Each iteration/install has separate
state, and the checker rejects cache destinations inside a Git worktree or
through a symbolic link/junction.

GitLab's latest endpoint orders by release date, so the checker compares the
selected stable numeric collection version locally and never recommends a
downgrade. It does not claim to search every historical tag for the greatest
semantic version. Upcoming and unsupported prerelease forms are not installed.

## Explicit downloads and installation

Check now, including when automatic checks are disabled, and inspect local state:

```sh
python mudweave.py update check --force --json
python mudweave.py update status --json
```

Request a verified download of your chosen iteration:

```sh
python mudweave.py update download --iteration codex --json
```

Replace `codex` with `claude` or `muse` as appropriate. Inside an individual
distribution, omitting `--iteration` selects that distribution. The collection
entry point requires a selected Codex, Claude or Muse iteration for a verified
standalone download. Inspect the returned JSON `status`; a failed check does not
mean that no update exists. Use `python mudweave.py update --help` for help.

The checker pins `downloads.json` to the release's commit, selects the matching
iteration and release-asset name, and verifies exact byte count and SHA-256
before publishing a downloaded ZIP in the external cache. HTTPS destinations
are limited to this project's GitLab assets. This is integrity verification
against GitLab metadata, not independent publisher signing. Downloads are never
automatically extracted or executed.

Install a downloaded release deliberately into a new directory and a fresh
environment for the same iteration. Follow its README, verify its configuration
and dependencies, and switch launch commands during a planned stop. Keep existing
credentials, keys, mailboxes, control files and journals in their private homes;
do not overwrite them with an archive or replay a pending request. The previous
installation remains available for rollback. The updater does not perform an
in-place installation or automatic migration.

For a one-launch opt-out, place `--no-update-check` before `broker` or `run`.
Set the persistent preference for this installation with:

```sh
python mudweave.py update config enabled off --json
python mudweave.py update config enabled on --json
```

No operating-system scheduler, background service, login item or paid API is
created by this feature.

See `updates.workflow.json` for the machine-readable contract and
`UPDATE_VALIDATION.json` for the separately recorded updater tests.
