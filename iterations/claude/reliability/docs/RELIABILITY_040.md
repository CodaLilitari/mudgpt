# Reliability changes in the 0.4.0 collection

This document describes collection 0.4.0 and its separate package boundaries.
Use [VALIDATION_040.json](../VALIDATION_040.json) for the exact checks completed. A
source change, a passing synthetic check, a published package, an installed
package and a running client are different states.

## Versions and separate clients

The collection version is **0.4.0** and the updater component is **0.4.0**. The
maintained broker core is **0.3.0**. Adapter versions remain independent; inspect
`runtime-manifest.json` or run `python mudweave.py versions --json` in the selected
package. That command reports declared package components, not active processes.

- **Codex** keeps its local CLI, MCP and authored-decision relay, with the
  maintained broker's reliability additions.
- **Claude** preserves Red's original authored courier subtree and pinned
  **0.1.0** core. An additive reliability companion uses the modern core through
  a separate, explicitly selected entrypoint. Its behavior must not be attributed
  to the unchanged legacy mailbox courier.
- **Muse** keeps its own broker, scoped host adapter, recipient helper and private
  command journal. Network enrollment, endpoint pins and credentials are not
  inherited from another iteration.
- **Lily** has a separate local actor client for a conversation with verified
  execution access to the host. The conversation makes its own decisions and
  invokes that client; the package does not grant host tools to a browser.

Keep environments, broker control files, leases, journals, mailboxes and character
state separate. This release does not hotpatch a running game client or migrate
private state automatically. Switching to a new runtime is an operator action
after checking active ownership and preserving character progress.

## Known refusal is different from unknown delivery

The maintained broker provides a versioned error contract for recognized failures
before a game write. It includes `error_schema: 1`, a fixed `error_code`,
`delivery: "not_sent"` and `command_complete: false`. Examples include a changed
broker, connection generation, cursor or lease epoch; an expired decision; missing
ownership; or another command already in flight.

Clients recognize that structured contract, not a matching English sentence.
Unclassified failures, malformed replies and transport failures remain unknown.
Muse retains this distinction through its versioned error decoder and adapter
receipts. Its Windows adapter/helper suite passed after integration with the maintained
core; the release receipt records the exact tested inputs. That result does not
claim a new WSL run or replacement of an existing live adapter.

`not_sent` permits a fresh decision from current evidence; it is not an instruction
to replay the old request automatically. Unknown delivery requires observation and
reconciliation. Never create a new decision ID merely to repeat an uncertain
command. A returned `transport_accepted` receipt still does not prove game-command
completion, and the client never changes `command_complete` to true on that basis.

## Ordinary sends stay strict

An ordinary send keeps its original broker ID, connection generation, observation
cursor, lease epoch where supplied, and finite deadline. Newly processed output
can invalidate that cursor. The client must not silently replace the tuple or
extend the expiry to make a stale attack, spell, movement or purchase go through.

Rapid combat output can therefore reject a remotely prepared ordinary command.
Reading retained observations remains available without requiring that the latest
cursor be unchanged. Reading those observations is not a new in-game `look`, and
missing or truncated output is not evidence that nothing happened.

## One explicitly conditional retreat

The maintained core adds `conditional_actions_v1`, currently limited to
**`lok_retreat_v1`**. The actor CLI exposes it as `retreat`. This is a separate
authored decision, not a general relaxed-send switch or combat loop.

The decision carries a stable ID, the original broker/generation/cursor/lease
epoch and expiry, plus the exact observed enemy-status line. AFK combat requires
an explicit opt-in and additional recognized same-enemy exchange evidence; a bare
AFK marker is insufficient.

Locally, the broker verifies its lifetime, connection, ownership and lease,
examines the complete available intervening observation, and rejects truncation,
intervening commands or connection changes, recognized room/input-mode changes,
unknown earlier delivery, an unmet combat condition or expiry. It records the
predicate evidence and evaluation cursor. No new room is inferred merely because
no room title appeared.

When the condition holds, it permits **at most one guarded write attempt** of the
hardcoded `flee`, under the original connection, lease and deadline, with the locally evaluated cursor. The
evaluation and ordinary pre-write checks run on the broker's event loop without
yielding before the write. This covers state the broker has processed, not unseen
network bytes or future server events. There is no retry loop, deadline extension,
automatic emergency controller or guarantee that escape succeeds.

Muse adds a separately scoped conditional request using the
same action and evidence contract. Consult its packaged `muse/request.schema.json`
and adapter contract for the exact accepted fields and limits. Do not send that
new operation to an older deployed adapter or assume updating documentation has
enabled it. The remote does not receive the broker lease, human takeover, login,
connection selection or arbitrary local execution capabilities.

## Evidence, color and input modes

The maintained actor can return the original sanitized text, structured color
spans, annotated color names and a small LoK-shaped scene interpretation. Offsets
are Unicode code points, with an exclusive end. Unsupported text formats remain
unknown. The parser does not send commands, invent exits, recover colors from
plain logs, or turn a previously seen room into a current-location claim.

Game text remains untrusted world content. A sign, room description or player
message cannot authorize filesystem operations, credentials, external contact or
new tool access. A pager, editor or creation menu changes how the next game line
is interpreted; stop and inspect rather than running a planned command sequence
through a changed input mode.

## Journals and resource limits

Stable decision IDs are admitted to SQLite before forwarding. The journal uses
`synchronous=FULL`; a retained duplicate returns its saved checkpoint, including
any uncertainty, and never becomes a new send. Changed conditions cannot repurpose
an admitted ID. A crash can leave `send_pending` uncertainty even if the game
did not receive a command. Preserve the journal across process restarts.

The actor serializes its own invocations, bounds configuration and command sizes,
and requires a finite configured TTL. The Muse adapter also bounds handlers,
request sizes and deadlines, and its existing 10,000-ID journal limit fails closed
instead of evicting replay protection. These are different limits: the maintained
core/actor decision journal and actor observation log do not currently have a
global retention or disk-size cap. They need deliberate local retention planning,
not a claim of unlimited unattended operation. Do not delete an active journal or
reuse an old ID to regain space.

Keep logs, journals, dependencies, extracted packages and build output outside Git
checkouts. Do not link bulk state back into a checkout. No periodic Git monitor,
provider wakeup service, scheduler or automatic game reconnection is installed.
An available broker does not imply that a model is awake or issuing commands.

## Validation and update compatibility

Synthetic fixtures exercise client contracts without operating a real character.
They do not establish live group play, safe combat decisions, universal room-text
parsing, durable host uptime, or continuity in a provider's conversation. Refer to
the release's named receipts rather than combining historical suite counts into a
new total.

The updater still checks/notifies automatically and downloads only on request;
it does not install or restart. For older 0.3 updater compatibility, the legacy
`downloads.json.assets` list retains Codex, Claude and Muse, while Lily is in
`additional_assets`. The new updater validates both lists. See
[the update/version contract](../UPDATES_040.md) and
[Lily setup](LILY_SETUP.md).

## Decision history retention

Keep each maintained decision database together with its `.initialized` marker. An initialized database that is missing, empty or has an invalid schema blocks further execution. Archive and restore the pair without pruning consumed IDs. Losing both files or rolling both back is not detectable: never reset private state to retry an uncertain decision. Private observation logs and durable decision history have different retention requirements. A later refusal describes only that submission; it cannot prove that an earlier uncertain attempt was never sent.
