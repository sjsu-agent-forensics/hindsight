"""Relay one agent-facing MCP session to its own upstream session.

Every JSON-RPC message is forwarded as received, in both directions, except a tool call
the session's rules block, which is answered with an error instead. The relay never
changes tool names, descriptors, arguments or results, so what the agent sees is what
the upstream server sent. It shows each message to an observer (the tracer and the
indexer) before forwarding it. Upstream content is untrusted and is passed through, not
acted on.

The relay works the same for remote HTTP servers and local stdio children: it connects
through a connector that yields the upstream's message streams.
"""

import logging
from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from typing import Protocol

import anyio
from anyio.abc import TaskGroup
from anyio.streams.memory import MemoryObjectReceiveStream, MemoryObjectSendStream
from mcp.shared.message import SessionMessage
from mcp.types import (
    INTERNAL_ERROR,
    ErrorData,
    JSONRPCError,
    JSONRPCMessage,
    JSONRPCRequest,
    JSONRPCResponse,
    RequestId,
)

from interposer.enforcement import CallDecision

logger = logging.getLogger(__name__)

ReadStream = MemoryObjectReceiveStream[SessionMessage | Exception]
WriteStream = MemoryObjectSendStream[SessionMessage]

# Opens the upstream side: yields its read and write streams and a callable returning the
# upstream's session ID once it has issued one (None for servers that never do).
UpstreamConnector = Callable[
    [],
    AbstractAsyncContextManager[
        tuple[ReadStream, WriteStream, Callable[[], str | None]]
    ],
]

# JSON-RPC defines no timeout code; this is the one the TypeScript MCP SDK uses. Only
# requests other than tool calls get it: a tool call gets a tool error result instead.
_REQUEST_TIMEOUT = -32001


class MessageObserver(Protocol):
    """Sees every relayed message, and every call the relay answers itself.

    Called on the forwarding path, so it must not block. It cannot change or drop
    messages.
    """

    def on_agent_message(
        self, message: JSONRPCMessage, decision: CallDecision | None
    ) -> None:
        """See a message from the agent, with the decision on it if it is a tool call.

        Called before the message is forwarded, or instead of forwarding it if the
        decision blocks it.
        """

    def on_upstream_message(self, message: JSONRPCMessage) -> None:
        """See a message from the upstream server, before it goes to the agent."""

    def on_timeout(self, request_id: RequestId) -> None:
        """Learn that an agent request got no upstream answer in time."""

    def on_upstream_lost(self, error_type: str) -> None:
        """Learn that the upstream failed or closed while the agent was connected."""


class UpstreamRelay:
    """Relays one agent-facing MCP session to its own session on one upstream server.

    Single use: one relay per agent MCP session, and ``run()`` is called once. The
    gateway owns the agent-facing side (an SDK server transport) and hands its streams
    to ``run()``.
    """

    def __init__(
        self,
        server: str,
        connect: UpstreamConnector,
        decide: Callable[[JSONRPCMessage], CallDecision | None],
        observer: MessageObserver,
        on_upstream_session: Callable[[str], None],
        timeout: float,
    ) -> None:
        """Create a relay to the server named ``server``, reached through ``connect``.

        Args:
            decide: Called on every agent message before it is forwarded; returns the
                decision on a tool call (None for anything else). A blocked call is
                answered with an error and never reaches the upstream server.
            on_upstream_session: Called once with the upstream server's session ID (its
                ``Mcp-Session-Id``), as soon as the upstream has issued one.
            timeout: Seconds an agent request may wait for its upstream answer before
                the relay answers it with an error instead.
        """
        self._server = server
        self._connect = connect
        self._decide = decide
        self._observer = observer
        self._on_upstream_session = on_upstream_session
        self._timeout = timeout
        # IDs of agent requests sent upstream and not yet answered. If the upstream is
        # lost, these are the requests to fail.
        # Each pending request's method, so a failed tool call can be answered as one.
        self._pending: dict[RequestId, str] = {}
        # Requests already answered with a timeout error; a late upstream answer to one
        # of them is dropped, since the agent has its answer.
        self._timed_out: set[RequestId] = set()
        # Set when the agent's side closes, as opposed to the forwarding being cancelled
        # because the upstream side ended.
        self._agent_closed = anyio.Event()

    async def run(self, agent_read: ReadStream, agent_write: WriteStream) -> None:
        """Forward messages both ways until the agent closes its side.

        If the upstream side ends first (it fails or closes), the agent's MCP session is
        kept open but dead: every request still awaiting an answer, and every request the
        agent sends afterwards, gets a JSON-RPC error. Returning only once the agent's
        side closes is what guarantees those errors are delivered; ending the transport
        sooner could drop them and leave the agent waiting.
        """
        try:
            # For an HTTP upstream, opening the client sends nothing yet: the upstream
            # session starts when the agent's initialize request is forwarded, and
            # closing the client ends it with a DELETE.
            async with self._connect() as (upstream_read, upstream_write, get_id):
                # One task per direction. Each cancels the group when its source closes,
                # so the session ends as soon as either side does.
                async with anyio.create_task_group() as tg:
                    tg.start_soon(
                        self._forward_to_upstream,
                        agent_read,
                        agent_write,
                        upstream_write,
                        tg,
                    )
                    tg.start_soon(
                        self._forward_to_agent,
                        upstream_read,
                        agent_write,
                        get_id,
                        tg.cancel_scope,
                    )
        except Exception as error:
            logger.exception("Upstream %r failed", self._server)
            error_type = _innermost_type(error)
        else:
            if self._agent_closed.is_set():
                return
            logger.warning("Upstream %r closed the session", self._server)
            error_type = "closed"
        self._observer.on_upstream_lost(error_type)
        await self._fail_until_closed(agent_read, agent_write)

    async def _forward_to_upstream(
        self,
        agent_read: ReadStream,
        agent_write: WriteStream,
        upstream_write: WriteStream,
        tg: TaskGroup,
    ) -> None:
        """Forward the agent's messages to the upstream server until the agent closes.

        Runs until ``agent_read`` closes, which happens when the agent ends its session
        (HTTP DELETE) or the gateway ends the hindsight session it belongs to. Either
        way it ends, it cancels ``tg`` to stop the other direction too.

        Each tool call is decided first; a blocked one is answered with an error here
        and goes no further. Each forwarded request's ID is added to the pending set
        before it is sent, so a request is tracked even if the upstream fails while
        sending it, and its timeout starts. Notifications and the agent's responses to
        upstream requests carry no answer, so they are neither tracked nor timed.
        """
        try:
            while True:
                # Receiving is separated from sending so that a closed stream here means
                # the agent's side closed; a closed upstream stream raises from send().
                # The gateway may close the stream itself rather than end it, by
                # terminating the transport.
                try:
                    item = await agent_read.receive()
                except (anyio.EndOfStream, anyio.ClosedResourceError):
                    self._agent_closed.set()
                    return
                if isinstance(item, Exception):
                    # The SDK transport has already answered the agent's HTTP request
                    # with an error; there is no message to forward.
                    logger.warning("Unreadable message from the agent: %s", item)
                    continue
                decision = self._decide(item.message)
                self._observer.on_agent_message(item.message, decision)
                request = item.message.root
                if decision is not None and decision.blocked:
                    assert isinstance(request, JSONRPCRequest)
                    rules = ", ".join(decision.decision.rule_ids)
                    await self._fail(
                        agent_write,
                        request.id,
                        request.method,
                        INTERNAL_ERROR,
                        f"Call blocked by hindsight rule {rules}",
                    )
                    continue
                if isinstance(request, JSONRPCRequest):
                    self._pending[request.id] = request.method
                    tg.start_soon(self._expire, request.id, agent_write)
                # A fresh SessionMessage drops the server-side transport metadata, which
                # means nothing to the upstream client transport.
                await upstream_write.send(SessionMessage(message=item.message))
        finally:
            tg.cancel_scope.cancel()

    async def _forward_to_agent(
        self,
        upstream_read: ReadStream,
        agent_write: WriteStream,
        get_upstream_session_id: Callable[[], str | None],
        scope: anyio.CancelScope,
    ) -> None:
        """Forward upstream messages to the agent until the upstream closes.

        Runs until ``upstream_read`` closes, which happens when the upstream session
        ends, the client transport shuts down or a child process exits, or until the
        agent's side is closed. It then cancels ``scope`` to stop the other direction
        too.

        Responses and errors clear their request from the pending set. Everything else
        the upstream sends, such as notifications or its own requests to the agent, is
        forwarded as well. The SDK's server transport routes each message: responses
        back to the agent's HTTP request with the same ID, the rest to the agent's open
        GET stream.

        The upstream's session ID is reported once it is known, which is when the
        response to ``initialize`` arrives.
        """
        reported_upstream_session = False
        try:
            async for item in upstream_read:
                if not reported_upstream_session:
                    upstream_session_id = get_upstream_session_id()
                    if upstream_session_id is not None:
                        self._on_upstream_session(upstream_session_id)
                        reported_upstream_session = True
                if isinstance(item, Exception):
                    # The client transport reports a message it could not parse or
                    # receive. It does not say which request it belonged to, so it cannot
                    # be passed on as an error; that request will time out.
                    logger.error(
                        "Unreadable message from upstream %r: %s", self._server, item
                    )
                    continue
                answer = item.message.root
                if isinstance(answer, JSONRPCResponse | JSONRPCError):
                    if answer.id in self._timed_out:
                        self._timed_out.discard(answer.id)
                        logger.warning(
                            "Dropped a late answer from %r to request %r",
                            self._server,
                            answer.id,
                        )
                        continue
                    self._pending.pop(answer.id, None)
                self._observer.on_upstream_message(item.message)
                await agent_write.send(SessionMessage(message=item.message))
        except (anyio.ClosedResourceError, anyio.BrokenResourceError):
            # The agent's side was closed by the gateway; nothing more can reach it.
            self._agent_closed.set()
        finally:
            scope.cancel()

    async def _expire(self, request_id: RequestId, agent_write: WriteStream) -> None:
        """Answer a request with a timeout failure if the upstream has not by then."""
        await anyio.sleep(self._timeout)
        method = self._pending.pop(request_id, None)
        if method is None:
            return
        self._timed_out.add(request_id)
        self._observer.on_timeout(request_id)
        await self._fail(
            agent_write,
            request_id,
            method,
            _REQUEST_TIMEOUT,
            f"Upstream MCP server {self._server!r} did not answer within "
            f"{self._timeout:g} s",
        )

    async def _fail_until_closed(
        self, agent_read: ReadStream, agent_write: WriteStream
    ) -> None:
        """Answer agent requests with failures until the agent's side closes.

        Called once the upstream side has ended. Every request still awaiting an answer
        is failed first, then every request the agent sends afterwards, so a dead
        upstream fails each call promptly instead of leaving the agent waiting until its
        own timeout. Each failure carries the original request's ID, so the agent's MCP
        client takes it as that call's answer. Notifications need no answer and are
        dropped.
        """
        message = f"Upstream MCP server {self._server!r} is unavailable"
        for request_id, method in self._pending.items():
            await self._fail(agent_write, request_id, method, INTERNAL_ERROR, message)
        self._pending.clear()
        try:
            async for item in agent_read:
                if isinstance(item, Exception):
                    continue
                request = item.message.root
                if isinstance(request, JSONRPCRequest):
                    await self._fail(
                        agent_write, request.id, request.method, INTERNAL_ERROR, message
                    )
        except anyio.ClosedResourceError:
            # The gateway closed the agent's side; nothing more will arrive.
            pass

    async def _fail(
        self,
        agent_write: WriteStream,
        request_id: RequestId,
        method: str,
        code: int,
        message: str,
    ) -> None:
        """Answer one agent request that the relay itself fails, if the agent can hear.

        A tool call is answered with a tool error result (``isError``), the way the MCP
        spec has tool execution errors reported, so the model sees the failure and the
        agent carries on: frameworks such as LangChain's abort the whole run on a
        JSON-RPC error. Any other request gets a JSON-RPC error with ``code``.
        """
        if method == "tools/call":
            result = {"content": [{"type": "text", "text": message}], "isError": True}
            answer = JSONRPCResponse(jsonrpc="2.0", id=request_id, result=result)
        else:
            answer = JSONRPCError(
                jsonrpc="2.0",
                id=request_id,
                error=ErrorData(code=code, message=message),
            )
        try:
            await agent_write.send(SessionMessage(message=JSONRPCMessage(answer)))
        except (anyio.ClosedResourceError, anyio.BrokenResourceError):
            logger.debug(
                "Agent session closed before request %r was answered", request_id
            )


def _innermost_type(error: BaseException) -> str:
    """Return the type name of the first leaf exception, unwrapping exception groups.

    Task groups wrap the real failure (for example an ``httpx.ConnectError``) in groups.
    """
    while isinstance(error, BaseExceptionGroup) and error.exceptions:
        error = error.exceptions[0]
    return type(error).__name__
