# MudGPT, Claude iteration: the mailbox courier

Version 0.1.0, pinned to the MudGPT 0.1.0 core. By Red (Claude, in Cowork), 2026-10-20.

This is the third of MudGPT's three client paths, kept separate from the Codex and Muse ones:
it lets a Claude session that can only reach a computer through short-lived commands play a
MUD character one decision at a time. A long-running courier on the player's machine owns the
game connection, through MudGPT's broker. Claude talks to it through files in a mailbox
folder.

## What it guarantees

- **No stale moves.** Every command can carry the decision cursor it was chosen from. If the
  game moved since then, the courier sends nothing and shows what changed.
- **No replays.** Each request runs once. A repeated request ID is refused, and the ledger
  is written before acting.
- **The human comes first.** A takeover lock lets the person at the keyboard take control at
  any moment. Claude waits until it is explicitly released.
- **Passwords stay out of sight.** The password goes from a local credentials file straight
  to the game. It is never printed, logged or passed through the mailbox.
- **Fresh profile per connect.** Switching servers needs no restart.

## Contents

| Path | What it is |
|---|---|
| `courier/mailbox_courier.py` | The courier |
| `courier/krasna.py` | The command-line side Claude uses (lock, status, connect, observe, claim, send, secret, note, unlock) |
| `courier/*.cmd`, `courier/*.sh` | Windows and WSL launchers: start, take over, release |
| `courier/tests/` | `test_courier_e2e.py` (32 checks) and `fake_lok.py`, a synthetic MUD written from scratch |
| `docs/CLAUDE_PLAY_SETUP.md` | How a session drives a character: rules, ritual, safety |
| `docs/MUDGPT_0.1.0_REVIEW.md` | Five issues found in the MudGPT 0.1.0 core while running it unattended |

## Running the tests

On Linux or WSL, from a MudGPT 0.1.0 checkout with its virtual environment:

```bash
MUDGPT_REPO=. MUDGPT_PYTHON=.venv/bin/python python3 claude/courier/tests/test_courier_e2e.py
```

The tests use throwaway folders and a loopback fixture only. Last run: 32/32, 2026-10-20.

## Not included

No credentials, keys, player files, play logs, game source or game data. Notes and findings
about a specific game's code stay private, for that game's staff.
