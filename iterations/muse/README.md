# MudGPT — Muse iteration 0.2.0

This is a self-contained Muse client iteration: its own pinned MudGPT broker
snapshot, scoped host adapter, receiving helper, setup tools, documentation and
tests. It is separate from the Codex and Claude iterations. Run this copy in its
own environment and use its own broker process, control file, adapter credential,
receiving configuration and durable command journal.

Start with [Muse setup and play instructions](muse/README.md). Machines can read
[setup.workflow.json](setup.workflow.json), which identifies the full workflow,
schemas, commands, core pin and validation receipts. The [adapter contract](muse/ADAPTER_CONTRACT.md)
defines the five allowed remote operations. [LoK play and collaboration lessons](docs/lok-and-collaboration.md)
cover room awareness, color, roleplay and observation-based decisions.

The core source is an independent copy of the validated **Codex 0.2.0** snapshot.
[provenance/inherited-core.json](provenance/inherited-core.json) pins each inherited
file by SHA-256 and records the small documentation path adjustments. The copied
broker keeps the `mudgpt` Python import and CLI name; this does not make its runtime
or credentials interchangeable with another iteration. Its preserved
[core reference](docs/core-readme.md) explains local CLI/MCP mechanics.

## Install and run from this directory

Use Python 3.11+ and create a fresh environment here:

```text
python -m venv .venv
```

On Windows PowerShell:

```powershell
.venv\Scripts\python.exe -m pip install -e .
.venv\Scripts\python.exe -m pip install -r muse/requirements.txt
.venv\Scripts\python.exe -m unittest discover -s muse/tests -v
```

On Linux:

```sh
.venv/bin/python -m pip install -e .
.venv/bin/python -m pip install -r muse/requirements.txt
.venv/bin/python -m unittest discover -s muse/tests -v
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
helper remains a publication candidate distinct from the fixed helper already
used in live play; publishing these files does not replace that running helper.

## Evidence and scope

See [validation.json](validation.json) for the exact final test environment and
results. The original 32-test staging receipt is preserved in
[provenance/muse-original-validation.json](provenance/muse-original-validation.json).
Core validation inherited from the separately tested snapshot is preserved in
[provenance/codex-validation.json](provenance/codex-validation.json); those results
are attributed to that snapshot rather than relabelled as new Muse tests.

The full iteration allowlist and hashes are in [source-manifest.json](source-manifest.json).
The nested [Muse package manifest](muse/package-manifest.json) identifies its
separate adapter/helper files. Runtime data, keys, envelopes, credentials,
journals, roleplay logs, pfiles and game server source are excluded. The server
project is maintained and published separately under its own access policy.

## Collection 0.3.0 update checks

The separate update-aware launcher checks GitLab and notifies on ordinary long-running starts. Downloads require an explicit request; installation is a deliberate side-by-side operation. Existing runtime code, original launchers and historical validation remain unchanged. See [UPDATES.md](UPDATES.md), [updates.workflow.json](updates.workflow.json) and [UPDATE_VALIDATION.json](UPDATE_VALIDATION.json) for the setup, machine-readable policy and separate updater evidence.

For the complete 0.3.0 package inventory, use [distribution-manifest.json](distribution-manifest.json); earlier source manifests and runtime validation retain their historical scope. Updater tests are supplied at the collection root, and the copied updater receipt refers to those collection-relative test paths.
