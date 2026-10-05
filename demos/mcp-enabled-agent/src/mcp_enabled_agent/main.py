"""Agent demo that reaches its tools over MCP.

Connects to the math server over stdio and the weather server over HTTP, asks one
question covering both, and prints the answer followed by every tool call and response
in the run. The math server is spawned automatically; the weather server listens on a
port and must already be running -- see demos/mcp-enabled-agent/README.md.
"""

import asyncio
import logging
from pathlib import Path

from hindsight.llm import get_llm
from langchain_core.messages import AIMessage, ToolMessage
from langchain_mcp_adapters.client import MultiServerMCPClient
from langchain.agents.factory import create_agent

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")

# The math server needs mcp 2.x (fastmcp 4) while langchain-mcp-adapters pins mcp 1.x, so it is
# excluded from the workspace and installed in its own .venv -- see "The MCP server exception" in
# the root README.md. Launching its console script directly is what keeps the two sets of
# dependencies apart; this demo's own interpreter has no fastmcp and cannot be given one.
_MATH_SERVER = (
    Path(__file__).resolve().parents[4] / "apps/mcp-servers/math/.venv/bin/math-server"
)

client = MultiServerMCPClient(
    {
        "math": {
            "command": str(_MATH_SERVER),
            "args": [],
            "transport": "stdio",
        },
        # No "args" here: it is a stdio-only key. The http session factory rejects any extra
        # kwarg, and the resulting TypeError is raised while the math session is still open,
        # which hangs the process instead of surfacing a traceback.
        "weather": {
            "url": "http://127.0.0.1:8001/api/mcp",
            "transport": "http",
        },
    }
)


async def _run() -> None:
    """Run the agent once and print its answer and tool trace.

    Tool responses are printed as received from the server; they are not validated.
    """
    logger.info("Starting the agent run.")
    tools = await client.get_tools()
    agent = create_agent(get_llm(), tools)
    math_response = await agent.ainvoke(
        {"messages": "What's (3 + 5) x 12? What's the weather in San Jose?"}
    )
    print(math_response["messages"][-1].content)

    for message in math_response["messages"]:
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
