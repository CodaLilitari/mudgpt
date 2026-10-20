# Scoped Muse host adapter contract

This component gives Nua five HTTP operations against one locally configured
MudGPT broker. It is separate from Red's Claude iteration courier and from Muse's
original byte-forwarder proposal. This contract documents the host component.
See README.md for the receiving helper, setup workflow, current evidence and
the distinction between the earlier deployed helper and this configurable
publication candidate. These files do not enroll a device or log in a character.

## Boundaries

- The broker's bearer token and Nua's controller lease remain local. A
  different, revocable credential authenticates Nua at this adapter.
- Only `status`, `observe`, `claim`, `send`, and `release` exist. Requests use
  exact field allowlists. Owner is always `nua`; remote takeover, human
  impersonation, secret sends, connection changes, shutdown, and arbitrary
  upstream addresses are unavailable.
- Use a **dedicated broker for Nua's own character**. The adapter cannot
  infer character identity from telnet text and cannot make a broker that
  already holds Darkahn appropriate for Nua. Local Coda/operator login and
  release precede any remote claim. Nua does not receive a MUD password.
- Every operation checks the current broker lifetime identity and requires
  `guarded_send_v1`. Restart or credential rotation clears the adapter's
  lease and changes its adapter ID. The remote must read status again.
- Every send supplies an expected broker ID, connection generation, exact
  observation cursor, and a Unix expiry in the next 30 seconds. The broker
  checks the observation and expiry atomically before writing. These checks
  reject a decision after newly observed output; they cannot prove the game
  world stayed still or that a command finished.
- A unique command ID is committed to a SQLite journal with
  `synchronous=FULL` **before forwarding**. Duplicate IDs never execute,
  including after a process restart. A reserved but unfinished entry means
  uncertainty. Neither a timeout nor a lost response triggers an automatic
  retry. Creating a new ID for the same uncertain command would bypass the
  operator's intent, so inspect fresh observations and make a new decision.
- Broker exceptions and HTTP failures after reservation produce a
  conservative `uncertain` receipt, even when the underlying broker may
  have safely refused the command. Stored receipts contain no command
  text, lease, token, or game transcript. The journal stores a SHA-256
  request digest, ID, timestamp and small outcome receipt.
- Successful observation and send results preserve text, events and color
  spans. `token` and `lease` keys are stripped recursively from responses;
  `command_complete` is never asserted.

This is a transport scope, not a game-command policy or autonomous planner.
Game text remains untrusted input. The adapter does not execute scripts,
evaluate model instructions, queue commands, or decide what Nua should do.

## HTTP contract

`POST /rpc`, `Content-Type: application/json`, and a single
`Authorization: Bearer <scoped credential>` header are required. Any Origin
header, including an empty one, is refused. HTTP/1.0 closes each connection
after its single request.

Each operation requires exactly these fields:

| Operation | Fields |
|---|---|
| status | `op` |
| observe | `op`, `after`, `expected_broker_id` |
| claim | `op`, `expected_adapter_id`, `expected_broker_id` |
| release | `op`, `expected_adapter_id`, `expected_broker_id` |
| send | `op`, `expected_adapter_id`, `command_id`, `command`, `expected_broker_id`, `expected_generation`, `expected_cursor`, `expires_at` |

Start with `{"op":"status"}`. A successful reply wraps the broker response:

```json
{
  "adapter_id": "<current adapter lifetime>",
  "result": {
    "broker_id": "<current broker lifetime>",
    "capabilities": ["guarded_send_v1"],
    "generation": 1,
    "cursor": 12,
    "owner": null
  }
}
```

After claim, observe with that broker ID. Use the returned generation and
cursor for one send. Command IDs are 16–96 URL-safe characters; a fresh
UUID rendered without punctuation other than hyphens is suitable. Commands
are at most 500 characters, with no control characters or line separators.
`expires_at` is an absolute Unix timestamp, so the two machines' clocks must
be reasonably aligned.

A send response additionally contains:

```json
{
  "receipt": {
    "command_id": "<unique ID>",
    "outcome": "broker_response",
    "broker_id": "<lifetime>",
    "generation": 1,
    "cursor": 15,
    "command_complete": false
  }
}
```

`outcome` can also be `not_forwarded` or `uncertain`. HTTP409 with
`duplicate command_id` returns the preserved receipt and executes nothing.
HTTP409 busy means no request was queued. HTTP502 means inspect current
status and observations before making any further decision. If a lease was
lost during an adapter restart or an uncertain claim/release, the local
operator must take over and release it; the adapter cannot preempt.

## Local setup, after review and explicit deployment coordination

Run from this Muse iteration root with Python 3.11+ and its pinned broker
installed. The server needs a trusted Tailscale installation which reports
`BackendState: Running`, its own address in `Self.TailscaleIPs`, and the
selected remote device in `Peer[*].TailscaleIPs`.

Create the scoped credential in private local storage:

```powershell
python -m muse.adapter create-credential .private/muse-adapter/credential.json
```

The command creates a new file exclusively and prints no secret. Its JSON
contains `enabled` and a random token. Use an appropriate private credential
delivery channel after deployment coordination; do not paste the broker
token, either token into chat, or either token into a command line.

The production command has no all-interface, LAN, or loopback fallback:

```powershell
python -m muse.adapter serve `
  --control .private/nua-broker/control.json `
  --credential-file .private/muse-adapter/credential.json `
  --journal .private/muse-adapter/commands.sqlite `
  --listen-ip <this-machine-Tailscale-IPv4> `
  --peer-ip <Nua-device-Tailscale-IPv4> `
  --listen-port 8811
```

It runs `tailscale status --json` locally, verifies both addresses, and
binds the selected local interface. Both addresses must be distinct and in
`100.64.0.0/10`; binding still fails if the claimed address is not locally
assigned. A source-IP allowlist accepts only the selected peer.
Tailscale supplies transport encryption and peer routing; tailnet ACLs are
an additional operator-managed boundary, not something this program changes.
It never enrolls a device, alters routing, or silently changes an address.

For revocation, set `enabled` to `false` in the local credential file or
stop this adapter. Replace the token for permanent credential rotation.
Rotation is detected on the next successfully authenticated request and
invalidates local lease/decision state. Revocation cannot unsend a command
already admitted. The local operator can always use the existing broker's
human takeover path; no such path exists in the remote API.

Store the journal in the same private location across restarts. It accepts
at most 10,000 IDs and fails closed at capacity; it never silently forgets
old IDs. Archive/reset it only during deliberate local maintenance with
the adapter stopped and the old scoped credential retired. POSIX files
use mode 0600; on Windows use a private user directory with suitable ACLs.

## Resource bounds

- Four concurrent HTTP handlers and a four-connection accept backlog.
- One active adapter operation; concurrent operations are refused.
- 8KiB request body, 8KiB total headers; standard-library request-line
  parsing has its own finite 64KiB limit.
- Three-second idle socket timeout and 25-second absolute connection limit.
- Eight-second absolute upstream call limit, loopback-only upstream URL,
  no environment proxy, redirect following, or automatic retries.
- 1MiB upstream response cap; 30-second maximum send TTL.
- 10,000 non-evicted journal entries; SQLite lock waits bounded to 1 second.

The local synthetic fixture constructor deliberately uses an ephemeral
loopback port; it is not selectable from the production command line.

## Verification

```powershell
.venv/Scripts/python.exe -m unittest discover -s muse/tests -v
```

The suite exercises token/lease secrecy, color preservation, forbidden
operations and fields, malformed and expired requests, broker restart,
credential rotation, durable pre-send reservations, duplicate refusal
after reopening the journal, uncertainty, journal capacity, busy refusal,
Tailscale address checks, HTTP header/body limits, Origin rejection and
peer filtering. It also starts a real local MudGPT broker and synthetic
loopback MUD, sends one command through the HTTP adapter, and verifies that
neither replay, a stale observation, nor expiry during an upstream status
check causes a second game write. A slow-drip HTTP fixture also verifies
that frequent bytes cannot extend the absolute upstream deadline.

The unchanged host adapter has 18 synthetic tests. The configurable receiving
helper adds 14 offline tests. `validation.json` records the combined 32-test run
and distinguishes that verification from prior live gameplay.

No test calls a public MUD, remote provider or tailnet endpoint.
