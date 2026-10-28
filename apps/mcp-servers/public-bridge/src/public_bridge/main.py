"""CLI entry point for launching a public MCP tool bridge or exporting its manifest."""

import argparse
import asyncio
import json
import os
from pathlib import Path
import sys

from public_bridge.config import PublicServerConfig, load_config
from public_bridge.manifest import build_manifest, toml_interposer_snippet
from public_bridge.serve import serve
from public_bridge.stdio_process import StdioProcessClient

DEFAULT_CONFIG_LOCATIONS = [
    Path("public_servers.toml"),
    Path("apps/mcp-servers/public-bridge/public_servers.toml"),
    Path(__file__).resolve().parents[2] / "public_servers.toml",
]


def _find_config(custom_path: str | None = None) -> Path:
    """Resolve the public servers TOML configuration file path."""
    if custom_path:
        p = Path(custom_path)
        if not p.exists():
            sys.exit(f"Error: Specified configuration file {p} does not exist.")
        return p

    env_path = os.environ.get("HINDSIGHT_PUBLIC_SERVERS")
    if env_path:
        p = Path(env_path)
        if p.exists():
            return p

    for loc in DEFAULT_CONFIG_LOCATIONS:
        if loc.exists():
            return loc.resolve()

    sys.exit(
        "Error: public_servers.toml not found. Pass --config <path> or set HINDSIGHT_PUBLIC_SERVERS."
    )


async def _export_manifest_async(config: PublicServerConfig) -> dict[str, object]:
    """Fetch tools from the server and build its manifest record."""
    from public_bridge.config import StdioPublicServerConfig

    if isinstance(config, StdioPublicServerConfig):
        client = StdioProcessClient(
            name=config.name,
            command=config.command,
            args=config.args,
            env=config.env,
            cwd=config.cwd,
            startup_timeout=config.startup_timeout,
        )
        await client.start()
        try:
            tools = await client.list_tools()
            return build_manifest(config, tools)
        finally:
            await client.stop()

    import httpx

    async with httpx.AsyncClient(timeout=config.startup_timeout) as client:
        resp = await client.post(
            config.target_url,
            json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
            headers=config.headers,
        )
        resp.raise_for_status()
        raw_tools = resp.json().get("result", {}).get("tools", [])
        return build_manifest(config, raw_tools)


def main() -> None:
    """Parse command line arguments and start the public server bridge."""
    parser = argparse.ArgumentParser(
        prog="public-bridge",
        description="Bridge and hosting supervisor for public third-party MCP tools.",
    )
    parser.add_argument(
        "server",
        nargs="?",
        default=None,
        help="Name of the public server to run from public_servers.toml",
    )
    parser.add_argument(
        "--config",
        "-c",
        default=None,
        help="Path to public_servers.toml configuration file",
    )
    parser.add_argument(
        "--manifest",
        action="store_true",
        help="Fetch tools/list, print manifest with SHA-256 hashes as JSON, and exit",
    )
    parser.add_argument(
        "--interposer-snippet",
        action="store_true",
        help="Print interposer servers.toml configuration snippet and exit",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=None,
        help="Override MCP HTTP listener port",
    )
    parser.add_argument(
        "--control-port",
        type=int,
        default=None,
        help="Override control listener port",
    )

    args = parser.parse_args()
    config_path = _find_config(args.config)
    servers = load_config(config_path)

    server_name = args.server
    if server_name is None:
        if len(servers) == 1:
            server_name = next(iter(servers.keys()))
        else:
            names = ", ".join(servers.keys())
            sys.exit(
                f"Error: Multiple public servers configured ({names}). Specify one as argument."
            )

    if server_name not in servers:
        available = ", ".join(servers.keys())
        sys.exit(
            f"Error: Unknown server {server_name!r}. Available in {config_path}: {available}"
        )

    cfg = servers[server_name]
    if args.port is not None:
        object.__setattr__(cfg, "port", args.port)
    if args.control_port is not None:
        object.__setattr__(cfg, "control_port", args.control_port)

    if args.interposer_snippet:
        print(toml_interposer_snippet(cfg))
        return

    if args.manifest:
        manifest_data = asyncio.run(_export_manifest_async(cfg))
        print(json.dumps(manifest_data, indent=2))
        return

    serve(cfg)


if __name__ == "__main__":
    main()
