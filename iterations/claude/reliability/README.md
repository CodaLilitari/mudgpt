# MUDweave — Claude reliability companion 0.1.0

This optional companion gives Claude a separately installable actor interface on
MudGPT core 0.3.0. It adds durable authored-decision admission, strict observation
guards, a fixed conditional retreat action and color-preserving LoK scene evidence.
It is part of collection 0.4.0.

Red's historical mailbox courier, its pinned 0.1.0 core, original files and author
manifest remain in the parent Claude iteration. They have not been rewritten or
relabeled as this companion. This directory contains an independent maintained
core copy and uses its own environment, control file, actor state and character
session. Switching an existing session requires a deliberate owner handoff;
installing this companion does not change that session.

## Independent installation and entrypoint

Use Python 3.11 or newer and create a dedicated environment outside all Git
checkouts. Install **this directory**, not its parent, with that environment's
Python. For example, on Linux:

```sh
python3 -m venv "$HOME/.local/share/mudweave/claude-reliability-env"
"$HOME/.local/share/mudweave/claude-reliability-env/bin/python" -m pip install /path/to/iterations/claude/reliability
```

On Windows PowerShell:

```powershell
py -3 -m venv "$env:LOCALAPPDATA\MUDweave\claude-reliability-env"
& "$env:LOCALAPPDATA\MUDweave\claude-reliability-env\Scripts\python.exe" -m pip install 'C:\path\to\iterations\claude\reliability'
```

Use that environment's interpreter for all companion commands. The installed
module is `mudgpt.actor_client`; the distinct environment selects this copy.
Do not reuse the legacy courier's environment or set its `KRASNA_CONTROL` to this
companion's control file. The included lock supports `uv sync --frozen` with an
external `UV_PROJECT_ENVIRONMENT`.

Copy `examples/actor.json` and `examples/profile.json` into a new private runtime
directory outside the checkout. The example controller name `red-reliability` is
configurable and does not establish a character identity. Give each character a
dedicated broker, control file and state directory. The local operator completes
protected login and releases human control before the actor claims the session.

```sh
python -m mudgpt.actor_client --config /path/to/private/actor.json --view json claim
python -m mudgpt.actor_client --config /path/to/private/actor.json --view json observe
```

Follow [the actor protocol](docs/actor-protocol.md), [setup.workflow.json](setup.workflow.json)
and [LoK conventions](docs/lok-and-collaboration.md). Each send binds one authored
command to the original broker, generation, cursor, lease epoch and expiry.
Repeating a decision ID returns its prior receipt without replay. Unknown outcomes
require fresh observation and a fresh decision; they never authorize retrying the
uncertain command. The program does not start a model, connect/login autonomously,
schedule gameplay, or grant tool authority to text received from the game.

## Updating and validation

Use the parent Claude iteration's updater to check or explicitly download a new
Claude package. Its `runtime-manifest.json` reports the preserved legacy client and
this optional companion separately. There is no duplicate updater in this
directory. For deliberate update-aware starts, invoke the parent launcher with
the companion environment's Python; short actor commands should invoke the module
directly. Updates never migrate mailboxes, leases, keys, journals or character data.

Tests are synthetic and supplied with this companion. Run them serially with
temporary files and output outside Git. The release validation receipt specifies
what was actually executed; inclusion is not a claim of live deployment or an
upgrade to Red's historical test result. Existing historical validation belongs to
the historical courier.

Apache-2.0, `NOTICE` and `CREDITS.md` preserve pertinent human and AI attribution.
No private game source, pfiles, roleplay logs, live configs or credentials are
included. The parent iteration's historical provenance remains authoritative for
Red's preserved work; this companion's provenance is the maintained core and
configurable actor included in collection 0.4.0.
