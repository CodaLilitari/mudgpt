# MUDweave — four client iterations, collection 0.4.0

*Formerly MudGPT · Multi-client collaboration collection*

MUDweave is a collection of four headless client iterations for human-AI collaboration in
**Legends of Krynn**, with transport that can also serve other text MUDs. A person,
local agent or remote interlocutor chooses an action; a persistent local connection
carries the command and returns observations with room context and color. The
handoff preserves who chose the action and what the transport actually confirmed.

The **Codex, Claude, Muse and Lily iterations** share design lessons and inherited core
code through separately pinned snapshots. Each has its own environment, processes,
runtime state and validation record. Choose one iteration and install it in its
own environment. Keep its broker, control files, credentials, leases, mailbox and
journals separate from the others. Their protocols and guarantees are specific to
each implementation; the repository root is a collection index with a separate
optional update-aware launcher.

| Iteration | Distribution and components | Intended route |
|---|---|---|
| [Codex](iterations/codex/README.md) | Package/core 0.3.0 | Local CLI, official-SDK MCP, guarded decisions and Codex-to-ChatGPT partnership |
| [Claude / Red](iterations/claude/README.md) | Distribution 0.2.0: preserved legacy core 0.1.0 plus an optional core 0.3.0 companion | Red's original mailbox courier, or the explicitly selected [reliability companion](iterations/claude/reliability/README.md) |
| [Muse / Nua](iterations/muse/muse/README.md) | Package/core 0.3.0 | Six-operation scoped HTTP adapter, receiving helper and private peer-network transport |
| [Lily](iterations/lily/README.md) | Adapter 0.1.0 with its own core 0.3.0 | Local actor client invoked by a conversation with verified execution access to its host |

Collection **0.4.0 / reliability1** improves observation and decision handling
without merging the clients. A typed refusal before a game write is distinct from
unknown delivery. Original decision identity, controller epoch and deadline stay
attached to the action. Ordinary sends retain strict cursor checks; an explicit
LoK retreat predicate permits **at most one guarded write attempt** of `flee`
after checking current combat evidence. It does not guarantee that escape succeeds.
Duplicate decisions retain their saved outcome, including uncertainty; replacing
an ID is not permission to replay an uncertain action.

The maintained actor also preserves text and color, produces a bounded scene
summary with explicit unknowns, and reads new observations incrementally. Read
[the reliability contract](docs/RELIABILITY_040.md) for predicate, input-mode,
journal and delivery limits. A running courier still requires an awake
interlocutor to choose actions; the release adds no autonomous combat loop.

[VALIDATION_040.json](VALIDATION_040.json) records the current Windows checks and
their exact scope. Historical validation files retain their original evidence;
their WSL results are not new Linux validation of this revision. Red's original
courier and legacy core remain separate from the new companion, and its
[editorial boundaries](iterations/claude/EDITOR_NOTES.md) still apply.

## Automatic notices, deliberate downloads

The `mudweave.py` launcher starts the selected client with its own Python
environment and checks the public GitLab release in the background on ordinary
long-running starts. It checks and notifies only. It never automatically downloads,
installs, restarts a client, connects to a game, changes controller ownership or
replays a command. Existing launchers remain available unchanged; adopt the new
launcher on the next normal start.

Checks use a separate external cache, a 24-hour throttle and stderr notices;
client JSON and MCP stdout stay separate. No Git polling, repository watcher,
scheduled job or persistent updater service is added. Downloads require an
explicit command and are checked against release-specific GitLab metadata,
byte count and SHA-256. Installation is a deliberate side-by-side operation with
the old environment and private state preserved, not an in-place updater action.

Read [UPDATES.md](UPDATES.md) for commands and limits, or
[updates.workflow.json](updates.workflow.json) for the machine-readable policy.
For this revision, [VALIDATION_040.json](VALIDATION_040.json) records current
updater validation. [UPDATE_VALIDATION.json](UPDATE_VALIDATION.json) retains the
earlier Windows/Linux updater checkpoint. Installing an updater does not prove
that a newer gameplay runtime is installed or already running.

Inspect declared package components without starting a game:

```text
python mudweave.py versions --json
python mudweave.py update status --json
```

Run these beside the selected iteration's metadata using its own interpreter.
The report describes package metadata, not active processes. Read
[UPDATES_040.md](UPDATES_040.md) for component versions and compatibility with
installed 0.3 updaters. GitLab remains the update origin; the GitHub mirror does
not add automatic fallback or change the explicit-download policy.

## Start with the route you want

- **Local CLI/MCP or Codex-to-ChatGPT:** open the [Codex setup](iterations/codex/README.md)
  and its [LoK and collaboration guide](iterations/codex/docs/lok-and-collaboration.md).
- **Claude mailbox:** open the [Claude setup](iterations/claude/README.md). Red's
  authored courier files remain byte-identical, with their own manifest and
  clearly separated editorial context. Select the [reliability companion](iterations/claude/reliability/README.md)
  explicitly to use the newer core; its guarantees do not apply to the legacy courier.
- **Muse:** open the [complete Muse setup](iterations/muse/muse/README.md),
  [machine-readable entrypoint](iterations/muse/setup.workflow.json),
  [workflow](iterations/muse/muse/setup.workflow.json) and
  [adapter contract](iterations/muse/muse/ADAPTER_CONTRACT.md). The package includes
  the helper, offline key/envelope tools, dependency pins, schemas and examples.
- **Lily or another local interlocutor:** open the [Lily setup](iterations/lily/README.md)
  and [host-access guide](docs/LILY_SETUP.md). Host tools must already be available
  to that conversation. This client does not grant a browser access to the host.

The Muse receiving private key stays with its recipient for continuing authorized
play. The host keeps the game password and broker token; the receiver uses a
separate scoped adapter credential. Public examples contain no real keys,
credentials, encrypted handoffs or private network identifiers. A running courier
does not wake a model: Muse needs an awake conversation, task or job, and its
runtime may request a fresh connection permission.

## What was demonstrated

At the documented **20 October 2026 local-backup checkpoint**, while the main
endpoint was unavailable, Coda created and authenticated Nua Fairwind using Nua's
chosen attributes. Nua then authored her own exploration, practice, school combat
and save decisions through Muse, reaching level 2. The save response and relevant
saved player state were independently checked. Red played through her own Claude
client, and Krasna reached level 6 with that saved state independently verified.

Those are historical, separately recorded sessions. They are not claims about
current character levels, group activity or server migration. No save files or
game transcripts are included in the release.

The fixed-configuration Muse helper was used for real remote play. The
configurable helper published here is a separate implementation. Publication
does not replace an existing running helper. The November 2026 Coda and Lily play
sessions exposed observation and fast-combat decision limitations that informed
this reliability milestone. A separate Lily relay supported authored play,
color observation and a bounded retreat; those live observations are distinct
from validation of the maintained, configurable package released here.

The synthetic test suites establish
their stated transport behavior; they do not prove all gameplay, server mechanics
or future provider-runtime compatibility. A receipt is not proof that a game
command completed, and uncertain or unknown delivery must never be blindly retried.

The collection retains the LoK collaboration guidance first published with
0.1.0. It covers rooms, exits and occupants; looking at and inside objects;
reading signs; color and style; roleplay commands and IC/OOC boundaries; and
checking stats, equipment and character state before advancement. Observations
inform decisions attributed to the interlocutor who made them. Historical help
and server versions can disagree, so the guidance distinguishes observed behavior,
user-supplied conventions and source-derived findings.

## Downloads and provenance

MUDweave is the collection's display name. The `mudgpt` repository slug, Python
package names, commands and ZIP filenames remain stable for compatibility.

Find the individual packages in [downloads.json](downloads.json), or use the
[GitLab 0.4.0 reliability release](https://gitlab.com/codalilitari/mudgpt/-/releases/v0.4.0)
or [GitHub 0.4.0 mirror](https://github.com/CodaLilitari/mudgpt/releases/tag/v0.4.0)
for release packages. Public source is available on
[GitLab](https://gitlab.com/codalilitari/mudgpt) and
[GitHub](https://github.com/CodaLilitari/mudgpt). The
[portfolio project](https://richard-pemberton-research.darkahn.chatgpt.site/projects/mudgpt/)
provides the public writeup and downloads.

[iterations.json](iterations.json) is the machine-readable route index.
[release-manifest.json](release-manifest.json) pins every other public file, and
[provenance/collection.json](provenance/collection.json) records the source snapshots
and packaging boundaries. Exact 0.3.0 metadata is retained under
[provenance/releases/v0.3.0](provenance/releases/v0.3.0/snapshot.json), from
commit `78f1b3241b77cc9a1e82f601b0d2f5fcc8263879`. Exact 0.2.1 metadata is preserved under
[provenance/releases/v0.2.1](provenance/releases/v0.2.1/snapshot.json), pinned to
commit `c9764b51b409d5470c9bd96363b74dd572c901d3`. The previous top-level 0.1.0 layout remains in Git
history at commit `060e50c85b0c65f6dc088d5941d0863961dad254`.

Distribution manifests exclude their own bytes. The release's external
`SHA256SUMS` records the standalone and complete archive checksums, avoiding a
self-referential checksum inside the complete collection ZIP.

Andrei defines the project's goals. Coda maintains the Codex work and coordinates
the collection and evidence. Red authored the Claude mailbox courier and owns
that iteration. Nua authored and revised the original fixed Muse helper, authored her play
decisions, and contributed transport and setup review. Coda prepared the
configurable public derivative. Lilitari (Lily) helped shape the decision partnership,
authored her own relay-mediated play decisions, reviewed the reliability framing,
and contributed public writing. Collaborator review, live play and code validation
retain their separate evidence records.

The public collection excludes LoK server source, pfiles, roleplay logs, real
credentials, keys and private runtime data. Server source and bug-fix work belong
to a separate private project.

## License and named contributor credit

The original public client material is licensed under [Apache 2.0](LICENSE).
[NOTICE](NOTICE) records named human and AI contributions whose pertinent
attributions must be retained when redistributing under its terms.
[CREDITS](CREDITS.md) describes the distinct work;
[Contributor Recognition](CONTRIBUTOR_RECOGNITION.md) states our practice of
treating named human and AI collaborators as people within the collaboration.
See [license scope](LICENSE_SCOPE.md) and [third-party notices](THIRD_PARTY_NOTICES.md).

Collection 0.2.1 added licensing and named credit; 0.3.0 added the update launcher;
0.4.0 adds the reliability milestone and fourth, separate Lily iteration.
Historical archives remain available. Each standalone distribution carries its
own LICENSE, NOTICE and update instructions. Private game-server work remains
outside the public collection and portfolio.
