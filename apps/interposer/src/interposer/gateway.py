"""ASGI app that exposes each upstream MCP server at its own URL path.

One path per server, ``/mcp/<name>``, rather than one combined endpoint: the agent sees
each server's tool names and descriptors exactly as that server publishes them, with no
renaming to avoid collisions.
"""

from collections.abc import AsyncGenerator
from contextlib import AsyncExitStack, asynccontextmanager
from typing import Any, cast

from mcp.server.streamable_http_manager import StreamableHTTPSessionManager
from starlette.applications import Starlette
from starlette.routing import Route
from starlette.types import Receive, Scope, Send

from interposer.config import StdioUpstream, Upstream
from interposer.relay import UpstreamRelay


def build_app(upstreams: dict[str, Upstream]) -> Starlette:
    """Build the gateway app serving every configured upstream server.

    Raises:
        ValueError: A configured server uses stdio, which the gateway does not serve yet.
    """
    managers = {
        name: _session_manager(upstream) for name, upstream in upstreams.items()
    }

    @asynccontextmanager
    async def lifespan(_app: Starlette) -> AsyncGenerator[None]:
        async with AsyncExitStack() as stack:
            for manager in managers.values():
                await stack.enter_async_context(manager.run())
            yield

    routes = [
        Route(f"/mcp/{name}", endpoint=_SessionEndpoint(manager))
        for name, manager in managers.items()
    ]
    return Starlette(routes=routes, lifespan=lifespan)


def _session_manager(upstream: Upstream) -> StreamableHTTPSessionManager:
    if isinstance(upstream, StdioUpstream):
        raise ValueError(
            f"Server {upstream.name!r} is a stdio server; the gateway serves only HTTP "
            "upstreams until per-session process management is added"
        )
    # The manager is typed for the SDK's low-level Server but only calls run() and
    # create_initialization_options(), which UpstreamRelay provides.
    return StreamableHTTPSessionManager(app=cast(Any, UpstreamRelay(upstream)))


class _SessionEndpoint:
    """Raw ASGI endpoint handing every request on a server's path to its session
    manager.

    A class instance rather than the manager's bound method, because Starlette wraps a
    function or method endpoint as a request/response handler instead of an ASGI app.
    """

    def __init__(self, manager: StreamableHTTPSessionManager) -> None:
        self._manager = manager

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        await self._manager.handle_request(scope, receive, send)
