"""Test helpers: run the gateway and an upstream MCP server for it on free ports.

Both run for real, each in its own thread with its own event loop, and are reached over
HTTP like the agent reaches them, so a test exercises the same paths a session does.
Tests are plain functions; ``run()`` drives the async MCP client from them.
"""

import asyncio
import json
import socket
import tempfile
import threading
import time
import uuid
from collections.abc import Awaitable, Callable, Iterator
from contextlib import AsyncExitStack, contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, TypeVar

import httpx
import uvicorn
from anyio.streams.memory import MemoryObjectReceiveStream, MemoryObjectSendStream
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client
from mcp.server.fastmcp import FastMCP
from mcp.shared.message import SessionMessage
from mcp.types import (
    LATEST_PROTOCOL_VERSION,
    JSONRPCError,
    JSONRPCMessage,
    JSONRPCNotification,
    JSONRPCRequest,
    JSONRPCResponse,
)
from starlette.types import ASGIApp

from interposer.config import Upstream
from interposer.gateway import build_app

T = TypeVar("T")


@dataclass(frozen=True)
class RegisteredSession:
    """A session registered with a running gateway."""

    id: str
    trace_dir: Path


@dataclass
class RunningGateway:
    """A gateway started by ``running_gateway()``, with calls for its launcher API."""

    url: str
    log_dir: Path

    def register(self, prompt: str = "test") -> RegisteredSession:
        """Register a new session with ``prompt`` as its only trusted input."""
        session_id = str(uuid.uuid4())
        response = httpx.post(
            f"{self.url}/sessions",
            json={
                "session_id": session_id,
                "trusted_inputs": [{"kind": "user_prompt", "content": prompt}],
            },
            timeout=30,
        )
        response.raise_for_status()
        return RegisteredSession(session_id, Path(response.json()["trace_dir"]))

    def end(self, session_id: str) -> dict[str, Any]:
        """End a session and return its summary."""
        response = httpx.delete(f"{self.url}/sessions/{session_id}", timeout=60)
        response.raise_for_status()
        return response.json()

    def mcp_url(self, session_id: str, server: str) -> str:
        """Return the agent's MCP URL for ``server`` within a session."""
        return f"{self.url}/s/{session_id}/mcp/{server}"

    def refusals(self) -> list[str]:
        """Return the reason of every request in the refused log so far."""
        path = self.log_dir / "refused.jsonl"
        if not path.exists():
            return []
        lines = path.read_text(encoding="utf-8").splitlines()
        return [json.loads(line)["reason"] for line in lines]


@contextmanager
def running_gateway(
    upstreams: dict[str, Upstream],
    idle_timeout: float = 60,
    upstream_timeout: float = 30,
) -> Iterator[RunningGateway]:
    """Serve a gateway for ``upstreams`` on a free port for the duration of the block.

    Its logs and traces go to a temporary directory, removed afterwards. Leaving the
    block ends every open session, stopping its children, as a gateway shutdown does.
    """
    with tempfile.TemporaryDirectory() as temp_dir:
        log_dir = Path(temp_dir) / "logs"
        app = build_app(
            upstreams,
            log_dir,
            Path(temp_dir) / "traces",
            idle_timeout=idle_timeout,
            upstream_timeout=upstream_timeout,
        )
        with _serving(app) as url:
            yield RunningGateway(url, log_dir)


@contextmanager
def running_test_server() -> Iterator[str]:
    """Serve a small HTTP MCP server on a free port and yield its MCP URL.

    Its tools: ``forecast`` (no arguments, a fixed result) and ``echo`` (returns its
    ``text``). It stands in for a remote tool server, so gateway tests need nothing else
    running.
    """
    # A fresh server each time: its session manager can run only once.
    server = FastMCP("Test Server")

    @server.tool()
    def forecast() -> dict:
        """Get the forecast."""
        return {"temperature_celsius": 25, "condition": "Sunny"}

    @server.tool()
    def echo(text: str) -> str:
        """Return the given text."""
        return text

    with _serving(server.streamable_http_app()) as url:
        yield f"{url}{server.settings.streamable_http_path}"


def run(function: Callable[..., Awaitable[T]], *args: Any) -> T:
    """Run an async function to completion from a plain test function."""
    return asyncio.run(function(*args))


async def open_session(stack: AsyncExitStack, url: str) -> ClientSession:
    """Open and initialize an MCP session to ``url``, closed when ``stack`` closes."""
    read, write, _ = await stack.enter_async_context(streamable_http_client(url))
    session = await stack.enter_async_context(ClientSession(read, write))
    await session.initialize()
    return session


async def list_tools_raw(url: str) -> dict[str, Any]:
    """Return the ``tools/list`` result from ``url`` exactly as received.

    Works at the transport level, without parsing into the SDK's Tool model, which would
    drop fields it does not know.
    """

    async with streamable_http_client(url) as (read, write, _):
        await _request(
            read,
            write,
            1,
            "initialize",
            {
                "protocolVersion": LATEST_PROTOCOL_VERSION,
                "capabilities": {},
                "clientInfo": {"name": "interposer-tests", "version": "0.1.0"},
            },
        )
        initialized = JSONRPCNotification(
            jsonrpc="2.0", method="notifications/initialized"
        )
        await write.send(SessionMessage(message=JSONRPCMessage(initialized)))
        return await _request(read, write, 2, "tools/list", {})


def free_port() -> int:
    """Return a local TCP port that is free right now."""
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


async def _request(
    read: MemoryObjectReceiveStream[SessionMessage | Exception],
    write: MemoryObjectSendStream[SessionMessage],
    request_id: int,
    method: str,
    params: dict[str, Any],
) -> dict[str, Any]:
    request = JSONRPCRequest(jsonrpc="2.0", id=request_id, method=method, params=params)
    await write.send(SessionMessage(message=JSONRPCMessage(request)))
    async for item in read:
        if isinstance(item, Exception):
            raise item
        reply = item.message.root
        if isinstance(reply, JSONRPCResponse) and reply.id == request_id:
            return reply.result
        if isinstance(reply, JSONRPCError) and reply.id == request_id:
            raise RuntimeError(f"{method} failed: {reply.error.message}")
    raise RuntimeError(f"Session closed before {method} was answered")


@contextmanager
def _serving(app: ASGIApp) -> Iterator[str]:
    """Serve an ASGI app on a free port in a background thread; yield its base URL."""
    port = free_port()
    # Shutdown waits for open connections, and an SSE stream only notices a shutdown
    # through signal handlers, which a server off the main thread does not install. The
    # grace period bounds that wait; without it a stream still open at teardown holds the
    # thread for as long as the join below allows.
    server = uvicorn.Server(
        uvicorn.Config(
            app,
            host="127.0.0.1",
            port=port,
            log_level="warning",
            timeout_graceful_shutdown=2,
        )
    )
    # Off the main thread uvicorn leaves signal handlers alone, so pytest keeps Ctrl-C.
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 15
    while not server.started:
        if time.monotonic() > deadline or not thread.is_alive():
            raise RuntimeError("Test server did not start")
        time.sleep(0.02)
    try:
        yield f"http://127.0.0.1:{port}"
    finally:
        server.should_exit = True
        thread.join(timeout=60)
