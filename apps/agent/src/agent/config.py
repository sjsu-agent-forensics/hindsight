"""MCP server configuration for the agent.

The agent knows each tool server only by a name and a streamable HTTP URL on the
interposer:

```toml
[servers.weather]
url = "http://127.0.0.1:8000/mcp/weather"
```
"""

import tomllib
from pathlib import Path


def load_servers(path: Path) -> dict[str, str]:
    """Read the MCP servers from a TOML config file, mapping each name to its URL.

    Raises:
        ValueError: The file has no servers, or a server has no ``url``.
    """
    with path.open("rb") as file:
        servers = tomllib.load(file).get("servers", {})
    if not servers:
        raise ValueError(f"{path} defines no [servers.<name>] tables")
    missing = [name for name, entry in servers.items() if "url" not in entry]
    if missing:
        raise ValueError(f"{path}: servers {missing} have no 'url'")
    return {name: entry["url"] for name, entry in servers.items()}
