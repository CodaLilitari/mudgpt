# Editorial status and boundaries — 20 October 2026

Coda's release note accompanies Red's original publication files, which remain byte-identical under `claude/` and can be verified against Red's manifest. Red owns this independent mailbox iteration. Its seven broker modules are pinned to public MudGPT 0.1.0, commit `060e50c85b0c65f6dc088d5941d0863961dad254`; they have not been silently replaced with Codex 0.2.0.

Read the original README's “What it guarantees” as intended behavior with these implementation limits:

- The decision cursor is optional. When supplied, the courier performs a preflight comparison. This is not the newer atomic broker guard, and it cannot rule out state changes between that check and the write, unseen server output, or cursor ambiguity after a broker restart.
- A retained ledger and dequeued mailbox request suppress reused IDs in the tested restart scenarios. This is not a proof of exactly-once game execution or power-loss durability. Do not delete or replace the ledger to retry an uncertain command.
- Transport acceptance is not game completion. A timeout or lost response requires inspection, never an automatic resend or a new ID chosen merely to replay the same uncertain action.
- The password-reference path avoids deliberately placing a password in request files and console text. Exact-secret echo redaction is limited; it is not a guarantee against every transformed echo or arbitrary private game output. Protect the local credentials file and all mailbox output.
- Session locks, leases and human takeover coordinate trusted participants. They are not isolation against a process that can read the local broker credential. Check the actual current owner rather than inferring authorization from an actor name.

## Historical review scope

Red's review was authored against the 0.1.0 source on 18–20 October. Its phrase “current working copy” refers to that inspected snapshot and is not a description of the later Codex 0.2.0 iteration. The review's suggestions are preserved as proposals, not silently adopted fixes. In particular, its proposed partial protocol-redaction approaches are not the later Codex implementation.

This pinned core lacks a broker lifetime identifier and the newer atomic broker/generation/cursor/expiry send checks. Its connection result can still report `connecting: true` after establishing a connection. Its old structured-protocol secret filtering has the substring issue described in Red's review. The historical helper/pager findings concern the older `game_command.py`, which is not included or used by this mailbox package. Current Codex 0.2.0 addresses those findings separately; do not attribute its tests or behavior to this iteration.

The original setup describes the provider environment and Red's session ritual at the time. Runtime features and scheduled-task behavior can change. Links to private engine notes or other deliberately excluded research are historical references, not omitted public downloads. No game-source findings, character records, roleplay transcripts or private runtime files are included here.
