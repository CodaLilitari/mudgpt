"""Carry one already-authorized collaborator command through the real MCP adapter.

Use after local login and human release. This script does not ask an AI provider
for decisions, and does not prove the identity behind the supplied attribution.
"""
import argparse
import asyncio
import json
import sys
from pathlib import Path
from datetime import datetime, timezone
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from mudgpt.observations import join_text_events, omit_contact_lines


def data(result):
    if result.is_error:
        raise RuntimeError(" ".join(c.text for c in result.content if c.type == "text"))
    if result.structured_content is not None:
        return result.structured_content
    return json.loads("".join(c.text for c in result.content if c.type == "text"))


async def main(args):
    control = str(Path(args.control).resolve())
    params = StdioServerParameters(command=sys.executable,
        args=["-m", "mudgpt", "--control", control, "mcp"])
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write, read_timeout_seconds=20) as client:
            await client.initialize()
            claim = data(await client.call_tool("mud_claim", {"owner": args.actor}))
            release_error = None
            try:
                observation = data(await client.call_tool("mud_send", {
                    "owner": args.actor, "command": args.command, "timeout": 4}))
            finally:
                try:
                    data(await client.call_tool("mud_release", {"owner": args.actor}))
                except Exception as error:
                    # A later takeover must not erase an already-returned send receipt.
                    release_error = str(error)
    # Contact fields are not relevant to a game decision or shared receipt.
    styled = omit_contact_lines(join_text_events(observation.get("events", [])))
    observation.update(styled)
    observation["style_offset_unit"] = "Unicode code points in observation.text; end exclusive"
    # Preserve sequence metadata without duplicating unsanitized split text chunks.
    observation["events"] = [{k: v for k, v in event.items() if k not in ("text", "style_spans")}
        for event in observation.get("events", [])]
    receipt = {"at": datetime.now(timezone.utc).isoformat(), "actor": args.actor,
        "courier": "local-courier", "source_conversation": args.source,
        "command": args.command, "lease_epoch": claim["lease_epoch"],
        "observation": observation, "release_error": release_error,
        "provenance": "Command attributed by the local courier; no remote socket access or identity proof implied."}
    if args.output:
        target = Path(args.output)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(receipt, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps(receipt, indent=2, ensure_ascii=True))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--control", required=True)
    parser.add_argument("--actor", required=True)
    parser.add_argument("--source", required=True, help="saved conversation reference for attribution")
    parser.add_argument("--output")
    parser.add_argument("command")
    asyncio.run(main(parser.parse_args()))
