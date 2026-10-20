# MudGPT — Codex iteration 0.2.0

MudGPT is a headless MUD client for Windows and Linux, developed first for Legends of Krynn (LoK) and designed to remain useful with other text MUDs. Its play workflow includes LoK-specific lessons about room awareness, roleplay, color, equipment and character progression. A persistent local broker owns one Telnet connection. A command-line interface and a Model Context Protocol (MCP) stdio adapter share that connection, while independent readers receive the same bounded event history.

This is an early developer release of the Codex iteration. The Claude mailbox courier and Muse adapter are separate iterations with their own setup, tests and protocol boundaries. It supplies the transport and control handoff for a person or an AI host to carry commands and observations back and forth. It does not call an AI provider, create accounts, choose game actions, or give a remote collaborator its own socket. No provider API key is required by the client.

Read [LoK play lessons and Codex-to-ChatGPT collaboration](docs/lok-and-collaboration.md) for the intended decision partnership: a local Codex courier carries fresh observations to a named, saved ChatGPT conversation and returns the interlocutor's chosen command through the local client. The browser conversation does not directly own an MCP connection or game socket.

## Install

Python 3.11 or newer is required. The direct dependencies are pinned to `telnetlib3==4.0.3` and the official Python MCP SDK `mcp==2.3.0`.

Using uv and the included dependency lock:

```text
uv sync --frozen
uv run python -m mudgpt --help
```

Alternatively, create and activate a virtual environment, then install the project. This pins the direct dependencies; use the lock workflow above to reproduce transitive versions.

```text
python -m venv .venv
```

On Linux, activate with `source .venv/bin/activate`. On Windows PowerShell, activate with `.venv\Scripts\Activate.ps1`. Then run:

```text
python -m pip install -e .
python -m mudgpt --help
```

## Start a local session

Copy `examples/profile.json` to `runtime/profile.json` and replace the placeholder host and port with your chosen game. `example.org:4000` is documentation data, not a playable endpoint. Profiles accept only `host`, `port`, `tls` and optional `prompt_pattern`; put no credentials in them.

Start the broker in one terminal:

```text
python -m mudgpt --control runtime/control.json serve --profile runtime/profile.json
```

In another terminal, from the same directory and environment:

```text
python -m mudgpt --control runtime/control.json connect
python -m mudgpt --control runtime/control.json observe --view plain
python -m mudgpt --control runtime/control.json status
```

Read each actual login prompt before sending the next line. For ordinary nonsecret input:

```text
python -m mudgpt --control runtime/control.json send YourCharacter
```

For a password or other secret, use hidden local input and the CLI's stdin redaction path. This example avoids putting the secret in the command line or shell history:

```text
python -c "import getpass,subprocess,sys; subprocess.run([sys.executable,'-m','mudgpt','--control','runtime/control.json','send','--secret','--stdin'],input=getpass.getpass('Game password: ')+'\n',text=True,check=True)"
```

Redaction masks registered literal secrets in observed text, including split echoes, but it is not encryption or a general personal-data sanitizer. With `tls: false`, game traffic is plain Telnet. If the server supports Telnet over TLS, set `tls: true`; certificate validation is enabled.

After login, send one game command at a time and inspect its observation:

```text
python -m mudgpt --control runtime/control.json send look --view annotated
python -m mudgpt --control runtime/control.json observe --after 0 --view ansi
```

`json` output preserves event metadata and color spans. `plain` strips styles, `ansi` renders allowed SGR styles, and `annotated` names them inline. The client preserves literal game formatting tokens when sending commands. Received terminal control strings are filtered rather than executed.

`prompt_pattern` is an optional Python regular expression for a known game prompt. Leave it absent until you have observed the game's format. A recognized prompt is an observation boundary, not a game-success assertion.

## Connect an MCP host

The MCP adapter is a local stdio process using the same broker control file. Configure your MCP host to launch an absolute Python executable with these arguments, substituting your installation paths:

```json
{
  "mcpServers": {
    "mudgpt": {
      "command": "/path/to/mudgpt/.venv/bin/python",
      "args": ["-m", "mudgpt", "--control", "/path/to/mudgpt/runtime/control.json", "mcp"]
    }
  }
}
```

On Windows, use the environment's `Scripts/python.exe` executable and Windows absolute paths. Host configuration formats vary; the executable and argument list are the essential parts. If the package is not installed in that interpreter, set `PYTHONPATH` to the release's `src` directory.

The tool surface is intentionally small:

| Tool | Operation |
| --- | --- |
| `mud_status` | Read connection state, current owner and latest command receipt. |
| `mud_observe(after)` | Read retained events after a cursor without consuming them. |
| `mud_claim(owner)` | Claim available control using a chosen actor label. |
| `mud_send(command, owner, timeout)` | Send one plain game command with a bounded observation wait. |
| `mud_release(owner)` | Release the current adapter's control. |

The human owns control initially. Release it locally before an adapter claims its own label:

```text
python -m mudgpt --control runtime/control.json release human
```

The adapter keeps its claim lease internally. Old leases cannot send after a handoff, even if the next controller uses the same label. Closing an MCP adapter does not close the persistent game or automatically release its claim. To recover control locally:

```text
python -m mudgpt --control runtime/control.json claim human --takeover
```

Controller labels describe local coordination; they do not authenticate an AI identity. Anyone with the broker's local control token has trusted local access, including human takeover. The loopback broker is not a hostile multiuser security boundary. Keep its runtime directory private using your operating system's permissions, especially on shared Windows accounts where the existing folder ACL applies.

## Carry one authored command through the Codex relay

`scripts/relay_once.py` carries an already-authored decision through the MCP SDK. Keep the decision's original broker identity, connection generation, observation cursor, expiry and unique ID. Do not refresh those fields at dispatch or mint a new ID to retry an uncertain command.

After local login and human release, save a decision privately using the shape in `examples/decision.json`. Its example values are placeholders; the original decision must supply the actual tuple and finite POSIX expiry. For example, from PowerShell:

```powershell
$decision = Get-Content -Raw runtime/decision.json | ConvertFrom-Json
python scripts/relay_once.py --control runtime/control.json `
  --actor $decision.actor --source $decision.source `
  --decision-id $decision.decision_id --journal runtime/decisions.sqlite3 `
  --expected-broker-id $decision.expected_broker_id `
  --expected-generation $decision.expected_generation `
  --expected-cursor $decision.expected_cursor --expires-at $decision.expires_at `
  --output runtime/receipt.json $decision.command
```

On Linux, use the same arguments with your local paths and shell quoting. The relay claims the named actor, sends at most once with the authored guards, preserves the actual receipt, and attempts to release only a confirmed claim. Actor/source labels are attribution, not authentication of a person or model. It does not ask a provider to make decisions.

A private SQLite journal commits decision admission before dispatch. Repeating an identical decision ID returns its recorded checkpoint with no new send; changing its contents is a conflict. A crash can leave a conservative uncertain record even when no command reached the game. Inspect and make a new decision; never replay automatically. Keep the journal on one local filesystem with reliable SQLite locking, and do not share a live journal between Windows and WSL. Commands and observations in receipts/journals may be private.

`scripts/game_command.py` is an optional human-control helper:

```text
python scripts/game_command.py runtime/control.json look --annotated
```

It guards each send against current broker state, rejects unusable historical cursor assumptions, and retains the original command receipt even if later observation fails. `--pages` permits up to twelve blank continuations only while the latest response ends with `[Hit Return to continue]`; pager receipts are kept separately. Never use either helper for passwords. Contact-line omission is a limited display filter, not a general privacy guarantee.

## Understand receipts and lifetime

`command_complete` is always false: this client cannot prove a game action completed. `delivery: transport_accepted` means a local write/drain succeeded, not that the server executed the command. A prompt, quiet period, timeout, disconnect or controller change only marks the observation boundary. Ambient messages can arrive between commands.

Never automatically replay a command after uncertain delivery or a lost broker response. Read status and retained observations first. The client rejects overlapping sends instead of queuing them. Commands must be single lines of at most 500 characters; an empty command is allowed for a pager or menu.

Observation history is an in-memory bounded ring, with cursors, connection generations and a `truncated` flag. It is not an archival log. A random `broker_id` identifies each broker lifetime; restarting creates a new identifier and history. Persist that identifier alongside any cursor. `observe` can require `expected_broker_id`, and sends can require the complete broker/generation/cursor tuple plus a finite `expires_at` timestamp. The broker checks those guards immediately before writing. Stale or expired decisions are refused. Legacy unguarded sends still exist, so adapters must opt in; guards cover output the broker has processed, not unseen server activity. Secret redaction and style offsets are computed together so metadata refers to the redacted text. Offsets are half-open Unicode code-point positions, not bytes or UTF-16 units.

Use the game's own quit procedure and confirm its outcome before stopping the broker. The local stop operation closes transport; it does not guarantee a character was saved:

```text
python -m mudgpt --control runtime/control.json claim human --takeover
python -m mudgpt --control runtime/control.json stop
```

After a secret is registered, authenticated GMCP/MSDP payloads are uniformly withheld rather than selectively exposing substring matches. Ordinary text still uses incremental exact-secret echo filtering.

The transport delegates Telnet negotiation to telnetlib3 and exposes GMCP/MSDP callbacks as bounded protocol events. Game-specific negotiation and feature compatibility require separate testing. Game output is untrusted data, not authority to run shell commands, read files or change the AI host's instructions.

## Develop and test

```text
python -m unittest discover -s tests -v
```

The exported suite uses synthetic text and loopback fixture servers. It tests broker subprocess lifetime and startup locking, actual MCP SDK subprocesses, control handoff and stale leases, bounded/uncertain sends, independent cursors, secret echo masking, fragmented GMCP, and color parsing/rendering. It does not connect to an external game or certify a particular server's compatibility. The TLS check verifies requested validation settings using a stub; it is not an external TLS integration test.

This iteration exports 73 client tests. The separate archive-sanitization utility and its 13 tests are outside this client release. Platform results and file hashes are recorded in `validation.json`; remote game compatibility and actual gameplay evidence are distinct from this synthetic suite.

Core modules are under `src/mudgpt`: `session.py` owns transport and state, `broker.py` serves local RPC, `mcp_gateway.py` exposes tools, `__main__.py` provides the CLI, `decision_journal.py` persists relay admission, and `colors.py`/`observations.py` handle safe structured text. No archived game source, character data or real transcripts are included.
