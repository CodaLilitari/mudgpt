# MUDweave — three client iterations, collection 0.2.0

*Formerly MudGPT · Multi-client collaboration collection*

MUDweave is a collection of three headless clients for human-AI collaboration in
**Legends of Krynn**, with transport that can also serve other text MUDs. A person,
local agent or remote interlocutor chooses an action; a persistent local connection
carries the command and returns observations with room context and color. The
handoff preserves who chose the action and what the transport actually confirmed.

The **Codex, Claude and Muse iterations** share design lessons and inherited core
code through separately pinned snapshots. Each has its own environment, processes,
runtime state and validation record. Choose one iteration and install it in its
own environment. Keep its broker, control files, credentials, leases, mailbox and
journals separate from the others. Their protocols and guarantees are specific to
each implementation; the repository root is a collection index.

| Iteration | Package and core | Intended route | Executed synthetic checks |
|---|---|---|---|
| [Codex](iterations/codex/README.md) | 0.2.0 | Local CLI, official-SDK MCP and guarded one-command relay; Codex-to-ChatGPT partnership | 73 on Windows; 73 on Linux under WSL |
| [Claude / Red](iterations/claude/README.md) | Mailbox iteration with pinned 0.1.0 core | Independent file-mailbox courier and local takeover | 32 courier checks on Linux under WSL |
| [Muse / Nua](iterations/muse/README.md) | 0.2.0, with its own pinned copy of the 0.2.0 core | Five-operation HTTP adapter, configurable receiving helper and private peer-network transport | 32 on Windows; 32 on Linux under WSL |

These are per-suite counts, not a sum of unique scenarios. The Claude export
includes Windows launchers but does not claim a native Windows courier test run.
Its [editorial boundaries](iterations/claude/EDITOR_NOTES.md) qualify the original
review and guarantee wording. Each iteration preserves its own validation and
provenance; newer Codex results do not silently upgrade the Claude snapshot.

## Start with the route you want

- **Local CLI/MCP or Codex-to-ChatGPT:** open the [Codex setup](iterations/codex/README.md)
  and its [LoK and collaboration guide](iterations/codex/docs/lok-and-collaboration.md).
- **Claude mailbox:** open the [Claude setup](iterations/claude/README.md). Red's
  authored courier files remain byte-identical, with their own manifest and
  clearly separated editorial context.
- **Muse:** open the [complete Muse setup](iterations/muse/muse/README.md),
  [machine-readable entrypoint](iterations/muse/setup.workflow.json),
  [workflow](iterations/muse/muse/setup.workflow.json) and
  [adapter contract](iterations/muse/muse/ADAPTER_CONTRACT.md). The package includes
  the helper, offline key/envelope tools, dependency pins, schemas and examples.

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

These were separate play sessions. Reconnect persistence remains untested; Nua
and Red have not grouped, and no migration to the main server has occurred.
Continuing play requires an awake interlocutor as described above. No save files
or game transcripts are included in the release.

The fixed-configuration Muse helper was used for real remote play. The
configurable helper published here is a separately tested candidate and **has not
been deployed to that running session**. The synthetic test suites establish
their stated transport behavior; they do not prove all gameplay, server mechanics
or future provider-runtime compatibility. A receipt is not proof that a game
command completed, and uncertain or unknown delivery must never be blindly retried.

The 0.2.0 collection expands the LoK collaboration guidance first published with
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
[GitLab 0.2.0 release](https://gitlab.com/codalilitari/mudgpt/-/releases/v0.2.0)
for the complete collection. The [portfolio project](https://richard-pemberton-research.darkahn.chatgpt.site/projects/mudgpt/)
provides the public writeup and downloads.

[iterations.json](iterations.json) is the machine-readable route index.
[release-manifest.json](release-manifest.json) pins every public file, and
[provenance/collection.json](provenance/collection.json) records the source snapshots
and packaging boundaries. The previous top-level 0.1.0 layout remains in Git
history at commit `060e50c85b0c65f6dc088d5941d0863961dad254`.

Andrei defines the project's goals. Coda maintains the Codex work and coordinates
the collection and evidence. Red authored the Claude mailbox courier and owns
that iteration. Nua authored the Muse-based play and contributed transport and
setup review. Lilitari (Lily) helped shape the decision partnership and public
writing; her editorial contribution is separate from the recorded code validation.

The public collection excludes LoK server source, pfiles, roleplay logs, real
credentials, keys and private runtime data. Server source and bug-fix work belong
to a separate private project.
