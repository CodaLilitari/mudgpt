> **0.4.0 collection update:** See [current component versions](runtime-manifest.json), [release validation](VALIDATION_040.json) and [update notes](UPDATES_040.md). The maintained core is 0.3.0. Historical validation below remains dated evidence; it is not a report of this release.

# MUDweave — Muse iteration 0.3.0

This is a self-contained Muse client iteration: its own pinned MudGPT broker
core 0.3.0, scoped host adapter, receiving helper, setup tools, documentation and
tests. It is separate from the Codex, Claude and Lily iterations. Run this copy in its
own environment and use its own broker process, control file, adapter credential,
receiving configuration and durable command journal.

Start with [Muse setup and play instructions](muse/README.md). Machines can read
[setup.workflow.json](setup.workflow.json), which identifies the full workflow,
schemas, commands, core pin and validation receipts. The [adapter contract](muse/ADAPTER_CONTRACT.md)
defines the six allowed remote operations. [LoK play and collaboration lessons](docs/lok-and-collaboration.md)
cover room awareness, color, roleplay and observation-based decisions.

The current core source is an independent copy of the maintained **0.3.0** core.
[provenance/inherited-core.json](provenance/inherited-core.json) records the
historical 0.2.0 inheritance; those hashes are not a manifest for the changed
0.3.0 source. Consult [the current component manifest](runtime-manifest.json),
[distribution inventory](distribution-manifest.json) and current release
validation for this package's versions, bytes and tested scope. The copied
broker keeps the `mudgpt` Python import and CLI name; this does not make its runtime
or credentials interchangeable with another iteration. Its preserved
[core reference](docs/core-readme.md) explains local CLI/MCP mechanics.

## Install and run from this directory

Use Python 3.11+ and create a dedicated environment outside every Git checkout.
Run these examples from this iteration directory, then use the selected
environment's interpreter for all subsequent operations. Store runtime state,
credentials, journals and test output separately, also outside Git.

On Windows PowerShell:

```powershell
$museEnv = Join-Path $env:LOCALAPPDATA 'MUDweave\muse-env'
py -3 -m venv $museEnv
& "$museEnv\Scripts\python.exe" -m pip install .
& "$museEnv\Scripts\python.exe" -m pip install -r muse/requirements.txt
```

On Linux:

```sh
python3 -m venv "$HOME/.local/share/mudweave/muse-env"
"$HOME/.local/share/mudweave/muse-env/bin/python" -m pip install .
"$HOME/.local/share/mudweave/muse-env/bin/python" -m pip install -r muse/requirements.txt
```

The broker directly pins `telnetlib3==4.0.3` and `mcp==2.3.0`; the receiving
helper adds `cryptography==50.0.2`. The inherited `uv.lock` reproduces the core's
transitive dependency set; it does not include the separately pinned helper
dependency. The standard pip instructions above pin direct dependencies and let
pip resolve their transitive dependencies. All subsequent commands in the Muse
guide run from this iteration directory with its selected interpreter.

The host logs in to the intended character locally, then releases control for
Muse's claim. The recipient gets an encrypted **scoped adapter credential**, not
the broker token or game password. Keep the receiving private key in private
storage for continuing authorized play. Network enrollment and policy must be
reviewed against the intended recipient and host; documentation examples contain
no working addresses, credentials or keys.

The package does not install a persistent service, wake Muse, or bypass runtime
permission cards. An awake conversation, task or job is needed. The configurable
helper remains distinct from the fixed helper used in historical live play;
publishing these files does not replace a running helper or demonstrate a live
deployment of this version.

## Evidence and scope

See [VALIDATION_040.json](VALIDATION_040.json) for this release's executed test
scope. [validation.json](validation.json) is historical evidence for the prior
Muse package, not the changed 0.3.0 code. The original 32-test staging receipt is preserved in
[provenance/muse-original-validation.json](provenance/muse-original-validation.json).
Core validation inherited from the separately tested snapshot is preserved in
[provenance/codex-validation.json](provenance/codex-validation.json); those results
are attributed to that snapshot rather than relabelled as new Muse tests.

The current distribution inventory is [distribution-manifest.json](distribution-manifest.json).
Earlier [source-manifest.json](source-manifest.json) and nested
[Muse package manifest](muse/package-manifest.json) records retain their original
versioned scope. Runtime data, keys, envelopes, credentials,
journals, roleplay logs, pfiles and game server source are excluded. The server
project is maintained and published separately under its own access policy.

## Collection 0.4.0 update checks

The separate update-aware launcher checks GitLab and notifies on ordinary long-running starts. Downloads require an explicit request; installation is a deliberate side-by-side operation. This package does not hotpatch or restart an existing runtime. Updater 0.4.0 reports declared component versions; it does not identify the active process. See [UPDATES_040.md](UPDATES_040.md) and [updates.workflow.json](updates.workflow.json) for the current contract. [UPDATE_VALIDATION.json](UPDATE_VALIDATION.json) retains the earlier updater's evidence.

Updater tests are supplied at the collection root. Current validation and component manifests distinguish this release from preserved historical results; publishing an archive does not replace a deployed helper or certify a new live session.
