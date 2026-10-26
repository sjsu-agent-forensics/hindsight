"""Test MCP server over stdio, for checking how the interposer handles failing servers.

Not for the agent: its tools exist so a check script can make a call hang (``slow``) and
find the server's process to kill it (``pid``).
"""

import os
import time

from fastmcp import FastMCP

server = FastMCP("Faults Server")


# Tool names and docstrings below are published to clients as the MCP tool name and
# description, so renaming a function or editing its docstring changes the tool descriptor.
@server.tool()
def pid() -> int:
    """Return this server's process ID."""
    return os.getpid()


@server.tool()
def slow(seconds: float) -> str:
    """Wait the given number of seconds, then answer."""
    # Blocking on purpose: the server cannot answer anything else meanwhile, like a hung
    # server.
    time.sleep(seconds)
    return f"waited {seconds} s"


def main() -> None:
    """Serve the tools on stdin/stdout until the client closes the connection."""
    server.run(transport="stdio", show_banner=False, log_level="WARNING")
