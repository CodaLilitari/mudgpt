# Update compatibility and declared component versions

Collection 0.4.0 keeps the client iterations separate. Collection, broker core,
adapter and updater versions describe different components. Installing an
update-check launcher does not establish that a newer gameplay runtime is
installed or that an already running session has changed.

## Inspect versions without starting anything

From the selected package directory, using its own Python environment:

```text
python mudweave.py versions
python mudweave.py versions --json
python mudweave.py update status --json
```

`versions` reads only the adjacent public `runtime-manifest.json`. It does not
contact GitLab, create an update cache, start a client or inspect private state.
Update `status` retains its existing local cache check and adds `version_report`.
The report labels its evidence `public_runtime_manifest` and always returns
`runtime_verified: false`. Declared package contents are not a measurement of an
active process, a source-hash verification or proof that a user selected a
particular optional entrypoint.

The collection manifest may enumerate Codex, Claude, Muse and Lily components.
A standalone iteration reports its own components. Run the command beside that
iteration's metadata; changing `--iteration` does not switch Python environments
or substitute a different installed runtime. A missing, inconsistent or invalid
manifest reports `unavailable`, leaving component versions empty instead of
guessing from the updater version.

## Public runtime manifest

The following is a **synthetic schema example**, not a claim about any current
release's component versions:

```json
{
  "schema_version": 1,
  "collection_version": "0.4.0",
  "iteration": "lily",
  "components": {
    "core": {"version": "0.3.0", "status": "included"},
    "adapter": {"version": "0.1.0", "status": "included"},
    "updater": {"version": "0.4.0", "status": "included"}
  }
}
```

Each component has a strict three-part version and one of these statuses:

- `included`: declared contents of this package.
- `historical`: preserved older contents, not silently replaced.
- `optional`: a component requiring an explicit entrypoint/selection.
- `not_applicable`: no such component; its version must be `null`.

A collection uses `iteration: "collection"`, marks its non-runtime core/adapter
as not applicable, and may add `iterations`, with up to four unique records of
the form `{"iteration": "codex", "components": {...}}`.

An explicitly separate companion may be described with:

```json
{
  "companion": {
    "name": "reliability",
    "entrypoint": "reliability/launcher.py",
    "components": {
      "core": {"version": "0.3.0", "status": "optional"},
      "adapter": {"version": "0.2.0", "status": "optional"},
      "updater": {"version": "0.4.0", "status": "optional"}
    }
  }
}
```

This optional object may appear at the manifest root or on a collection's
iteration record. It never changes which program is launched. In particular,
report a preserved Claude core separately from a modern companion; do not label
the original mailbox courier as upgraded merely because the companion exists.
Unknown metadata is not echoed into reports. Private paths, credentials,
controller state and game observations do not belong in this public file.

## Keep installed 0.3 clients able to discover the release

The 0.3 updater validates **every** row in `downloads.json`'s `assets` array
against its original iteration-ID allowlist. Putting a new Lily row directly in
that array would make those installed clients reject the entire new manifest.

For this release, preserve the three existing IDs in `assets`, and put Lily in
the additive `additional_assets` array:

```text
assets:            Codex, Claude, Muse
additional_assets: Lily
iterations.json:   all four iterations and their independent versions
```

The 0.4 updater validates the combined arrays, with a total limit of sixteen
assets and no duplicate iteration IDs. It accepts `lily` for settings and CLI
selection. The older updater ignores the additive field and can continue
selecting its own verified archive from `assets`. Release asset links must still
match exact filenames and the existing trusted GitLab project/path rules.

The complete collection can include all four packages. Its existing integrity
metadata requirements remain unchanged; it is not a reason to bypass per-asset
hash and size checks. A GitHub mirror does not change the update service's
GitLab origin or add automatic network fallback.

Before publication, verify the final manifest with both the new updater and an
exact frozen 0.3 updater outside the checkout. The synthetic compatibility tests
document the legacy ID contract but do not substitute for that old-client run.

## Unchanged operating policy

Automatic behavior remains a bounded check and notification on an update-aware
long-running launch. Short gameplay commands do not start network checks.
Downloads require an explicit request. Installation remains deliberate and
side by side. No update operation installs files into an active runtime,
restarts a process, connects to the game, takes control, migrates state or
replays a command. Existing authored launchers remain unchanged.

Keep each iteration's environment, broker, mailbox, journals, credentials and
character state separate. Use external storage for build/test environments and
expanded packages; do not create dependency links back into source checkouts.
