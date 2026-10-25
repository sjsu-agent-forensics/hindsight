"""ASGI app: the launcher's session endpoints and the agent's MCP paths.

Routes:

- ``POST /sessions``: the launcher registers a session (see ``_parse_registration``) and
  gets back the directory its trace files go in.
- ``DELETE /sessions/<session-id>``: the launcher ends a session and gets back its MCP
  session mapping.
- ``/s/<session-id>/mcp/<server>``: the agent's MCP endpoint for one server in one
  session.

Anything else, and any MCP request for an unknown or ended session, is refused with an
HTTP error and written to the refused log, never to a session.

There is one path per server rather than one combined endpoint: the agent sees each
server's tool names and descriptors exactly as that server publishes them, with no
renaming to avoid collisions.
"""

import json
import logging
import uuid
from collections.abc import AsyncGenerator, Awaitable, Callable
from contextlib import asynccontextmanager
from pathlib import Path

import anyio
from anyio.abc import TaskGroup, TaskStatus
from mcp.server.streamable_http import (
    MCP_SESSION_ID_HEADER,
    StreamableHTTPServerTransport,
)
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Route
from starlette.types import Message, Receive, Scope, Send

from interposer.config import HttpUpstream, StdioUpstream, Upstream
from interposer.refused import RefusedLog
from interposer.relay import UpstreamRelay
from interposer.sessions import (
    TRUSTED_INPUT_KINDS,
    McpChannel,
    Session,
    SessionExistsError,
    SessionRegistry,
    TrustedInput,
    normalize_session_id,
)
from interposer.tracing import ChannelTracer, TraceWriter

logger = logging.getLogger(__name__)

_ALL_METHODS = ["GET", "HEAD", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"]


def build_app(
    upstreams: dict[str, Upstream],
    log_dir: Path,
    trace_dir: Path,
    idle_timeout: float,
) -> Starlette:
    """Build the gateway app serving every configured upstream server.

    Args:
        log_dir: Directory for the refused log (``refused.jsonl``).
        trace_dir: Directory under which each session gets a directory of trace files.
        idle_timeout: Seconds without a request after which a session the launcher never
            ended is ended.

    Raises:
        ValueError: A configured server uses stdio, which the gateway does not serve yet.
    """
    http_upstreams: dict[str, HttpUpstream] = {}
    for name, upstream in upstreams.items():
        if isinstance(upstream, StdioUpstream):
            raise ValueError(
                f"Server {name!r} is a stdio server; the gateway serves only HTTP "
                "upstreams until per-session process management is added"
            )
        http_upstreams[name] = upstream

    trace_writer = TraceWriter(trace_dir)
    registry = SessionRegistry(idle_timeout, trace_writer)
    refused = RefusedLog(log_dir / "refused.jsonl")
    launcher_api = _LauncherApi(registry, trace_writer)
    mcp_endpoint = _McpEndpoint(http_upstreams, registry, refused)

    @asynccontextmanager
    async def lifespan(_app: Starlette) -> AsyncGenerator[None]:
        async with anyio.create_task_group() as tg:
            mcp_endpoint.task_group = tg
            tg.start_soon(trace_writer.run)
            tg.start_soon(registry.expire_idle)
            try:
                yield
            finally:
                # End every session (closing its upstream sessions and writing out its
                # trace) before the relays and the trace writer are cancelled.
                await registry.end_all()
                tg.cancel_scope.cancel()

    async def refuse_unrouted(request: Request) -> Response:
        return await refused.refuse(
            request,
            404,
            "no_session",
            "MCP requests must use /s/<session-id>/mcp/<server>",
        )

    routes = [
        Route("/sessions", launcher_api.register, methods=["POST"]),
        Route("/sessions/{session_id}", launcher_api.end, methods=["DELETE"]),
        Route("/s/{session_id}/mcp/{server}", endpoint=mcp_endpoint),
        Route("/{path:path}", refuse_unrouted, methods=_ALL_METHODS),
    ]
    return Starlette(routes=routes, lifespan=lifespan)


class _LauncherApi:
    """The launcher's endpoints for registering and ending sessions."""

    def __init__(self, registry: SessionRegistry, trace_writer: TraceWriter) -> None:
        self._registry = registry
        self._trace_writer = trace_writer

    async def register(self, request: Request) -> Response:
        """Register a session from the JSON registration payload.

        Answers 201 with the canonical session ID and the session's trace directory,
        where the agent's trace belongs too; 400 for an invalid payload; or 409 if the
        ID was registered before.
        """
        try:
            session_id, trusted_inputs = _parse_registration(await request.json())
        except (json.JSONDecodeError, ValueError) as error:
            return JSONResponse({"error": f"Invalid registration: {error}"}, 400)
        try:
            self._registry.register(session_id, trusted_inputs)
        except SessionExistsError:
            return JSONResponse(
                {"error": f"Session {session_id} was already registered"}, 409
            )
        trace_dir = self._trace_writer.session_dir(session_id).resolve()
        return JSONResponse(
            {"session_id": session_id, "trace_dir": str(trace_dir)}, 201
        )

    async def end(self, request: Request) -> Response:
        """End a session and answer with its summary, including its MCP sessions.

        Returns only after the session's upstream sessions are closed. Ending a session
        that already ended (for example by idle timeout) returns the same summary.
        Answers 404 for an ID that was never registered.
        """
        raw_id = request.path_params["session_id"]
        session_id = normalize_session_id(raw_id)
        session = self._registry.lookup(session_id) if session_id else None
        if session is None:
            return JSONResponse({"error": f"Unknown session {raw_id!r}"}, 404)
        await self._registry.end(session, "launcher")
        return JSONResponse(session.summary())


def _parse_registration(payload: object) -> tuple[str, list[TrustedInput]]:
    """Validate a registration payload and return its session ID and trusted inputs.

    The payload is ``{"session_id": "<uuid>", "trusted_inputs": [{"kind", "content"}]}``,
    where ``kind`` is ``user_prompt``, ``system_prompt``, ``attached_file`` or ``turn``.

    Raises:
        ValueError: The payload does not have that shape.
    """
    if not isinstance(payload, dict):
        raise ValueError("payload must be a JSON object")
    session_id = normalize_session_id(str(payload.get("session_id", "")))
    if session_id is None:
        raise ValueError("'session_id' must be a UUID")
    raw_inputs = payload.get("trusted_inputs")
    if not isinstance(raw_inputs, list):
        raise ValueError("'trusted_inputs' must be a list")
    trusted_inputs = []
    for raw in raw_inputs:
        if (
            not isinstance(raw, dict)
            or raw.get("kind") not in TRUSTED_INPUT_KINDS
            or not isinstance(raw.get("content"), str)
        ):
            raise ValueError(
                "each trusted input must be {'kind', 'content'} with kind one of "
                f"{sorted(TRUSTED_INPUT_KINDS)}"
            )
        trusted_inputs.append(TrustedInput(kind=raw["kind"], content=raw["content"]))
    return session_id, trusted_inputs


class _McpEndpoint:
    """Raw ASGI endpoint for ``/s/<session-id>/mcp/<server>``.

    Admits a request only for a registered, active session and a configured server.
    Within a session, a request without an ``Mcp-Session-Id`` opens a new MCP session,
    relayed to its own upstream session; a request with one is handed to that MCP
    session's transport.

    A class instance rather than a function, because Starlette wraps a function or
    method endpoint as a request/response handler instead of an ASGI app.
    """

    def __init__(
        self,
        upstreams: dict[str, HttpUpstream],
        registry: SessionRegistry,
        refused: RefusedLog,
    ) -> None:
        self._upstreams = upstreams
        self._registry = registry
        self._refused = refused
        # Set by the app's lifespan; the relays run in it so they outlive the request
        # that opened them.
        self.task_group: TaskGroup | None = None

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        request = Request(scope, receive)
        raw_id = request.path_params["session_id"]
        server = request.path_params["server"]
        session_id = normalize_session_id(raw_id)
        session = self._registry.lookup(session_id) if session_id else None

        if session is None:
            response = await self._refused.refuse(
                request, 403, "unknown_session", f"Unknown hindsight session {raw_id!r}"
            )
        elif session.ended_by is not None:
            response = await self._refused.refuse(
                request,
                403,
                "ended_session",
                f"Hindsight session {session.id} has ended",
            )
        elif server not in self._upstreams:
            response = await self._refused.refuse(
                request, 404, "unknown_server", f"Unknown MCP server {server!r}"
            )
        else:
            with session.activity(counts_as_in_flight=request.method != "GET"):
                await self._serve(session, self._upstreams[server], request, send)
            return
        await response(scope, receive, send)

    async def _serve(
        self, session: Session, upstream: HttpUpstream, request: Request, send: Send
    ) -> None:
        """Hand an admitted request to its MCP session, opening one if it has no ID."""
        mcp_session_id = request.headers.get(MCP_SESSION_ID_HEADER)
        if mcp_session_id is None:
            await self._open_channel(session, upstream, request, send)
            return
        channel = session.channels.get(mcp_session_id)
        if channel is None or channel.server != upstream.name:
            # 404 is what the MCP spec prescribes for an unknown MCP session ID.
            response = await self._refused.refuse(
                request, 404, "unknown_mcp_session", "MCP session not found"
            )
            await response(request.scope, request.receive, send)
            return
        await channel.transport.handle_request(request.scope, request.receive, send)

    async def _open_channel(
        self, session: Session, upstream: HttpUpstream, request: Request, send: Send
    ) -> None:
        """Open a new MCP session in ``session`` and serve its opening request.

        The opening request must be ``initialize``; the transport refuses anything else.
        If the request is not answered with success, the MCP session is dropped and not
        recorded.
        """
        assert self.task_group is not None, "the app's lifespan has not started"
        transport = StreamableHTTPServerTransport(mcp_session_id=uuid.uuid4().hex)
        channel = McpChannel(server=upstream.name, transport=transport)
        # Recorded before the opening request is served: the agent sends its next request
        # as soon as it has the session ID, which can be before this request returns.
        session.channels[channel.mcp_session_id] = channel
        tracer = ChannelTracer(session.trace, upstream.name, channel.mcp_session_id)
        await self.task_group.start(_run_channel, channel, upstream, tracer)

        status = await _serve_and_report_status(
            transport.handle_request, request.scope, request.receive, send
        )
        if status is None or status >= 400:
            del session.channels[channel.mcp_session_id]
            await transport.terminate()
            return
        logger.info(
            "Session %s: opened MCP session %s on %r",
            session.id,
            channel.mcp_session_id,
            upstream.name,
        )


async def _run_channel(
    channel: McpChannel,
    upstream: HttpUpstream,
    tracer: ChannelTracer,
    *,
    task_status: TaskStatus[None] = anyio.TASK_STATUS_IGNORED,
) -> None:
    """Relay and trace one MCP session until either side closes, then mark it closed.

    However the relay ends, the agent-facing transport is terminated, so later requests
    with this MCP session ID get 404 rather than reaching a dead relay.
    """

    def record_upstream_session(upstream_session_id: str) -> None:
        channel.upstream_session_id = upstream_session_id
        tracer.upstream_session_id = upstream_session_id

    try:
        async with channel.transport.connect() as (agent_read, agent_write):
            task_status.started()
            relay = UpstreamRelay(
                upstream,
                observer=tracer,
                on_upstream_session=record_upstream_session,
            )
            await relay.run(agent_read, agent_write)
    finally:
        with anyio.CancelScope(shield=True):
            await channel.transport.terminate()
        channel.closed.set()


async def _serve_and_report_status(
    app: Callable[[Scope, Receive, Send], Awaitable[None]],
    scope: Scope,
    receive: Receive,
    send: Send,
) -> int | None:
    """Run an ASGI app and return the HTTP status it answered with, or None if none."""
    status: int | None = None

    async def send_and_record(message: Message) -> None:
        nonlocal status
        if message["type"] == "http.response.start":
            status = message["status"]
        await send(message)

    await app(scope, receive, send_and_record)
    return status
