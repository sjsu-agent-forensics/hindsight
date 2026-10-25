"""Relay one agent-facing MCP session to its own persistent upstream session.

Every JSON-RPC message is forwarded as received, in both directions. The relay never
changes tool names, descriptors, arguments or results, so what the agent sees is what
the upstream server sent. It shows each message to an observer (the tracer) before
forwarding it. Upstream content is untrusted and is passed through, not acted on.
"""

import logging
from collections.abc import Callable
from typing import Protocol

import anyio
from anyio.streams.memory import MemoryObjectReceiveStream, MemoryObjectSendStream
from mcp.client.streamable_http import streamable_http_client
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

from interposer.config import HttpUpstream

logger = logging.getLogger(__name__)


class MessageObserver(Protocol):
    """Sees every relayed message just before it is forwarded.

    Called on the forwarding path, so it must not block. It cannot change or drop the
    message.
    """

    def on_agent_message(self, message: JSONRPCMessage) -> None:
        """See a message from the agent, before it goes upstream."""

    def on_upstream_message(self, message: JSONRPCMessage) -> None:
        """See a message from the upstream server, before it goes to the agent."""


class UpstreamRelay:
    """Relays one agent-facing MCP session to its own session on one upstream server.

    The gateway owns the agent-facing side (an SDK server transport) and hands its
    streams to ``run()``. The upstream session is opened when the agent initializes and
    closed when either side ends.
    """

    def __init__(
        self,
        upstream: HttpUpstream,
        observer: MessageObserver,
        on_upstream_session: Callable[[str], None],
    ) -> None:
        """Create a relay to ``upstream``.

        ``on_upstream_session`` is called once with the upstream server's session ID
        (its ``Mcp-Session-Id``), as soon as the upstream has issued one.
        """
        self._upstream = upstream
        self._observer = observer
        self._on_upstream_session = on_upstream_session

    async def run(
        self,
        agent_read: MemoryObjectReceiveStream[SessionMessage | Exception],
        agent_write: MemoryObjectSendStream[SessionMessage],
    ) -> None:
        """Forward messages both ways until the agent closes its side.

        If the upstream side ends first (it fails or closes), the agent's MCP session is
        kept open but dead: every request still awaiting an answer, and every request the
        agent sends afterwards, gets a JSON-RPC error. Returning only once the agent's
        side closes is what guarantees those errors are delivered; ending the transport
        sooner could drop them and leave the agent waiting.
        """
        # IDs of agent requests forwarded upstream but not yet answered. Both forwarding
        # tasks update it; if the upstream fails, these are the requests to fail.
        pending: set[RequestId] = set()
        # Set when the agent's side closes, as opposed to the forwarding being cancelled
        # because the upstream side ended.
        agent_closed = anyio.Event()
        try:
            # Opening the client sends nothing yet: the upstream session starts when the
            # agent's initialize request is forwarded, and closing the client ends it
            # with a DELETE.
            async with streamable_http_client(self._upstream.url) as (
                upstream_read,
                upstream_write,
                get_upstream_session_id,
            ):
                # One task per direction. Each cancels the group when its source closes,
                # so the session ends as soon as either side does.
                async with anyio.create_task_group() as tg:
                    tg.start_soon(
                        self._forward_to_upstream,
                        agent_read,
                        upstream_write,
                        pending,
                        agent_closed,
                        tg.cancel_scope,
                    )
                    tg.start_soon(
                        self._forward_to_agent,
                        upstream_read,
                        agent_write,
                        pending,
                        get_upstream_session_id,
                        agent_closed,
                        tg.cancel_scope,
                    )
        except Exception:
            logger.exception("Upstream %r failed", self._upstream.name)
        else:
            if agent_closed.is_set():
                return
            logger.warning("Upstream %r closed the session", self._upstream.name)
        await self._fail_until_closed(agent_read, agent_write, pending)

    async def _forward_to_upstream(
        self,
        agent_read: MemoryObjectReceiveStream[SessionMessage | Exception],
        upstream_write: MemoryObjectSendStream[SessionMessage],
        pending: set[RequestId],
        agent_closed: anyio.Event,
        scope: anyio.CancelScope,
    ) -> None:
        """Forward the agent's messages to the upstream server until the agent closes.

        Runs until ``agent_read`` closes, which happens when the agent ends its session
        (HTTP DELETE) or the gateway ends the hindsight session it belongs to; it then
        sets ``agent_closed``. Either way it ends, it cancels ``scope`` to stop the
        other direction too.

        Each request's ID is added to ``pending`` before it is sent, so a request is
        tracked even if the upstream fails while sending it. Notifications and the
        agent's responses to upstream requests carry no answer, so they are not tracked.
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
                    agent_closed.set()
                    return
                if isinstance(item, Exception):
                    # The SDK transport has already answered the agent's HTTP request
                    # with an error; there is no message to forward.
                    logger.warning("Unreadable message from the agent: %s", item)
                    continue
                if isinstance(item.message.root, JSONRPCRequest):
                    pending.add(item.message.root.id)
                self._observer.on_agent_message(item.message)
                # A fresh SessionMessage drops the server-side transport metadata, which
                # means nothing to the upstream client transport.
                await upstream_write.send(SessionMessage(message=item.message))
        finally:
            scope.cancel()

    async def _forward_to_agent(
        self,
        upstream_read: MemoryObjectReceiveStream[SessionMessage | Exception],
        agent_write: MemoryObjectSendStream[SessionMessage],
        pending: set[RequestId],
        get_upstream_session_id: Callable[[], str | None],
        agent_closed: anyio.Event,
        scope: anyio.CancelScope,
    ) -> None:
        """Forward upstream messages to the agent until the upstream closes.

        Runs until ``upstream_read`` closes, which happens when the upstream session
        ends or the client transport shuts down, or until the agent's side is closed. It
        then cancels ``scope`` to stop the other direction too.

        Responses and errors remove their request's ID from ``pending``. Everything else
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
                    # be passed on as an error; that request may go unanswered.
                    logger.error(
                        "Unreadable message from upstream %r: %s",
                        self._upstream.name,
                        item,
                    )
                    continue
                if isinstance(item.message.root, JSONRPCResponse | JSONRPCError):
                    pending.discard(item.message.root.id)
                self._observer.on_upstream_message(item.message)
                await agent_write.send(SessionMessage(message=item.message))
        except (anyio.ClosedResourceError, anyio.BrokenResourceError):
            # The agent's side was closed by the gateway; nothing more can reach it.
            agent_closed.set()
        finally:
            scope.cancel()

    async def _fail_until_closed(
        self,
        agent_read: MemoryObjectReceiveStream[SessionMessage | Exception],
        agent_write: MemoryObjectSendStream[SessionMessage],
        pending: set[RequestId],
    ) -> None:
        """Answer agent requests with errors until the agent's side closes.

        Called once the upstream side has ended. Every request still awaiting an answer
        gets an error first, then every request the agent sends afterwards, so a dead
        upstream fails each call promptly instead of leaving the agent waiting until its
        own timeout. Each error carries the original request's ID, so the agent's MCP
        client raises it as that call's failure. Notifications need no answer and are
        dropped.
        """
        error = ErrorData(
            code=INTERNAL_ERROR,
            message=f"Upstream MCP server {self._upstream.name!r} is unavailable",
        )

        async def fail(request_id: RequestId) -> None:
            message = JSONRPCMessage(
                JSONRPCError(jsonrpc="2.0", id=request_id, error=error)
            )
            await agent_write.send(SessionMessage(message=message))

        try:
            for request_id in pending:
                await fail(request_id)
            pending.clear()
            async for item in agent_read:
                if not isinstance(item, Exception) and isinstance(
                    item.message.root, JSONRPCRequest
                ):
                    await fail(item.message.root.id)
        except (anyio.ClosedResourceError, anyio.BrokenResourceError):
            # The agent's side closed; nothing more can reach it.
            pass
