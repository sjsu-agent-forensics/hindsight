"""MCP tool server giving the agent the date and time, fixed per scenario.

Replaces the in-process `get_local_datetime` tool, the last one the agent ran outside MCP. It
answers with the session's scenario time rather than the wall clock, so a rerun from reset sees
the same time as the original run (N-07).
"""

from fastmcp import FastMCP
from hindsight_server_kit import SessionStore, serve

PORT = 8015

server = FastMCP("Clock Server")
# No fixture loader: the only state is the session's scenario time, which the kit owns and which
# a seed request sets with its "time" key.
store = SessionStore("clock")


# Tool names and docstrings below are published to clients as the MCP tool name and
# description, so renaming a function or editing its docstring changes the tool descriptor.
@server.tool()
def get_datetime() -> dict:
    """Get the current local date and time."""
    now = store.get().now
    return {
        "datetime": now.isoformat(),
        "date": now.date().isoformat(),
        "time": now.strftime("%H:%M:%S"),
        "weekday": now.strftime("%A"),
        "utc_offset": now.strftime("%z")[:3] + ":" + now.strftime("%z")[3:],
    }


def main() -> None:
    """Serve the tool over HTTP, with the kit's control endpoint on PORT + 100."""
    serve(server, store, port=PORT)
