"""MCP tool server exposing basic arithmetic tools over stdio.

The baseline tool server for the MCP-enabled agent demo, which spawns this server as a
subprocess rather than connecting to a running one.
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
    """Serve the tools on stdin/stdout until the client closes the connection."""
    # stdio keeps the transport private to the spawning client: no port is opened, so the
    # tools are not reachable by anything else on the host.
    #
    # The banner and startup logs go to stderr, which a client spawning this server inherits,
    # so they surface in the client's terminal and bury its output. Silence both: under stdio
    # there is no human watching this process.
    server.run(transport="stdio", show_banner=False, log_level="WARNING")
