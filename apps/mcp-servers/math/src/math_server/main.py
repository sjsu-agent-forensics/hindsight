"""MCP tool server exposing basic arithmetic tools over streamable HTTP.

The baseline tool server for the MCP-enabled agent demo.
"""

from fastmcp import FastMCP

server = FastMCP("Math Server")


# Tool names and docstrings below are published to clients as the MCP tool name and
# description, so renaming a function or editing its docstring changes the tool descriptor.
@server.tool()
def add(a: int, b: int) -> int:
    """Add two numbers."""
    return a + b


@server.tool()
def multiply(a: int, b: int) -> int:
    """Multiply two numbers."""
    return a * b


def main() -> None:
    """Serve the tools until interrupted."""
    # fastmcp's default host, port and path: http://127.0.0.1:8000/mcp, which
    # mcp-enabled-agent connects to.
    server.run(transport="http")
