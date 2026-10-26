"""Static upstream server configuration.

The gateway fronts a fixed set of upstream MCP servers, read once at startup from a TOML
file. Each server is either a remote one reached over streamable HTTP or a local one the
gateway runs as a child process over stdio, once per session:

```toml
[servers.weather]
url = "http://127.0.0.1:8001/api/mcp"

[servers.math]
project = "../mcp-servers/math"   # a uv project, relative to this file
program = "math-server"           # a console script in that project's .venv

[servers.sandboxed]
command = "docker"                # or any command, run as given
args = ["run", "-i", "--rm", "some-image"]
```

``project`` and ``program`` find the program inside the project's own virtual
environment on any OS, and run it directly. Running it through ``uv run`` instead would
put a wrapper process between the gateway and the server.
"""

import re
import sys
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
    """A local MCP server the gateway runs as a child process, talking over stdio."""

    name: str
    command: str
    args: list[str] = field(default_factory=list)
    cwd: Path | None = None


Upstream = HttpUpstream | StdioUpstream

# A server's name becomes a URL path segment (/mcp/<name>), so keep it to characters that
# need no escaping.
_NAME_PATTERN = re.compile(r"[A-Za-z0-9_-]+")


def load_config(path: Path) -> dict[str, Upstream]:
    """Read the upstream servers from a TOML config file, keyed by server name.

    Relative ``project`` paths are resolved against the config file's directory.

    Raises:
        ValueError: The file has no servers, a server name is not a valid path segment,
            a server does not set exactly one of ``url``, ``command`` or ``project``,
            or a ``project`` server's program is not installed.
    """
    with path.open("rb") as file:
        servers = tomllib.load(file).get("servers", {})
    if not servers:
        raise ValueError(f"{path} defines no [servers.<name>] tables")
    return {
        name: _parse_server(name, entry, path.parent) for name, entry in servers.items()
    }


def _parse_server(name: str, entry: dict, base_dir: Path) -> Upstream:
    if not _NAME_PATTERN.fullmatch(name):
        raise ValueError(
            f"Server name {name!r} must contain only letters, digits, '_' or '-'"
        )
    kinds = [key for key in ("url", "command", "project") if key in entry]
    if len(kinds) != 1:
        raise ValueError(
            f"Server {name!r} must set exactly one of 'url', 'command' or 'project'"
        )
    args = list(entry.get("args", []))
    if "url" in entry:
        return HttpUpstream(name=name, url=entry["url"])
    if "command" in entry:
        return StdioUpstream(name=name, command=entry["command"], args=args)
    project = (base_dir / entry["project"]).resolve()
    program = _venv_program(project, entry.get("program", ""))
    if not program.is_file():
        raise ValueError(
            f"Server {name!r}: {program} does not exist; "
            f"run `uv sync --project {project}` first"
        )
    return StdioUpstream(name=name, command=str(program), args=args, cwd=project)


def _venv_program(project: Path, program: str) -> Path:
    """Return where a console script lives in a project's .venv on this OS."""
    if sys.platform == "win32":
        return project / ".venv" / "Scripts" / f"{program}.exe"
    return project / ".venv" / "bin" / program
