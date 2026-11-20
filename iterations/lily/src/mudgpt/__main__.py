import argparse
import json
import sys

from .broker import request, serve


def main():
    parser = argparse.ArgumentParser(description="MudGPT local session CLI; no AI provider keys needed")
    parser.add_argument("--control", help="absolute broker control-file path")
    sub = parser.add_subparsers(dest="op", required=True)
    launch = sub.add_parser("serve", help="start the persistent local broker")
    launch.add_argument("--profile", help="credential-free connection JSON")
    sub.add_parser("mcp", help="start official-SDK stdio MCP proxy to the broker")
    connect = sub.add_parser("connect")
    connect.add_argument("--host")
    connect.add_argument("--port", type=int)
    connect.add_argument("--tls", action="store_true", default=None)
    connect.add_argument("--owner", default="human")
    connect.add_argument("--lease", help="current claim token for a named controller")
    send = sub.add_parser("send")
    send.add_argument("command", nargs="?")
    send.add_argument("--stdin", action="store_true", help="read command from stdin, avoiding shell history")
    send.add_argument("--secret", action="store_true", help="redact secret from observations; use with --stdin")
    send.add_argument("--owner", default="human")
    send.add_argument("--lease", help="current claim token for a named controller")
    send.add_argument("--timeout", type=float, default=3)
    send.add_argument("--quiet", type=float, default=.3)
    send.add_argument("--expected-broker-id", help="refuse a command after a broker restart")
    send.add_argument("--expected-generation", type=int, help="refuse a command after reconnect")
    send.add_argument("--expected-cursor", type=int, help="refuse a command after new observations")
    send.add_argument("--expires-at", type=float, help="refuse at or after this finite POSIX timestamp")
    send.add_argument("--view", choices=("json", "plain", "ansi", "annotated"), default="json",
                      help="JSON preserves color spans; ansi renders colors; annotated names them")
    observe = sub.add_parser("observe")
    observe.add_argument("--after", type=int, default=0)
    observe.add_argument("--expected-broker-id", help="require the cursor's original broker lifetime")
    observe.add_argument("--view", choices=("json", "plain", "ansi", "annotated"), default="json")
    claim = sub.add_parser("claim")
    claim.add_argument("owner")
    claim.add_argument("--lease", help="existing token when renewing a named controller claim")
    claim.add_argument("--takeover", action="store_true", help="human control preemption")
    release = sub.add_parser("release")
    release.add_argument("owner")
    release.add_argument("--lease", help="current claim token for a named controller")
    sub.add_parser("status")
    for name in ("close", "stop"):
        command = sub.add_parser(name)
        command.add_argument("--owner", default="human")
        command.add_argument("--lease", help="current claim token for a named controller")
    args = parser.parse_args()
    try:
        if args.op == "serve":
            serve(args.control, args.profile)
            return
        if args.op == "mcp":
            from .mcp_gateway import run
            run(args.control)
            return
        payload = {key: value for key, value in vars(args).items()
                   if key not in ("control", "stdin", "view") and value is not None}
        if args.op == "send":
            if args.stdin:
                if args.command is not None:
                    raise ValueError("use either command argument or --stdin")
                payload["command"] = sys.stdin.read().removesuffix("\n").removesuffix("\r")
            elif args.secret:
                raise ValueError("secret commands require --stdin")
            if "command" not in payload:
                raise ValueError("provide a command or --stdin")
        result = request(payload, args.control)
        if getattr(args, "view", "json") == "json":
            print(json.dumps(result, ensure_ascii=True, indent=2))
        else:
            from .observations import join_text_events, render
            print(render(join_text_events(result.get("events", [])), args.view), end="")
            print(json.dumps({k: result[k] for k in
                              ("broker_id", "cursor", "generation", "truncated", "reason", "delivery", "command_complete")
                              if k in result}), file=sys.stderr)
    except (OSError, ValueError, RuntimeError) as exc:
        print(json.dumps({"error": str(exc)}), file=sys.stderr)
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()

