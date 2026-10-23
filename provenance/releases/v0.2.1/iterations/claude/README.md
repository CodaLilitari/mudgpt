# MudGPT — Claude mailbox iteration 0.1.0

Red's independent file-mailbox courier is packaged with its pinned MudGPT 0.1.0 core. It is separate from the Codex 0.2.0 relay and Muse adapter. Its control files, mailbox, ledger, local credentials and character session must remain separate too.

**Start with [the editorial boundaries](EDITOR_NOTES.md).** They qualify absolute wording in Red's original README: the optional cursor check is preflight rather than atomic, retained IDs suppress repeated requests without proving power-loss exactly-once execution, and a transport receipt never proves game completion. The historical review predates the later Codex fixes.

- [Red's original public README](claude/PUBLIC_README.md)
- [Red's session setup](claude/docs/CLAUDE_PLAY_SETUP.md)
- [Historical client review with current editorial context](docs/HISTORICAL_CLIENT_REVIEW.md)
- [Author-supplied file hashes](claude/PUBLICATION_MANIFEST.sha256)
- [Pinned core provenance](provenance/core.json)
- [Exact-export validation](validation.json)

## Install and test this iteration

Use Python 3.11 or newer. From this iteration directory, create and activate your own virtual environment, then install with `python -m pip install -e .`. Direct dependencies are `telnetlib3==4.0.3` and `mcp==2.3.0`; the preserved lock supports `uv sync --frozen`. The installed package is this pinned 0.1.0 core, not another iteration's editable install.

On Linux or WSL, after installation:

```bash
MUDGPT_REPO="$PWD" MUDGPT_PYTHON="$PWD/.venv/bin/python" python3 claude/courier/tests/test_courier_e2e.py
```

The 32-check runner creates throwaway homes and a synthetic loopback MUD. It copies this iteration's `src/` and courier into those homes; it does not contact a real game. The restart scenario needs Linux/WSL `pgrep`. Windows launcher files are included, but this release's executed courier check is WSL, not a Windows execution claim. See `validation.json` for the result and exact hashes.

## Prepare a private runtime home

Choose a new private directory for this character. Copy only `claude/courier/` launcher and Python files into it, and copy this iteration's `src/` beside them as `src/`. Copy `examples/profile.json` as `profile.json` and supply your own authorized game endpoint. Create `credentials.txt` locally with a `password=` entry using an editor; no real password belongs in shell arguments, chat, a public example or this repository.

The launchers expect that private-home layout; running directly from the published `claude/courier/` directory without its sibling `src/`, local profile and credentials is not a configured game session. Follow Red's setup for mailbox operations, current-owner checks, saving and session notes. WSL launchers keep broker control state on the Linux filesystem. Never point this courier at another iteration's control file or existing character session without an explicit handoff.

The code retains its tested `krasna` launcher names and `red` actor label. Those are defaults for the original iteration, not credentials or authentication of the person operating it. Human takeover remains a local control operation.

## Provenance and limits

The original 14 Red files and her manifest are preserved exactly under `claude/`. The seven core modules and dependency metadata are exact public commit `060e50c85b0c65f6dc088d5941d0863961dad254`. The entry README, editorial wrapper, provenance and validation record were prepared by Coda. No active runtime, Red original or other client was edited during packaging.

No server source, engine findings, pfiles, roleplay logs, private mailbox files, credentials or keys are included. Current server mechanics and successful gameplay are separate evidence from the synthetic courier suite.
