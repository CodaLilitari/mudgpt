"""MUDweave's optional update-aware launcher; never controls a running session.

Run with the chosen iteration's Python. Child stdin/stdout/stderr, environment,
working directory and arguments are inherited. Update notices use stderr only.
"""
import subprocess
import sys

import mudweave_update as updates


HELP = """MUDweave launcher (Python 3.11+)

  python mudweave.py [--no-update-check] broker <mudgpt arguments>
  python mudweave.py [--no-update-check] run -- <Python arguments>
  python mudweave.py update <check|download|status|config> [options]

broker runs this Python's -m mudgpt; run passes arguments to this Python.
Use your selected iteration's environment. Checks notify only; they never
install, restart, take control, connect to a game, or replay a command.
Existing -m mudgpt and authored courier launchers remain unchanged.
"""


def main(argv=None):
    args = list(sys.argv[1:] if argv is None else argv)
    enabled = True
    if args[:1] == ["--no-update-check"]:
        enabled = False
        args.pop(0)
    if not args or args[0] in ("-h", "--help", "help"):
        print(HELP)
        return 0
    action = args.pop(0)
    if action == "update":
        return updates.main(argv=args)
    if action == "broker":
        child_args = ["-m", "mudgpt", *args]
        # Short commands must not start network work on every game turn.
        operation_args = list(args)
        if operation_args[:1] == ["--control"]:
            operation_args = operation_args[2:]
        elif operation_args and operation_args[0].startswith("--control="):
            operation_args = operation_args[1:]
        enabled = enabled and bool(operation_args and operation_args[0] in ("serve", "mcp"))
    elif action == "run":
        if args[:1] == ["--"]:
            args.pop(0)
        if not args:
            print("run requires Python arguments after --", file=sys.stderr)
            return 2
        child_args = args
    else:
        print("Unknown action; use --help", file=sys.stderr)
        return 2
    try:
        child = subprocess.Popen([sys.executable, *child_args])
    except OSError:
        print("Could not start the selected Python client", file=sys.stderr)
        return 1
    # Startup must not depend on network/cache availability. The helper is
    # deliberately outside all broker/MCP/courier protocol paths.
    if enabled:
        try:
            updates.start_check()
        except Exception:
            pass
    while True:
        try:
            result = child.wait()
            return result if result >= 0 else 128 - result
        except KeyboardInterrupt:
            # Terminal interrupts reach the child too. Let its own established
            # shutdown path finish; do not terminate/kill or send game commands.
            continue


if __name__ == "__main__":
    raise SystemExit(main())
