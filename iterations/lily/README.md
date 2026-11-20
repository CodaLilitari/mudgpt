# MUDweave — Lily actor iteration 0.1.0

Lily has a separately packaged, configurable local actor interface with its own
MudGPT core 0.3.0. It supports explicit claim, status, observation, command,
conditional retreat and release operations. The actor keeps its controller lease
in private local state and durably records authored decisions before dispatch.
Repeated decision IDs return their saved result without replaying a command.

This collection 0.4.0 package is independent of the Codex, Claude and Muse
iterations. Sharing a protocol does not share their control files, environments,
journals, credentials or character sessions. The actor is a callable program;
it does not wake a ChatGPT conversation, schedule model turns, establish a remote
network route or reconnect a character automatically. A model needs an authorized
local tool executor to invoke it. Muse's recipient-side transport remains separate.

## Install in a separate environment

Use Python 3.11 or newer. Create a virtual environment outside all Git checkouts,
then use that environment's Python to install this directory. For example, on
Linux:

```sh
python3 -m venv "$HOME/.local/share/mudweave/lily-env"
"$HOME/.local/share/mudweave/lily-env/bin/python" -m pip install /path/to/iterations/lily
```

On Windows PowerShell:

```powershell
py -3 -m venv "$env:LOCALAPPDATA\MUDweave\lily-env"
& "$env:LOCALAPPDATA\MUDweave\lily-env\Scripts\python.exe" -m pip install 'C:\path\to\iterations\lily'
```

Use the selected environment's interpreter for every command below. The included
`uv.lock` pins the same dependencies as the maintained core; if using uv, set
`UV_PROJECT_ENVIRONMENT` to an external directory before `uv sync --frozen`.
Do not install another iteration's editable package into this environment.

## Prepare and play

Copy `examples/actor.json` and `examples/profile.json` to a private runtime
directory outside the checkout. Set the operator-selected endpoint in the profile
and the actor's own broker control path in the actor config. Config paths resolve
relative to that config file. `state_dir` is required to resolve outside every Git
checkout; each actor/character needs a separate directory and broker.

The local operator starts the broker, connects and completes protected login using
the ordinary `mudgpt` CLI. Password entry uses `send --secret --stdin`; never put
credentials in an actor config, a command argument, an observation journal or a
shared packet. After verifying the intended character, the operator releases the
human controller. See [the explicit setup and decision protocol](docs/actor-protocol.md)
and [the machine-readable workflow](setup.workflow.json).

```sh
python -m mudgpt.actor_client --config /path/to/private/actor.json --view json claim
python -m mudgpt.actor_client --config /path/to/private/actor.json --view json observe
```

Select one command from the returned observation. Copy its exact `next_decision`
fields and choose a new stable decision ID; never refresh a stale decision's
guards to make it pass. Complete command and retreat examples are in the protocol.
Use `release` to return confirmed control. Release, game logout, server shutdown
and stopping a broker are distinct actions.

The default LoK profile adds evidence-backed room, exit, prompt and resource facts
to the original text and color spans. It leaves unsupported facts unknown and
does not certify that an earlier displayed room is still current. The fixed
`lok_retreat_v1` action permits at most one guarded write attempt for `flee` only after the broker verifies the
explicit combat condition and original decision lifetime. It does not add an
autonomous combat loop. Review [LoK and collaboration conventions](docs/lok-and-collaboration.md)
alongside the running game's current rules.

## Versions, updates and evidence

`python mudweave.py versions --json` reads the adjacent public runtime manifest:
collection 0.4.0, core 0.3.0, actor adapter 0.1.0 and updater 0.4.0. It does not
inspect or replace a running game. The update-aware launcher checks and notifies;
download and side-by-side installation require an explicit operator request.
See [the 0.4.0 update guide](UPDATES_040.md). Use the actor module directly for
individual turns so update checks do not become part of the command path.

Synthetic tests are included in `tests/`; use an external temporary/output
directory and run the suites serially. The release's validation receipt records
the actual executed scope. This README does not claim this package has been
installed in a model sandbox or deployed to a live character session.

The package derives from the maintained Codex core and Coda's configurable actor
work. Human and AI contributions remain credited in `NOTICE` and `CREDITS.md` under
the unmodified Apache-2.0 license. No game-server source, pfiles, roleplay logs,
private keys, real control files or deployment credentials belong in this package.
