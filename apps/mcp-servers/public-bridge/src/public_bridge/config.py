"""Configuration schemas and loaders for public MCP servers.

Public MCP tools are defined declaratively in a TOML configuration file, specifying their
transport (stdio or HTTP), command/URL, loopback port allocations, and session sandbox policies.
Every network listener is strictly bound to the loopback address (127.0.0.1).
"""

from dataclasses import dataclass, field
import os
from pathlib import Path
import re
import tomllib
from typing import Literal

SandboxMode = Literal["stateless", "ephemeral_dir", "process_per_session"]

# Server names become URL path segments (/mcp/<name>), so keep to safe characters.
_NAME_PATTERN = re.compile(r"^[A-Za-z0-9_-]+$")

# Matches ${VAR} or ${VAR:-default} for environment variable expansion.
_ENV_VAR_PATTERN = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)(?::-([^}]*))?\}")

DEFAULT_HOST = "127.0.0.1"
DEFAULT_CONTROL_OFFSET = 100


@dataclass(frozen=True)
class StdioPublicServerConfig:
    """Configuration for a third-party MCP tool server launched over stdio."""

    name: str
    command: str
    args: list[str] = field(default_factory=list)
    env: dict[str, str] = field(default_factory=dict)
    cwd: Path | None = None
    port: int = 8021
    control_port: int = 8121
    host: str = DEFAULT_HOST
    sandbox_mode: SandboxMode = "stateless"
    fixture_dir: Path | None = None
    allowed_tools: list[str] | None = None
    startup_timeout: float = 30.0
    transport: Literal["stdio"] = "stdio"


@dataclass(frozen=True)
class HttpPublicServerConfig:
    """Configuration for an upstream third-party MCP tool server reached over HTTP."""

    name: str
    target_url: str
    port: int = 8021
    control_port: int = 8121
    host: str = DEFAULT_HOST
    headers: dict[str, str] = field(default_factory=dict)
    allowed_tools: list[str] | None = None
    startup_timeout: float = 30.0
    transport: Literal["http"] = "http"


PublicServerConfig = StdioPublicServerConfig | HttpPublicServerConfig


def _expand_env_vars(text: str) -> str:
    """Replace ${VAR} and ${VAR:-default} placeholders with environment variable values."""

    def _replace(match: re.Match[str]) -> str:
        var_name = match.group(1)
        default_val = match.group(2) if match.group(2) is not None else ""
        return os.environ.get(var_name, default_val)

    return _ENV_VAR_PATTERN.sub(_replace, text)


def _parse_stdio_server(name: str, entry: dict[str, object]) -> StdioPublicServerConfig:
    """Parse and validate a stdio public server configuration block."""
    if "command" not in entry or not isinstance(entry["command"], str):
        raise ValueError(f"Public server {name!r} must specify a string 'command'")

    command = _expand_env_vars(entry["command"])
    raw_args = entry.get("args", [])
    if not isinstance(raw_args, list):
        raise ValueError(f"Public server {name!r} 'args' must be a list of strings")
    args = [_expand_env_vars(str(a)) for a in raw_args]

    raw_env = entry.get("env", {})
    if not isinstance(raw_env, dict):
        raise ValueError(f"Public server {name!r} 'env' must be a table")
    env = {k: _expand_env_vars(str(v)) for k, v in raw_env.items()}

    cwd = Path(_expand_env_vars(str(entry["cwd"]))) if "cwd" in entry else None
    port = int(entry.get("port", 8021))
    control_port = int(entry.get("control_port", port + DEFAULT_CONTROL_OFFSET))
    host = str(entry.get("host", DEFAULT_HOST))

    if host != DEFAULT_HOST:
        raise ValueError(
            f"Public server {name!r} host {host!r} must be {DEFAULT_HOST!r} (N-06 loopback only)"
        )

    sandbox_mode = str(entry.get("sandbox_mode", "stateless"))
    if sandbox_mode not in ("stateless", "ephemeral_dir", "process_per_session"):
        raise ValueError(
            f"Public server {name!r} invalid sandbox_mode: {sandbox_mode!r}"
        )

    fixture_dir = (
        Path(_expand_env_vars(str(entry["fixture_dir"])))
        if "fixture_dir" in entry
        else None
    )

    allowed_tools = (
        [str(t) for t in entry["allowed_tools"]]
        if "allowed_tools" in entry and isinstance(entry["allowed_tools"], list)
        else None
    )

    startup_timeout = float(entry.get("startup_timeout", 30.0))

    return StdioPublicServerConfig(
        name=name,
        command=command,
        args=args,
        env=env,
        cwd=cwd,
        port=port,
        control_port=control_port,
        host=host,
        sandbox_mode=sandbox_mode,  # type: ignore[arg-type]
        fixture_dir=fixture_dir,
        allowed_tools=allowed_tools,
        startup_timeout=startup_timeout,
    )


def _parse_http_server(name: str, entry: dict[str, object]) -> HttpPublicServerConfig:
    """Parse and validate an HTTP public server configuration block."""
    if "target_url" not in entry or not isinstance(entry["target_url"], str):
        raise ValueError(f"Public server {name!r} must specify a string 'target_url'")

    target_url = _expand_env_vars(entry["target_url"])
    port = int(entry.get("port", 8021))
    control_port = int(entry.get("control_port", port + DEFAULT_CONTROL_OFFSET))
    host = str(entry.get("host", DEFAULT_HOST))

    if host != DEFAULT_HOST:
        raise ValueError(
            f"Public server {name!r} host {host!r} must be {DEFAULT_HOST!r} (N-06 loopback only)"
        )

    raw_headers = entry.get("headers", {})
    headers = (
        {k: _expand_env_vars(str(v)) for k, v in raw_headers.items()}
        if isinstance(raw_headers, dict)
        else {}
    )

    allowed_tools = (
        [str(t) for t in entry["allowed_tools"]]
        if "allowed_tools" in entry and isinstance(entry["allowed_tools"], list)
        else None
    )

    startup_timeout = float(entry.get("startup_timeout", 30.0))

    return HttpPublicServerConfig(
        name=name,
        target_url=target_url,
        port=port,
        control_port=control_port,
        host=host,
        headers=headers,
        allowed_tools=allowed_tools,
        startup_timeout=startup_timeout,
    )


def load_config(path: Path) -> dict[str, PublicServerConfig]:
    """Load and validate the public MCP servers from a TOML configuration file.

    Args:
        path: Path to the public servers TOML configuration file.

    Returns:
        Dictionary mapping server names to their configuration objects.

    Raises:
        FileNotFoundError: The configuration file does not exist.
        ValueError: The file is missing server tables or contains invalid configuration.
    """
    if not path.exists():
        raise FileNotFoundError(f"Public server configuration file not found: {path}")

    with path.open("rb") as f:
        data = tomllib.load(f)

    servers_dict = data.get("public_servers", {})
    if not servers_dict:
        raise ValueError(f"{path} defines no [public_servers.<name>] tables")

    configs: dict[str, PublicServerConfig] = {}
    used_ports: dict[int, str] = {}

    for name, entry in servers_dict.items():
        if not isinstance(entry, dict):
            continue
        if not _NAME_PATTERN.fullmatch(name):
            raise ValueError(
                f"Public server name {name!r} must contain only letters, digits, '_' or '-'"
            )

        transport = entry.get("transport", "stdio")
        if transport == "stdio":
            cfg = _parse_stdio_server(name, entry)
        elif transport == "http":
            cfg = _parse_http_server(name, entry)
        else:
            raise ValueError(
                f"Public server {name!r} invalid transport {transport!r}; must be 'stdio' or 'http'"
            )

        # Check port collisions
        if cfg.port in used_ports:
            raise ValueError(
                f"Port {cfg.port} collision between {used_ports[cfg.port]!r} and {name!r}"
            )
        used_ports[cfg.port] = f"{name} (MCP)"

        if cfg.control_port in used_ports:
            raise ValueError(
                f"Control port {cfg.control_port} collision between {used_ports[cfg.control_port]!r} and {name!r}"
            )
        used_ports[cfg.control_port] = f"{name} (Control)"

        configs[name] = cfg

    return configs
