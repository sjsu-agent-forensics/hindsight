"""Run the interposer: the MCP gateway between the agent and its tool servers."""

import argparse
import logging
from pathlib import Path

import uvicorn

from interposer.config import load_config
from interposer.gateway import build_app

_APP_DIR = Path(__file__).resolve().parents[2]


def main() -> None:
    """Serve every configured upstream MCP server at ``/s/<session-id>/mcp/<name>``."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--config",
        type=Path,
        default=_APP_DIR / "servers.toml",
        help="TOML file listing the upstream servers (default: %(default)s)",
    )
    parser.add_argument(
        "--log-dir",
        type=Path,
        default=_APP_DIR / "logs",
        help="directory for the refused-traffic log (default: %(default)s)",
    )
    parser.add_argument(
        "--idle-timeout",
        type=float,
        default=30 * 60,
        help="seconds without a request before a session is ended (default: 1800)",
    )
    # Loopback by default: the gateway has no authentication of its own.
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO, format="%(levelname)s %(name)s: %(message)s"
    )
    app = build_app(load_config(args.config), args.log_dir, args.idle_timeout)
    uvicorn.run(app, host=args.host, port=args.port)
