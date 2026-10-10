"""Load the agent's tools from its MCP servers over persistent sessions."""

from contextlib import AsyncExitStack

from langchain_core.tools import BaseTool
from langchain_mcp_adapters.client import MultiServerMCPClient
from langchain_mcp_adapters.tools import load_mcp_tools


async def open_mcp_tools(
    stack: AsyncExitStack, servers: dict[str, str]
) -> list[BaseTool]:
    """Open one MCP session per server and return the tools from all of them.

    Each session stays open until ``stack`` closes, and every call to a server's tools
    reuses it. ``MultiServerMCPClient.get_tools()`` would instead open a fresh session
    per call, which costs far more per call and splits one run across many sessions.

    Tool descriptors and results come from the servers and are untrusted; they are
    passed to the model as they are.
    """
    client = MultiServerMCPClient(
        {name: {"url": url, "transport": "http"} for name, url in servers.items()}
    )
    tools: list[BaseTool] = []
    for name in servers:
        session = await stack.enter_async_context(client.session(name))
        tools += await load_mcp_tools(session, server_name=name)
    return tools
