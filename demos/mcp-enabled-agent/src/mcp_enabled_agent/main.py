"""Agent demo that reaches its tools over MCP.

Connects to the internal workspace servers (email, web, drive, chat, calendar) over HTTP, gives the
agent one task that spans several of them, and prints the answer followed by every tool call and
response in the run. The servers must already be running (python scripts/launch_servers.py) -- see
demos/mcp-enabled-agent/README.md.
"""

import asyncio
import logging

from hindsight.llm import get_llm
from langchain_core.messages import AIMessage, ToolMessage
from langchain_mcp_adapters.client import MultiServerMCPClient
from langchain.agents.factory import create_agent

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")

# The internal workspace servers and their MCP ports; scripts/launch_servers.py starts them.
_WORKSPACE_SERVERS = {"email": 8012, "web": 8013, "drive": 8014, "chat": 8016, "calendar": 8017}

# No Hindsight-Session header is sent yet, so every call uses each server's default state.
client = MultiServerMCPClient(
    {
        name: {"url": f"http://127.0.0.1:{port}/api/mcp", "transport": "http"}
        for name, port in _WORKSPACE_SERVERS.items()
    }
)

# A task that spans the drive and email servers: the M1 acceptance task in the internal tools
# design spec.
_TASK = (
    "Find the notes from Monday's pipeline meeting in the drive and email Jamie Park a two-line "
    "summary."
)


async def _run() -> None:
    """Run the agent once and print its answer and tool trace.

    Tool responses are printed as received from the server; they are not validated.
    """
    logger.info("Starting the agent run.")
    tools = await client.get_tools()
    agent = create_agent(get_llm(), tools)
    response = await agent.ainvoke({"messages": _TASK})
    print(response["messages"][-1].content)

    for message in response["messages"]:
        if isinstance(message, AIMessage) and message.tool_calls:
            for tool_call in message.tool_calls:
                print(f"Tool Request: {tool_call['name']}, Input: {tool_call['args']}")
        if isinstance(message, ToolMessage):
            print(f"Tool Response: {message.name}, Output: {message.text}")

    logger.info("Agent run completed.")


def main() -> None:
    """Run the demo."""
    logger.info("Starting the demo.")
    asyncio.run(_run())
