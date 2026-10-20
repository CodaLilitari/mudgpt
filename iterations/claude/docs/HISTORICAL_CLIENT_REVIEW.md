> **Historical review, read with the release qualifications.** The following is Red's original 0.1.0 review. Its “current working copy” statement is dated to the inspected snapshot and does not describe the later Codex 0.2.0 iteration. Suggested fixes are proposals. See [editor notes](../EDITOR_NOTES.md) and the byte-identical [original](../claude/docs/MUDGPT_0.1.0_REVIEW.md).

---

# MudGPT 0.1.0: review notes from Red's first use

Red (Claude), 2026-10-18 to 20. I read the whole source of the GitLab 0.1.0 release and ran
its suite in a Linux sandbox (43/43 tests pass). I then drove a character through it for a
day: first against a LoK-shaped fixture, then on the local Legends of Krynn server, through
the courier in `claude/courier/`.

The five issues below are present in both the release and Andrei's current working copy
(checked 2026-10-20). Line numbers are from `src/mudgpt/session.py` and
`scripts/game_command.py` as they stand now.

## What works well

- **One broker owns the socket.** Everyone else reads sequence-numbered events. This made a
  second, very different client (a file-mailbox courier) easy to add without touching the
  core.
- **The receipts are honest.** `delivery: transport_accepted` with `command_complete: false`
  never pretends the game acted. Prompt, quiet and timeout are documented as observation
  boundaries.
- **Leases and human takeover work as designed.** A named controller needs its lease; the
  human CLI can always preempt.
- **Secrets travel cleanly.** They go through `send --secret --stdin`, with echo redaction
  that survives chunk boundaries. Nothing is written to disk by the broker.
- **Style spans keep colour attached to words.** ROM's dark grey (`fg=black,bold`) reads
  correctly once you know it.

## Issues

### 1. `game_command.py` can crash after the command was sent

**Where:** `scripts/game_command.py:13-21` and `:35`.

The starting cursor comes from the last line of a persistent `observations.jsonl`. The
script sends first and observes afterwards. `Session.observe` rejects a cursor above the
current one ("Cursor must be an integer within this session's history", `session.py:255`).

**Scenario:**

1. The broker restarts. A new Session counts from 0.
2. The saved cursor is now ahead of the broker.
3. The `send` goes out.
4. The `observe` raises, and the script exits with a traceback. It writes no entry and prints
   none of the game's reply.

A human, or an agent, then reasonably resends: the replay MudGPT works hard to prevent. The
same crash happens if the last saved receipt has `cursor: null`.

**Suggested fix:** take `after` from `status` (or check the saved cursor against
`status.cursor` and `generation`) *before* sending. Wrap the post-send observe so that a
failure still prints and saves the send result.

### 2. Receipts carry no broker identity, so stale cursors can fail silently

A restarted broker starts again at cursor 0 and generation 0. A client holding cursor 15
from the old broker gets one of two outcomes:

- an error (issue 1), if the new broker hasn't reached 15;
- if it has, a silent gap: `observe(15)` skips the new broker's first 15 events and reports
  `truncated: false`.

**Suggested fix:** add a random `broker_id`, made per broker start, to `status`, `observe`
and `send` results. Clients reset their cursor whenever it changes. (Red's courier hit the
same problem and keeps its last cursor per broker lifetime for this reason.)

### 3. `--pages` saves the wrong receipt

**Where:** `scripts/game_command.py:26-38`.

With `--pages`, each `[Hit Return to continue]` gets an empty send, and `result` is
overwritten each time. The saved entry pairs the *original* command with the receipt of the
*last pager Return*: its `command_id`, `reason` and `delivery`.

**Suggested fix:** keep the first receipt as `receipt` and add `pager_receipts: [...]`.

### 4. Protocol redaction removes any value that occurs inside a secret

**Where:** `session.py:129`, inside `_protocol.clean`.

A GMCP or MSDP string is replaced with `[REDACTED]` whenever it is a *substring* of any
secret. That removes values like `"1"`, `"a"` or `"on"` whenever the password contains them.
It also leaks information: whoever reads the observations, usually the AI collaborator, can
learn which short strings occur in the password by watching which values disappear.

**Suggested fix:**

- Keep `_redact` for values that *contain* a secret.
- For partial matches, redact only long fragments, for example 4 characters or more that are
  also a prefix or suffix of the secret.
- Or redact protocol payloads only within a short window after a secret send.

### 5. The `connect` result says `connecting: true`

**Where:** `session.py:193-195`.

`return self.status()` is built inside the `try`, before `finally` clears `_connecting`. The
connect result therefore reports `connected: true` and `connecting: true` together. A client
that waits for `connecting` to clear can stall or misread the state.

**Suggested fix:** set `self._connecting = False` before building the return value. Or call
`status()` after the `try`/`finally`.

## Design notes from running it unattended

**Cowork's device shell kills its processes when each call ends,** so an AI session there
can't keep a broker or MCP stdio process alive. The courier in `claude/courier/` solves this
with a file mailbox and a persistent process on the user's machine. Requests are dequeued and
ledgered *before* acting. Each one carries a decision cursor (stale decisions are refused)
and an expiry (no late execution). The password is referenced, never carried. A `broker_id`
(issue 2) would let it drop its per-lifetime cursor bookkeeping.

**File locks across the Windows `/mnt/c` bridge in WSL are unreliable.** The WSL launcher
keeps the control and lock file on the Linux disk (`KRASNA_CONTROL`). The README could
mention this for WSL users.

**Python 3.10** is still common in VMs, and the README's 3.11+ requirement matters there. On
3.10, `asyncio.wait_for` raises `asyncio.TimeoutError`, which the `except TimeoutError` in
`Session.send` doesn't catch (the two were unified in 3.11). A send would fail on its first
quiet wait. The courier checks the version at startup. `mudgpt` could too, so the failure
comes with a clear message.
