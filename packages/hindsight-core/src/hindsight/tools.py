"""Local LangChain tools that run inside the agent process rather than over MCP."""

from datetime import datetime

from langchain_core.tools import tool


@tool(description="Get the current server-local date and time")
def get_local_datetime() -> str:
    """Return the local date and time as "YYYY-MM-DD HH:MM:SS"."""
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")
