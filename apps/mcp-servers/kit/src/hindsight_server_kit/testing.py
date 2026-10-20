"""Test helpers: run a server for real on free local ports and talk to it over HTTP.

The in-memory fastmcp client cannot send request headers, so session handling can only be
tested through a real HTTP connection.
"""

import socket
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any

import httpx2
import uvicorn
from fastmcp import Client, FastMCP
from fastmcp.client.transports import StreamableHttpTransport

from hindsight_server_kit.control import control_app
from hindsight_server_kit.serve import MCP_PATH
from hindsight_server_kit.session import SESSION_HEADER, SessionStore


@dataclass
class RunningServer:
    """Addresses of a server started by `running()`."""

    mcp_url: str
    control_url: str

    def client(self, session: str | None = None) -> Client:
        """Return an MCP client, sending `session` as the session header if given."""
        headers = {SESSION_HEADER: session} if session else {}
        return Client(StreamableHttpTransport(self.mcp_url, headers=headers))

    def seed(self, session: str, **spec: Any) -> dict[str, Any]:
        """Seed (or reset) `session` through the control endpoint."""
        response = httpx2.post(f"{self.control_url}/sessions/{session}", json=spec)
        response.raise_for_status()
        return response.json()

    def effects(self, session: str) -> list[dict[str, Any]]:
        """Return `session`'s effect log."""
        response = httpx2.get(f"{self.control_url}/sessions/{session}/effects")
        response.raise_for_status()
        return response.json()["effects"]

    def discard(self, session: str) -> int:
        """Discard `session` and return the response status."""
        return httpx2.delete(f"{self.control_url}/sessions/{session}").status_code


@contextmanager
def running(server: FastMCP, store: SessionStore) -> Iterator[RunningServer]:
    """Serve `server` and its control endpoint on free ports for the duration of the block."""
    servers = []
    for app in (server.http_app(path=MCP_PATH), control_app(store)):
        config = uvicorn.Config(app, host="127.0.0.1", port=_free_port(), log_level="warning")
        servers.append(uvicorn.Server(config))
    # Off the main thread uvicorn leaves signal handlers alone, so pytest keeps its Ctrl-C.
    threads = [threading.Thread(target=s.run, daemon=True) for s in servers]
    for t in threads:
        t.start()
    deadline = time.monotonic() + 15
    while not all(s.started for s in servers):
        if time.monotonic() > deadline:
            raise RuntimeError("Test server did not start.")
        time.sleep(0.02)
    mcp, control = (s.config.port for s in servers)
    try:
        yield RunningServer(
            mcp_url=f"http://127.0.0.1:{mcp}{MCP_PATH}",
            control_url=f"http://127.0.0.1:{control}/control",
        )
    finally:
        for s in servers:
            s.should_exit = True
        for t in threads:
            t.join(timeout=10)


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]
