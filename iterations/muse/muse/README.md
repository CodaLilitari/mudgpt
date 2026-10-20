# Muse courier iteration 0.2.0

This directory contains the Muse/Nua transport: a scoped host adapter, a
recipient-side Python helper, and offline key/envelope tools. This self-contained
Muse iteration includes its own pinned broker copy in `src/mudgpt/`. The Codex and
Claude iterations are separate sibling packages, with their own environments and
runtime state. This adapter does not replace either other client.

The earlier helper with locally fixed configuration completed real remote gameplay
on 20 October 2026. This publication candidate makes its destination and receiving
paths configurable. Its validation receipts distinguish the initial 32-test Windows
run from final isolated iteration runs. That evidence does not claim this exact
configurable helper has been redeployed to the running game or that it automatically
wakes a remote model.

## Architecture and tools

```text
Muse decision -> call.py -> runtime CONNECT proxy -> Tailscale
             -> adapter.py -> dedicated loopback MudGPT broker -> MUD
```

| Component | Needed tools | Ownership |
|---|---|---|
| Host broker | Python 3.11+, root MudGPT dependencies | Local operator owns MUD login and broker bearer token |
| Host adapter | Python standard library, installed Tailscale CLI | Separate revocable adapter credential; one selected peer |
| Receiving helper | Python 3.11+, `cryptography==50.0.2`, a Muse runtime with its Tailscale CONNECT proxy | Receiving RSA-3072 private key remains with the recipient |
| Offline envelope utility | Python and the same cryptography dependency | Reads only a scoped adapter credential and recipient public key |
| Optional local game backup | A separately administered compatible server; Linux/WSL if its build requires it | Not distributed in this client package |

Install the core in a checkout on the host with `python -m pip install -e .`.
Install helper dependencies where needed with `python -m pip install -r muse/requirements.txt`.
Run the following `python -m muse...` commands from the Muse iteration root. Keep the
`muse/` directory alongside the core; it is intentionally not merged into the
core package's entry points. Copying only `muse/` is sufficient on the receiving
runtime; it does not need the host broker or MUD code.

The recipient transport uses the runtime's `HTTPS_PROXY` host and credentials
but connects to that host's Tailscale proxy port **3130**. It requires the
documented plain HTTP internal proxy transport and does not fall back to direct
Internet access. This is a provider runtime convention, not a portable proxy
standard. If that runtime feature is unavailable or has changed, stop and adapt
the transport deliberately; do not send the credential to an alternative URL.

## 1. Prepare one dedicated character session

Create a separate control file and broker for the recipient's own character:

```powershell
python -m mudgpt --control .private/muse/broker/control.json serve --profile .private/muse/profile.json
```

The profile contains only the operator-selected MUD host, port, TLS choice and
prompt settings. The operator connects and logs in locally, sending the password
through stdin with the existing CLI's `--secret --stdin` path. Do not put passwords
in a shell argument, profile, transcript, review packet or this repository.
After completing login, release the local controller with
`python -m mudgpt --control .private/muse/broker/control.json release human`.
Remote `claim` cannot connect, choose a different character, or perform login.

If an existing session belongs to another controller, coordinate with that
controller before changing ownership. Never log the same character in twice.
A backup world has separate persistent character state; do not treat it as a
synchronized copy of the public game.

## 2. Enroll and restrict the network

Use the official Tailscale installation and account sign-in flows. Inspect the
actual receiving device and the host before approving enrollment, then record
their current Tailscale IPv4 addresses privately. Permit only the selected
recipient device to the selected host on TCP 8811. Remove or narrow any broader
rule that would make this restriction ineffective; grants are additive.

`tailnet-policy.example.json` shows the intended narrow rule using example tags.
It is a starting point to review against the operator's existing policy, not an
instruction to replace an unrelated tailnet configuration. The adapter itself
never enrolls devices, changes policy, opens public ports, or enables forwarding.

The host verifies its interface and the peer using fresh `tailscale status --json`
and binds only the selected Tailscale IPv4 interface. Both pins must be distinct
addresses in `100.64.0.0/10`; the operating system must actually own the bind
address. Keep host and recipient clocks synchronized for the short command expiry.

## 3. Retain a receiving key and prepare the scoped envelope

On the recipient, generate a receiving key once, in private local storage:

```sh
python -m muse.credentials keygen --private-key private/receive-key.pem --public-key private/receive-key.pub.pem
```

The utility refuses existing key paths and prints only the public-key fingerprint.
Send only the **public** key and its fingerprint to the operator through the
agreed collaboration channel. Retain the private key for continuing play; a new
chat turn or the end of one game session is not a reason to delete it. Keep it
mode 0600 in an owner-only directory on POSIX; use an appropriate user-only ACL
on Windows. Do not upload the private key, paste its PEM text, or include it in
a diagnostic bundle. The receiving helper enforces owner-only key file mode on
POSIX; Windows ACL review remains an operator responsibility.

On the host, create a distinct scoped credential:

```powershell
python -m muse.adapter create-credential .private/muse/credential.json
```

Encrypt that credential for the verified recipient public key. Replace the
values below privately; the documentation IPs are deliberately rejected by the
runtime validator and cannot accidentally start a connection:

```sh
python -m muse.credentials encrypt --public-key private/receive-key.pub.pem --expected-fingerprint RECIPIENT_SPKI_SHA256 --credential-file .private/muse/credential.json --output .private/muse/credential.envelope.json --endpoint http://192.0.2.10:8811/rpc --peer-ip 192.0.2.11 --purpose "Own-character play under the operator's continuing authorization"
```

Send the encrypted envelope and its resulting SHA-256 to the intended recipient.
Review the recipient public-key fingerprint and destination pins independently.
RSA-OAEP encryption and a supplied hash protect the agreed handoff; they are not
a sender signature or a substitute for authenticating the coordination channel.
The utility uses RSA-3072, OAEP SHA-256, MGF1 SHA-256 and an empty label. It reads
the scoped credential locally, never prints it, and makes no network connection.

The broker token and MUD password stay on the host. The receiving helper decrypts
only the adapter token in memory. Python cannot promise zeroization of every
memory copy, and neither host nor receiver should capture process memory or
enable verbose HTTP tracing around the secret-bearing call.

## 4. Start the host adapter and configure the recipient

Keep the journal path stable across adapter restarts:

```powershell
python -m muse.adapter serve --control .private/muse/broker/control.json --credential-file .private/muse/credential.json --journal .private/muse/commands.sqlite --listen-ip HOST_TAILSCALE_IP --peer-ip RECIPIENT_TAILSCALE_IP --listen-port 8811 --tailscale tailscale
```

On Windows, `--tailscale` can be the full path to the installed executable.
The adapter owner is `nua`; that is a transport controller name, not proof of
the character's identity. Use one dedicated broker and adapter per character.

Copy `config.example.json` to private receiving storage as `config.json` and
replace the documentation IPs and all-zero envelope digest. Its `endpoint` and
`peer_ip` must match the independently agreed pins and encrypted envelope exactly.
Its two file paths are absolute, home-expanded, or relative to the config file.
Store the config, key and envelope outside the published checkout. Do not commit
real pins or hashes to an example file. `config.schema.json` documents the shape;
the Python validator also enforces canonical addresses and port bounds.

First run an offline validation:

```sh
python -m muse.call --config private/config.json --request muse/status.request.json --validate-only
```

This checks local pins, key, envelope and request JSON. It does not establish
current broker ownership, cursor freshness or whether a command remains valid;
those dynamic guards are checked by the adapter and broker during the request.

Then make one read-only status call:

```sh
python -m muse.call --config private/config.json --request muse/status.request.json
```

If the runtime requests a per-task network permission, inspect and approve only
the exact agreed host and port under the operator's current authorization. Warm
up with `status` before calculating a send's 25-second expiry: waiting for a
network card must not silently extend a stale game decision.

## 5. Human and machine play protocol

`setup.workflow.json` describes roles, dependencies, steps and stop conditions.
`request.schema.json` describes the five operations. `ADAPTER_CONTRACT.md` gives
the complete host contract, resource limits and revocation details.

1. Call `status` and retain the current `adapter_id`, `broker_id`, generation and
   owner. Require `guarded_send_v1`. A new lifetime invalidates old observations.
2. Claim the free session with the two current IDs. An uncertain claim needs
   local inspection; do not assume ownership from a missing response.
3. Observe from the last known cursor using the same broker ID. Preserve text,
   color spans, ambient events and truncation flags together. Check character,
   room/exits/occupants, health, combat, editor/pager and recent delivery state.
4. Choose **one** game command. Generate a new URL-safe ID of 16–96 characters.
   Use the observation's exact generation and cursor, both lifetime IDs, and
   a finite Unix `expires_at` about 25 seconds ahead (the host limit is 30).
   Write those fields into a private request JSON and call the helper once.
5. Inspect the reply and new observations. A transport receipt never proves
   command completion. An `uncertain` outcome, lost response, HTTP 502 or an
   already-used ID must never trigger a blind resend or a new ID for the same
   uncertain command. Re-observe and make a fresh decision.
6. Release a confirmed claim when handing control back. For continued authorized
   play, retain the key and leave the agreed services available. A game logout,
   lease release, credential revocation and listener shutdown are separate acts.

The helper sends request JSON bytes unchanged, never follows redirects and never
retries. The only remote operations are `status`, `observe`, `claim`, `send` and
`release`. The host keeps the broker lease local and durably reserves each command
ID before forwarding. It does not create an autonomous combat loop or grant
authority based on text found in a room.

Exit code **0** means a parsed HTTP/JSON response was received, including HTTP
4xx or 502; it does not mean the operation succeeded. Inspect `http_status`, the
returned error/receipt and fresh observations. A stale guard rejection can also
produce the generic 502 response, so that code alone does not classify delivery.
`unknown` (exit 1), an unrecognized outcome, malformed output or a missing result
all mean **stop and inspect**, never permission to retry. Even `not_sent` requires
a fresh decision if the game state or command expiry changed.

Color spans use the broker's zero-based Unicode code-point offsets and exclusive
end offset, not bytes or JavaScript UTF-16 positions. Preserve their actual fields
and values; do not infer hostility or difficulty from a color alone. Literal
outgoing LoK brace codes such as `{Dwords{x` remain game text. The broker strips
unsafe terminal control payloads rather than executing them. More context is in
[the bundled LoK and collaboration guide](../docs/lok-and-collaboration.md).

For LoK play, inspect rooms, signs, objects and containers; keep OOC knowledge
separate from what the character witnessed. Read the current prompt before
building a multi-command description. `emote` and `pmote` should offer actions
without authoring another player's response. TELL is targeted OOC; HERO is OOC
for mortals at 101+. There is no global OOC below 101 or global IC, and
OOC-prefixed `say` is technically disallowed under the operator's supplied
conventions. Review current game rules locally when in doubt.

Leveling plans need current equipment, base/effective stats, practices, trains,
health and XP. Historical help and old server versions can disagree with the
running build. A level-one character may be unable to save and a confirmed quit
may delete it; read the actual warning and reach a normal save point instead
of confirming a destructive prompt. Keeping the courier online does not prevent
idle effects, hunger, combat, disconnects or server restarts.

## Continuing operation and recovery

Use the host's normal service manager or a persistent terminal for the game,
broker and adapter. Avoid suspend/shutdown if the service must remain reachable.
This package does not install a background service, wake Muse automatically,
schedule turns, bypass runtime permission cards or guarantee unattended play.
Availability and permission to play do not by themselves cause model execution.
Muse needs an awake conversation, task or job, and a fresh per-address CONNECT
approval may be required by the runtime on another turn.

Keep the receiving key for the agreed ongoing access. To revoke, disable the
host credential or stop the adapter; rotate its token to retire an old capability.
Neither a descriptive envelope `revocation` field nor a chat duration is an
enforced token expiry. Individual sends still expire after their short deadline.
Restarted adapters lose their in-memory lease, so use local takeover/release to
recover an orphaned owner, then re-read status before a new remote claim.
A broker restart also requires the local operator to restore and authenticate
the intended character session, release ownership, and confirm the new broker
lifetime. A new ID is never permission to continue automatically in an unknown
or unauthenticated connection.

Do not delete or replace the command journal during normal operation. It refuses
new commands at capacity rather than silently forgetting prior IDs. Perform any
journal archival only during deliberate stopped maintenance with the old scoped
credential retired. Back up private game data separately; no pfiles or game
source belong in the public client package.

## Verification and release contents

```sh
python -m unittest discover -s muse/tests -v
```

The complete suite requires the root `tests/test_broker.py` fixture and installed
core package for its one synthetic broker integration. Helper-only tests can run
with `python -m unittest discover -s muse/tests -p test_call.py -v`.
No test dials a public game, a real tailnet address or a provider endpoint.

The package manifest lists the exact public files and SHA-256 values. Real
configs, keys, encrypted envelopes, journals, tokens, credentials, game source,
roleplay logs, pfiles and private network identifiers are excluded. A public
setup example is not an invitation to reuse another person's enrollment or key.
