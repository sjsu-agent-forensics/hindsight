"""Serve a public MCP tool bridge and its control endpoint side by side in one process.

Both listeners strictly bind to 127.0.0.1, with MCP exposed at /api/mcp and the session
control endpoint at /control.
"""

import asyncio
import contextlib
import logging
import signal
import sys
from typing import Any

import uvicorn

from public_bridge.bridge import PublicBridgeServer
from public_bridge.config import PublicServerConfig, StdioPublicServerConfig
from public_bridge.control import control_app
from public_bridge.sandbox import SessionSandboxManager
from public_bridge.stdio_process import StdioProcessClient

logger = logging.getLogger(__name__)


class _Server(uvicorn.Server):
    """Uvicorn server subclass that coordinates signal handling across dual listeners."""

    @contextlib.contextmanager
    def capture_signals(self) -> Any:
        yield


async def serve_bridge_async(config: PublicServerConfig) -> None:
    """Run the MCP bridge server and control endpoint side by side until stopped.

    Args:
        config: Configuration for the public server to run.
    """
    stdio_client: StdioProcessClient | None = None
    if isinstance(config, StdioPublicServerConfig):
        stdio_client = StdioProcessClient(
            name=config.name,
            command=config.command,
            args=config.args,
            env=config.env,
            cwd=config.cwd,
            startup_timeout=config.startup_timeout,
        )
        await stdio_client.start()

    sandbox_mgr = SessionSandboxManager(
        server_name=config.name, fixture_dir=config.fixture_dir
    )
    bridge_server = PublicBridgeServer(
        config=config, sandbox_manager=sandbox_mgr, stdio_client=stdio_client
    )

    mcp_app = bridge_server.build_app()
    ctrl_app = control_app(
        server_name=config.name,
        sandbox_manager=sandbox_mgr,
        is_alive_fn=lambda: stdio_client.is_running if stdio_client else True,
    )

    servers = [
        _Server(
            uvicorn.Config(
                mcp_app,
                host=config.host,
                port=config.port,
                log_level="warning",
            )
        ),
        _Server(
            uvicorn.Config(
                ctrl_app,
                host=config.host,
                port=config.control_port,
                log_level="warning",
            )
        ),
    ]

    def stop_all(_signum: int, _frame: Any) -> None:
        for s in servers:
            if s.should_exit:
                s.force_exit = True
            s.should_exit = True

    prev_handlers = {
        sig: signal.signal(sig, stop_all) for sig in (signal.SIGINT, signal.SIGTERM)
    }

    try:
        tasks = [asyncio.create_task(_run_server(s)) for s in servers]
        await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        for s in servers:
            s.should_exit = True
        await asyncio.gather(*tasks, return_exceptions=True)
    finally:
        for sig, h in prev_handlers.items():
            signal.signal(sig, h)
        await bridge_server.close()
        sandbox_mgr.cleanup_all()


async def _run_server(server: uvicorn.Server) -> None:
    """Run an individual uvicorn server, catching bind failure SystemExit."""
    try:
        await server.serve()
    except SystemExit:
        pass


def serve(config: PublicServerConfig) -> None:
    """Synchronous entry point to serve the bridge for a configured public server."""
    print(
        f"public-bridge ({config.name}): MCP at http://{config.host}:{config.port}/api/mcp, "
        f"control at http://{config.host}:{config.control_port}/control",
        file=sys.stderr,
    )
    try:
        asyncio.run(serve_bridge_async(config))
    except KeyboardInterrupt:
        pass
