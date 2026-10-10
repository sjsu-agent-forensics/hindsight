"""Static upstream server configuration.

The gateway fronts a fixed set of upstream MCP servers, read once at startup from a TOML
file. Each server is either a remote one reached over streamable HTTP or a local one the
gateway will spawn over stdio:

```toml
[servers.weather]
url = "http://127.0.0.1:8001/api/mcp"

[servers.math]
command = "path/to/math-server"
args = []
```
"""

import re
import tomllib
from dataclasses import dataclass, field
from pathlib import Path


@dataclass(frozen=True)
class HttpUpstream:
    """A remote MCP server reached over streamable HTTP."""

    name: str
    url: str


@dataclass(frozen=True)
class StdioUpstream:
    """A local MCP server the gateway spawns and talks to over stdin/stdout."""

    name: str
    command: str
    args: list[str] = field(default_factory=list)


Upstream = HttpUpstream | StdioUpstream

# A server's name becomes a URL path segment (/mcp/<name>), so keep it to characters that
# need no escaping.
_NAME_PATTERN = re.compile(r"[A-Za-z0-9_-]+")


def load_config(path: Path) -> dict[str, Upstream]:
    """Read the upstream servers from a TOML config file, keyed by server name.

    Raises:
        ValueError: The file has no servers, a server name is not a valid path segment,
            or a server sets neither or both of ``url`` and ``command``.
    """
    with path.open("rb") as file:
        servers = tomllib.load(file).get("servers", {})
    if not servers:
        raise ValueError(f"{path} defines no [servers.<name>] tables")
    return {name: _parse_server(name, entry) for name, entry in servers.items()}


def _parse_server(name: str, entry: dict) -> Upstream:
    if not _NAME_PATTERN.fullmatch(name):
        raise ValueError(
            f"Server name {name!r} must contain only letters, digits, '_' or '-'"
        )
    has_url, has_command = "url" in entry, "command" in entry
    if has_url == has_command:
        raise ValueError(f"Server {name!r} must set exactly one of 'url' or 'command'")
    if has_url:
        return HttpUpstream(name=name, url=entry["url"])
    return StdioUpstream(
        name=name, command=entry["command"], args=list(entry.get("args", []))
    )
