"""Attack test server: an email outbox, the sink attacks try to reach.

For the end-to-end scenarios only. Nothing is sent anywhere: a call is acknowledged and
forgotten, and the proxy trace is the record of what the agent tried to send.
"""

from fastmcp import FastMCP

server = FastMCP("Scenario Mail")


# Tool names and docstrings below are published to clients as the MCP tool name and
# description, so renaming a function or editing its docstring changes the tool descriptor.
@server.tool()
def send_email(to: str, subject: str, body: str) -> str:
    """Send an email."""
    return f"Email sent to {to}."


def main() -> None:
    """Serve the tools on stdin/stdout until the client closes the connection."""
    server.run(transport="stdio", show_banner=False, log_level="WARNING")
