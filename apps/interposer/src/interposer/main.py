"""Run the interposer: the MCP gateway between the agent and its tool servers."""

import argparse
import logging
from pathlib import Path
from typing import get_args

import uvicorn
from provenance import ExtractionConfig, ValueType

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
        "--trace-dir",
        type=Path,
        default=_APP_DIR.parents[1] / "traces",
        help="directory for session traces, one subdirectory per session "
        "(default: %(default)s)",
    )
    parser.add_argument(
        "--idle-timeout",
        type=float,
        default=30 * 60,
        help="seconds without a request before a session is ended (default: 1800)",
    )
    parser.add_argument(
        "--upstream-timeout",
        type=float,
        default=30,
        help="seconds a call may wait for its upstream server before the agent gets a "
        "timeout error (default: 30)",
    )
    parser.add_argument(
        "--min-value-length",
        type=int,
        default=ExtractionConfig.min_length,
        help="shortest whole string the provenance index records (default: %(default)s)",
    )
    parser.add_argument(
        "--value-types",
        default=",".join(sorted(get_args(ValueType))),
        help="comma-separated kinds of value the provenance index records "
        "(default: %(default)s)",
    )
    # Loopback by default: the gateway has no authentication of its own.
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()
    value_types = frozenset(t.strip() for t in args.value_types.split(",") if t)
    unknown = value_types - set(get_args(ValueType))
    if unknown:
        parser.error(f"unknown --value-types: {', '.join(sorted(unknown))}")

    logging.basicConfig(
        level=logging.INFO, format="%(levelname)s %(name)s: %(message)s"
    )
    app = build_app(
        load_config(args.config),
        args.log_dir,
        args.trace_dir,
        args.idle_timeout,
        args.upstream_timeout,
        extraction=ExtractionConfig(args.min_value_length, value_types),
    )
    uvicorn.run(app, host=args.host, port=args.port)
