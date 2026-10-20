"""Run a server's MCP endpoint and its control endpoint side by side in one process."""

import argparse
import asyncio
import json
import sys

import uvicorn
from fastmcp import FastMCP

from hindsight_server_kit.control import control_app
from hindsight_server_kit.manifest import manifest
from hindsight_server_kit.session import SessionStore

MCP_PATH = "/api/mcp"

# The control endpoint listens on the MCP port plus this, e.g. records on 8011 and 8111.
CONTROL_PORT_OFFSET = 100


def serve(server: FastMCP, store: SessionStore, *, port: int, host: str = "127.0.0.1") -> None:
    """Parse the command line, then serve until interrupted, or print the manifest and exit.

    Binds 127.0.0.1 by default, unlike weather-server: these servers hold the confidential test
    data, and the interposer refuses anything but local HTTP upstreams anyway.
    """
    parser = argparse.ArgumentParser(prog=f"{store.name}-server", description=server.name)
    parser.add_argument("--manifest", action="store_true", help="print tools/list as JSON and exit")
    parser.add_argument("--host", default=host)
    parser.add_argument("--port", type=int, default=port, help="MCP port (control is +100)")
    args = parser.parse_args()

    if args.manifest:
        print(json.dumps(asyncio.run(manifest(server)), indent=2))
        return

    control_port = args.port + CONTROL_PORT_OFFSET
    print(
        f"{store.name}-server: MCP at http://{args.host}:{args.port}{MCP_PATH}, "
        f"control at http://{args.host}:{control_port}/control",
        file=sys.stderr,
    )
    try:
        asyncio.run(_serve_both(server, store, args.host, args.port, control_port))
    except KeyboardInterrupt:
        pass


async def _serve_both(
    server: FastMCP, store: SessionStore, host: str, port: int, control_port: int
) -> None:
    servers = [
        uvicorn.Server(uvicorn.Config(app, host=host, port=p, log_level="warning"))
        for app, p in (
            (server.http_app(path=MCP_PATH), port),
            (control_app(store), control_port),
        )
    ]
    tasks = [asyncio.create_task(_run(s)) for s in servers]
    # Each uvicorn server takes over Ctrl-C while it runs, so only one of them sees it. When
    # either stops, for that or because its port was taken, stop the other one too.
    await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
    for s in servers:
        s.should_exit = True
    await asyncio.gather(*tasks, return_exceptions=True)
    if any(not s.started for s in servers):
        sys.exit(f"{store.name}-server: could not start; is another copy already running?")


async def _run(server: uvicorn.Server) -> None:
    # uvicorn calls sys.exit() when it cannot bind its port. Inside a task that SystemExit would
    # tear down the whole event loop with a traceback, before the other server could stop.
    try:
        await server.serve()
    except SystemExit:
        pass
