# Lily iteration update checks

Use [the 0.4.0 update and version guide](UPDATES_040.md) and
[the machine-readable update policy](updates.workflow.json).

From this package directory, run `python mudweave.py versions --json` with the
Lily environment's Python to inspect declared package components. This reads
public metadata and does not establish the version of an active process.

`python mudweave.py update check --force --json` performs an explicit metadata
check. Automatic update-aware starts check and notify only. Downloads and
side-by-side installation require an explicit request; there is no automatic
installation, restart, controller handoff or character-state migration.

Use the actor module directly for individual turns. The updater is separate from
command decisions and does not wake a model or continue a game session. Keep
all private runtime paths and environments outside Git. Retain prior installations
and stable private journals until a deliberate maintenance handoff is complete.
