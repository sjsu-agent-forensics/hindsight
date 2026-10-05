"""MCP tool server exposing weather tools over streamable HTTP.

A tool server for the MCP-enabled agent demo. Unlike math-server, this one listens on a
port, so it must be started before the demo rather than being spawned by it.
"""

from fastmcp import FastMCP

server = FastMCP("Weather Server")


# Tool names and docstrings below are published to clients as the MCP tool name and
# description, so renaming a function or editing its docstring changes the tool descriptor.
@server.tool()
def weather_san_jose() -> dict:
    """Get the weather in San Jose."""
    return {"temperature_celsius": 25, "condition": "Sunny"}


def main() -> None:
    """Serve the tools over HTTP."""
    server.run(
        transport="http",
        host="0.0.0.0",
        port=8001,
        path="/api/mcp",  # Your custom path here
    )
