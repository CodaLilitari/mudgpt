# MUDweave collection changes

*Formerly MudGPT · Multi-client collaboration collection*

## 0.3.0 / updates1 — 23 October 2026

- Add a separate update-aware launcher and standard-library GitLab checker to
  each distribution. Ordinary long-running starts check and notify automatically;
  short broker operations and the original launchers remain unchanged.
- Require an explicit request to download an archive. Verify its selected
  iteration, release-specific public GitLab URL, byte count and SHA-256 before
  retaining it outside repositories. No automatic extraction or execution occurs.
- Keep installation deliberate and side by side. Do not restart clients, change
  controller ownership, move private state, connect to a game or replay commands.
- Keep updater cache and packaging output outside Git checkouts. Add no Git
  monitor, scheduled task, background service or paid API requirement.
- Preserve all existing runtime code and historical tests/validation: Codex and
  Muse remain core 0.2.0; Claude remains core 0.1.0. Red's nested courier tree and
  Muse's nested adapter/helper tree remain byte-identical to the baseline.
- Add human and machine update instructions, a separate updater validation
  receipt, exact distribution manifests and standalone `updates1` packages.
- Preserve exact 0.2.1 metadata from baseline
  `c9764b51b409d5470c9bd96363b74dd572c901d3` before regenerating release metadata.

Updater validation passed on Windows and Linux; UPDATE_VALIDATION.json records its exact scope and hashes.
Historical runtime suite results do not certify the new updater.

## 0.2.1 / license1 — 20 October 2026

- Add Apache 2.0 licensing, contributor recognition, LICENSE/NOTICE documents and
  distribution metadata without changing the three client runtimes.
- Preserve earlier collection metadata and exact per-iteration validation.
  The retained 0.2.1 snapshot records this metadata-only release; no new runtime
  test execution is inferred from packaging.

## 0.2.0 — 20 October 2026

- Adopt MUDweave as the collection's display name while retaining the `mudgpt`
  repository slug, Python package names, commands, iteration versions and ZIP
  filenames for compatibility.
- Replace the top-level runtime layout with three self-contained client iterations.
- Publish Codex 0.2.0 with guarded decisions, broker lifetime identity, durable
  relay admission and color transport.
- Expand the Legends of Krynn and Codex-to-ChatGPT collaboration guidance first
  published with 0.1.0, including observation-led play and attributed decisions.
- Publish Red's independent Claude mailbox iteration with its own pinned 0.1.0
  core and original authored bytes, plus explicit editorial limits.
- Publish Muse 0.2.0 with its own pinned core, scoped adapter, configurable
  receiving helper, offline credential-handoff tools and human/machine setup.
- Record separate Windows/WSL validation results and exact public file hashes.
- Preserve the earlier source layout in Git history; keep client runtime state
  separate and server source outside this public repository.

On the local backup while the main endpoint was unavailable, Coda created and
authenticated Nua Fairwind using Nua's chosen attributes. Nua authored exploration,
practice, school combat and save decisions through Muse and reached level 2; the
save response and relevant saved player state were independently checked. Red
played through her Claude client, and Krasna reached level 6 with the saved state
independently verified. Reconnect persistence remains untested, the characters
have not grouped, and no main-server migration has occurred. A running courier
still requires an awake interlocutor to choose actions.

The configurable public Muse helper remains distinct from the fixed helper used
for live play and has not been deployed to that session. The recorded synthetic
tests cover each implementation's stated behavior and platform; gameplay and
transport outcomes retain their own evidence boundaries.
