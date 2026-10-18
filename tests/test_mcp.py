"""Real MCP 2.3.0 stdio client/server subprocess tests against fake MUD only."""
import asyncio
from contextlib import AsyncExitStack
import json
import sys
import unittest

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from test_broker import BrokerHarness, WORKSPACE


def result_data(result):
    if result.structured_content is not None:
        return result.structured_content
    return json.loads("".join(item.text for item in result.content if item.type == "text"))


class McpIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.h = BrokerHarness()
        self.addCleanup(self.h.close)

    def params(self):
        return StdioServerParameters(
            command=sys.executable,
            args=["-m", "mudgpt", "--control", str(self.h.control), "mcp"],
            env=self.h.env, cwd=WORKSPACE,
        )

    def test_actual_sdk_initialization_tools_and_shared_broker_observation(self):
        self.h.connect()
        self.h.call(op="release", owner="human")
        async def scenario():
            async with stdio_client(self.params()) as (read, write):
                async with ClientSession(read, write, read_timeout_seconds=8) as client:
                    init = await client.initialize()
                    self.assertEqual(init.server_info.name, "MudGPT")
                    listed = await client.list_tools()
                    names = {tool.name for tool in listed.tools}
                    self.assertEqual(names, {"mud_status", "mud_observe", "mud_claim", "mud_release", "mud_send"})
                    # Game target and secrets must not be exposed by tool inputs.
                    for tool in listed.tools:
                        props = tool.input_schema.get("properties", {})
                        self.assertNotIn("password", props)
                        self.assertNotIn("secret", props)
                        self.assertNotIn("host", props)
                    claimed = await client.call_tool("mud_claim", {"owner": "agent_one"})
                    self.assertFalse(claimed.is_error)
                    sent = await client.call_tool("mud_send", {"command": "look", "owner": "agent_one", "timeout": 1})
                    self.assertFalse(sent.is_error)
                    self.assertFalse(result_data(sent)["command_complete"])
                    observed = await client.call_tool("mud_observe", {"after": 0})
                    first = result_data(observed)
                    self.assertIn("Fixture response: look", json.dumps(first))
                    self.assertEqual(first, self.h.call(op="observe", after=0))
            # The MCP adapter exiting must not terminate the persistent game.
            self.assertTrue(self.h.call(op="status")["connected"])
            async with stdio_client(self.params()) as (read, write):
                async with ClientSession(read, write, read_timeout_seconds=8) as second:
                    await second.initialize()
                    again = await second.call_tool("mud_observe", {"after": 0})
                    self.assertEqual(result_data(again), first)
                    blocked = await second.call_tool("mud_claim", {"owner": "other"})
                    self.assertTrue(blocked.is_error)
                    self.assertIn("controller", " ".join(c.text for c in blocked.content if c.type == "text").lower())
                    blocked_send = await second.call_tool("mud_send", {"owner": "other", "command": "north"})
                    self.assertTrue(blocked_send.is_error)
                    self.assertIn("control", " ".join(c.text for c in blocked_send.content if c.type == "text").lower())
            self.assertEqual(self.h.fake.commands, ["look"])
        asyncio.run(asyncio.wait_for(scenario(), timeout=20))

    def test_same_name_stale_adapter_cannot_reclaim_or_write_after_handoff(self):
        self.h.connect()
        self.h.call(op="release", owner="human")

        async def scenario():
            async with AsyncExitStack() as stack:
                clients = []
                for _ in range(2):
                    read, write = await stack.enter_async_context(stdio_client(self.params()))
                    client = await stack.enter_async_context(
                        ClientSession(read, write, read_timeout_seconds=8))
                    await client.initialize()
                    clients.append(client)
                first, second = clients
                claimed = await first.call_tool("mud_claim", {"owner": "agent_one"})
                self.assertFalse(claimed.is_error)
                self.assertNotIn("lease", result_data(claimed))
                self.assertNotIn("lease", result_data(await first.call_tool("mud_status", {})))

                for name, args in [
                    ("mud_claim", {"owner": "agent_one"}),
                    ("mud_send", {"owner": "agent_one", "command": "north"}),
                    ("mud_claim", {"owner": "human"}),
                ]:
                    self.assertTrue((await second.call_tool(name, args)).is_error)

                # The local human replaces ownership, then grants it to a different
                # adapter with the same actor label. The old capability must stay stale.
                self.h.call(op="claim", owner="human", takeover=True)
                self.h.call(op="release", owner="human")
                reacquired = await second.call_tool("mud_claim", {"owner": "agent_one"})
                self.assertFalse(reacquired.is_error)
                self.assertNotIn("lease", result_data(reacquired))
                for name, args in [
                    ("mud_send", {"owner": "agent_one", "command": "south"}),
                    ("mud_claim", {"owner": "agent_one"}),
                    ("mud_release", {"owner": "agent_one"}),
                ]:
                    result = await first.call_tool(name, args)
                    self.assertTrue(result.is_error)
                    self.assertIn("lease", " ".join(
                        item.text for item in result.content if item.type == "text").lower())

                sent = await second.call_tool("mud_send", {
                    "owner": "agent_one", "command": "look", "timeout": 1})
                self.assertFalse(sent.is_error)
                self.assertEqual(self.h.fake.commands, ["look"])
                # Losing control must not cut off read-only observation.
                observed = await first.call_tool("mud_observe", {"after": 0})
                self.assertFalse(observed.is_error)
                self.assertIn("Fixture response: look", json.dumps(result_data(observed)))

        asyncio.run(asyncio.wait_for(scenario(), timeout=30))

