# Claude reliability companion: local actor protocol

This setup is for a named, saved conversation that already has a verified tool
capable of running commands on the game host. It lets that conversation choose an
action, call the local actor client and read the real result directly. It does not
add tools to an ordinary browser chat, create provider credentials, wake a model
or install a scheduled player.

The primary entrypoint is:

```text
python -m mudgpt.actor_client --config /absolute/path/to/actor.json OPERATION
```

Use the selected Claude reliability companion's own Python environment. Do not reuse another
player's broker, control file or actor state. The local operator handles game
connection and protected login; the actor interface cannot connect, authenticate,
take over a human controller, or choose another server.

## 1. Install outside working source checkouts

Unpack the verified release into a normal external release directory with no Git
ancestor or junction back into a source checkout. Create the environment and
private state separately. These Windows paths are examples, not existing folders:

```powershell
python -m venv C:\MUDweave-Tools\claude-companion-venv
& 'C:\MUDweave-Tools\claude-companion-venv\Scripts\python.exe' -m pip install 'C:\MUDweave-Releases\0.4.0\iterations\claude\reliability'
```

Python 3.11 or newer is required. On Linux, use the same package and module
commands with an external virtual environment's `bin/python`. Keep an actor's
SQLite journal on one local filesystem with functioning locks; do not share a
live database between Windows and WSL processes.

Protect private directories with the operating system's account permissions.
The code requests owner-only POSIX modes; Windows ACLs need an explicit operator
check. A local actor with access to the host is a trusted collaborator, not a
sandbox-isolation boundary.

## 2. Create separate configuration files

Example private `C:/MUDweave-State/claude-state/actor.json`:

```json
{
  "actor": "claude_player",
  "control": "broker/control.json",
  "state_dir": "actor-state",
  "profile": "lok",
  "max_ttl_seconds": 120
}
```

This is the actor configuration's complete field set; it has no schema-version
field and contains no password or bearer/lease token. Relative paths resolve from
the configuration file's directory. `actor` is a non-human simple name. `profile`
is `lok` for evidence-only LoK text interpretation or `none` for raw observations.
The finite TTL range is 1–300 seconds. It limits a decision's lifetime; it does not
make stale observations valid.

The operator's separate game profile might be
`C:/MUDweave-State/claude-state/game-profile.json`:

```json
{
  "host": "127.0.0.1",
  "port": 4000,
  "tls": false
}
```

That is an **illustrative local endpoint**; this example supplies or starts no
game server. Replace it with the intended operator-authorized endpoint and actual
TLS requirements. Do not put login
credentials in either configuration. The broker generates its private control
file; do not hand-author a token or copy another actor's control file.

The actor writes these private files under `state_dir`:

- `actor-state.json`: confirmed lease and lifetime/checkpoint state; never share.
- `decisions.sqlite3`: durable admissions and receipts; preserve across restarts.
- `observations.jsonl`: private observations, not a public transcript.
- `actor.lock`: one actor invocation at a time.

The state directory must be outside every Git checkout. If `state_dir` is omitted,
the client chooses account-local state keyed to actor and control path. Keep that
binding stable instead of creating a fresh directory after an uncertain action.

## 3. Operator starts one broker and completes login

In a long-running operator terminal:

```powershell
& 'C:\MUDweave-Tools\claude-companion-venv\Scripts\python.exe' -m mudgpt --control 'C:\MUDweave-State\claude-state\broker\control.json' serve --profile 'C:\MUDweave-State\claude-state\game-profile.json'
```

In a second operator terminal, connect using the same control file:

```powershell
& 'C:\MUDweave-Tools\claude-companion-venv\Scripts\python.exe' -m mudgpt --control 'C:\MUDweave-State\claude-state\broker\control.json' connect
```

Follow the real login/menu prompts. Enter the password only through the protected local
`send --stdin --secret` path, never as an argument, in an actor request, or in
conversation text. Confirm the intended character and inspect any unexpected
creation prompt; it is not proof an existing character has been restored.

After login, the operator releases their controller:

```powershell
& 'C:\MUDweave-Tools\claude-companion-venv\Scripts\python.exe' -m mudgpt --control 'C:\MUDweave-State\claude-state\broker\control.json' release human
```

If a different controller owns the session, coordinate first. Do not log the same
character in twice or use human takeover merely to clear an inconvenient refusal.

## 4. The conversation claims and observes directly

Through its verified host-execution tool, the conversation runs one operation at
a time. Global options such as `--view` precede the operation:

```powershell
$claudePython = 'C:\MUDweave-Tools\claude-companion-venv\Scripts\python.exe'
$claudeConfig = 'C:\MUDweave-State\claude-state\actor.json'
& $claudePython -m mudgpt.actor_client --config $claudeConfig --view json status
& $claudePython -m mudgpt.actor_client --config $claudeConfig --view json claim
& $claudePython -m mudgpt.actor_client --config $claudeConfig --view json observe
```

Some tool shells are short-lived: repeat the two path assignments in a new shell
or use literal paths. The actual lease persists privately in actor state; it is
not a conversation variable and is not printed in ordinary results.

`observe` defaults to the actor's retained cursor. Explicit history reads use
`observe --after N --broker ORIGINAL_BROKER_ID`. A returned `next_decision` object
contains the exact broker, generation, cursor, lease epoch and finite expiry for
a possible next action. Missing or truncated evidence is not a valid decision
tuple. Inspect the room, exits, occupants, resources, effects, input mode and
uncertain earlier outcomes before choosing an action.

JSON preserves `text`, `style_spans`, receipts and any scene interpretation.
The default annotated view prints color-tagged text and an `ACTOR_RECEIPT` JSON
line. Color offsets use Unicode code points and exclusive ends. Scene fields
have evidence and uncertainty; an older room description is not automatically
the character's present position.

## 5. Author one guarded command

The following is a command **template**, not a runnable packet. Substitute the
original values returned in the observation used to make the decision:

```text
python -m mudgpt.actor_client --config ACTOR_CONFIG --view json send "look" --decision-id UNIQUE_DECISION_ID --broker ORIGINAL_BROKER_ID --generation ORIGINAL_GENERATION --cursor ORIGINAL_CURSOR --lease-epoch ORIGINAL_LEASE_EPOCH --expires-at ORIGINAL_FINITE_UNIX_EXPIRY
```

Use a stable decision ID for the authored packet. A reused identical ID returns
its saved checkpoint without another send; changed content under that ID is a
conflict. Do not renew the timestamp, replace the cursor, delete the journal or
invent a new ID merely to replay an uncertain command. A stale refusal calls for
fresh observation and a genuinely new decision.

The command must be one non-secret line, at most 500 characters, without control
characters or line separators. `send --stdin` is available instead of the
positional command. It is **not a secret/password mode**. A blank input sends a
real Return, which can advance a pager. WAIT means do not invoke send at all.

Read the full receipt and returned events after every action. A transport receipt
does not certify the game action completed. Exit code 2 also covers a known
not-sent refusal; interpret the structured result rather than assuming every
nonzero exit is a lost or executed command.

## 6. Optional explicit conditional retreat

Only on a broker advertising `conditional_actions_v1`, a separately authored
retreat can use this template:

```text
python -m mudgpt.actor_client --config ACTOR_CONFIG --view json retreat --decision-id UNIQUE_DECISION_ID --broker ORIGINAL_BROKER_ID --generation ORIGINAL_GENERATION --cursor ORIGINAL_CURSOR --lease-epoch ORIGINAL_LEASE_EPOCH --expires-at ORIGINAL_FINITE_UNIX_EXPIRY --enemy-status "EXACT OBSERVED ENEMY-STATUS LINE"
```

`--allow-afk-combat` is an additional explicit opt-in, not a default. It requires
recognized same-enemy combat evidence beside the AFK prompt; AFK by itself does
not establish a fight. The broker checks the condition locally and permits at most one guarded write
attempt for the hardcoded `flee`, preserving the original identity, generation,
lease and deadline. It can refuse or fail to escape. No automatic flee threshold or attack
loop is installed. See [the reliability contract](RELIABILITY_040.md).

## 7. End or hand off deliberately

Choose in-game save/logout actions from fresh evidence, return their receipts,
and verify the actual game response. Releasing the actor lease is separate:

```text
python -m mudgpt.actor_client --config ACTOR_CONFIG --view json release
```

Release does not save, quit, close the game connection or stop the broker. An
operator may leave authorized services available, but that does not keep the
model awake. Preserve unknown outcomes and journals; a new session begins with
fresh ownership, lifetime and character observations.

## Machine-readable operation map

This map summarizes the actual CLI; it does not execute commands:

```json
{
  "entrypoint": ["python", "-m", "mudgpt.actor_client"],
  "global_options": ["--config", "--view"],
  "operations": {
    "status": [],
    "claim": [],
    "observe": ["--after", "--broker"],
    "send": ["command_or_stdin", "--decision-id", "--broker", "--generation", "--cursor", "--lease-epoch", "--expires-at"],
    "retreat": ["--decision-id", "--broker", "--generation", "--cursor", "--lease-epoch", "--expires-at", "--enemy-status", "optional:--allow-afk-combat"],
    "release": []
  },
  "operator_only": ["connect", "login", "secret_entry", "human_takeover", "server_selection"],
  "automatic_model_wakeup": false,
  "automatic_reconnect": false,
  "unknown_outcome_policy": "observe_and_reconcile_never_blind_replay"
}
```

Synthetic validation of this package is not live-game proof. It does not verify
the receiving conversation's host tools, provider limits, persistent availability
or the current character's state. Confirm those in the actual deployment.

## Decision history retention

Keep each maintained decision database together with its `.initialized` marker. An initialized database that is missing, empty or has an invalid schema blocks further execution. Archive and restore the pair without pruning consumed IDs. Losing both files or rolling both back is not detectable: never reset private state to retry an uncertain decision. Private observation logs and durable decision history have different retention requirements. A later refusal describes only that submission; it cannot prove that an earlier uncertain attempt was never sent.
