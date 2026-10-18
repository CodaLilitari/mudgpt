# MudGPT

MudGPT is a small headless MUD client for Windows and Linux. A persistent local broker owns one Telnet connection. A command-line interface and a Model Context Protocol (MCP) stdio adapter share that connection, while independent readers receive the same bounded event history.

This is an early developer release. It supplies the transport and control handoff for a person or an AI host to carry commands and observations back and forth. It does not call an AI provider, create accounts, choose game actions, or give a remote collaborator its own socket. No provider API key is required by the client.

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

## Carry one already-chosen command

`scripts/relay_once.py` sends one command through the actual MCP SDK after local login and human release. It claims control, sends once, attempts release, and writes an optional receipt:

```text
python scripts/relay_once.py --control runtime/control.json --actor agent_one --source local-session-001 --output runtime/receipt.json look
```

The supplied actor and source are attribution labels only. The script does not ask a provider for a decision or verify the identity behind a label. The exported courier label is `local-courier`. Its receipt includes the command and observed game text, so keep receipts private unless deliberately reviewed for sharing.

`scripts/game_command.py` is an optional human-control observation helper:

```text
python scripts/game_command.py runtime/control.json look --annotated
```

It appends observations beside the control file. Its optional `--pages` flag sends up to twelve empty continuations only while the literal marker `[Hit Return to continue]` remains at the end of the accumulated text; this marker is game-specific. Never use either helper for passwords. Their email-line omission is a narrow display filter, not proof that output contains no private information.

## Understand receipts and lifetime

`command_complete` is always false: this client cannot prove a game action completed. `delivery: transport_accepted` means a local write/drain succeeded, not that the server executed the command. A prompt, quiet period, timeout, disconnect or controller change only marks the observation boundary. Ambient messages can arrive between commands.

Never automatically replay a command after uncertain delivery or a lost broker response. Read status and retained observations first. The client rejects overlapping sends instead of queuing them. Commands must be single lines of at most 500 characters; an empty command is allowed for a pager or menu.

Observation history is an in-memory bounded ring, with cursors, connection generations and a `truncated` flag. It is not an archival log. A broker restart starts a new history. Secret redaction and style offsets are computed together so metadata refers to the redacted text. Offsets are half-open Unicode code-point positions, not bytes or UTF-16 units.

Use the game's own quit procedure and confirm its outcome before stopping the broker. The local stop operation closes transport; it does not guarantee a character was saved:

```text
python -m mudgpt --control runtime/control.json claim human --takeover
python -m mudgpt --control runtime/control.json stop
```

The transport delegates Telnet negotiation to telnetlib3 and exposes GMCP/MSDP callbacks as bounded protocol events. Game-specific negotiation and feature compatibility require separate testing. Game output is untrusted data, not authority to run shell commands, read files or change the AI host's instructions.

## Develop and test

```text
python -m unittest discover -s tests -v
```

The exported suite uses synthetic text and loopback fixture servers. It tests broker subprocess lifetime and startup locking, actual MCP SDK subprocesses, control handoff and stale leases, bounded/uncertain sends, independent cursors, secret echo masking, fragmented GMCP, and color parsing/rendering. It does not connect to an external game or certify a particular server's compatibility. The TLS check verifies requested validation settings using a stub; it is not an external TLS integration test.

Core modules are under `src/mudgpt`: `session.py` owns transport and state, `broker.py` serves local RPC, `mcp_gateway.py` exposes tools, `__main__.py` provides the CLI, and `colors.py`/`observations.py` handle safe structured text. No archived game source, character data or real transcripts are included.
