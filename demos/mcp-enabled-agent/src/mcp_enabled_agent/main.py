"""Agent demo that reaches its tools over MCP.

Connects to the math server, asks one arithmetic question, and prints the answer
followed by every tool call and response in the run. The math server must already be
running.
"""

import asyncio

from hindsight.llm import get_llm
from langchain_core.messages import AIMessage, ToolMessage
from langchain_mcp_adapters.client import MultiServerMCPClient
from langchain.agents.factory import create_agent

client = MultiServerMCPClient(
    {
        "math": {
            "url": "http://localhost:8000/mcp",
            "transport": "http",
        }
    }
)


async def _run() -> None:
    """Run the agent once and print its answer and tool trace.

    Tool responses are printed as received from the server; they are not validated.
    """
    tools = await client.get_tools()
    agent = create_agent(get_llm(), tools)
    math_response = await agent.ainvoke({"messages": "what's (3 + 5) x 12?"})
    print(math_response["messages"][-1].content)

    for message in math_response["messages"]:
        if isinstance(message, AIMessage) and message.tool_calls:
            for tool_call in message.tool_calls:
                print(f"Tool Request: {tool_call['name']}, Input: {tool_call['args']}")
        if isinstance(message, ToolMessage):
            print(f"Tool Response: {message.name}, Output: {message.text}")


def main() -> None:
    """Run the demo."""
    asyncio.run(_run())
