"""Run the interposer: the MCP gateway between the agent and its tool servers."""

import argparse
import logging
from pathlib import Path

import uvicorn

from interposer.config import load_config
from interposer.gateway import build_app

_DEFAULT_CONFIG = Path(__file__).resolve().parents[2] / "servers.toml"


def main() -> None:
    """Serve every configured upstream MCP server at ``/mcp/<name>``."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--config",
        type=Path,
        default=_DEFAULT_CONFIG,
        help="TOML file listing the upstream servers (default: %(default)s)",
    )
    # Loopback by default: the gateway has no authentication of its own.
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO, format="%(levelname)s %(name)s: %(message)s"
    )
    uvicorn.run(build_app(load_config(args.config)), host=args.host, port=args.port)
