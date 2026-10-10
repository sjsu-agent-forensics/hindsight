"""Relay one agent-facing MCP session to its own persistent upstream session.

Every JSON-RPC message is forwarded as received, in both directions. The relay never
interprets tool names, descriptors, arguments or results, so what the agent sees is what
the upstream server sent. Upstream content is untrusted and is passed through, not acted
on.
"""

import logging

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


class UpstreamRelay:
    """Stand-in for an MCP server that relays each session to one upstream server.

    ``StreamableHTTPSessionManager`` drives an MCP server only through
    ``create_initialization_options()`` and ``run()``. Providing both lets the SDK
    manage the agent-facing HTTP sessions (session IDs, DELETE, idle expiry) while this
    class forwards the messages. Each agent session gets its own upstream session,
    opened when the agent initializes and closed when either side ends.
    """

    def __init__(self, upstream: HttpUpstream) -> None:
        self._upstream = upstream

    def create_initialization_options(self) -> None:
        """Return nothing: the upstream server answers ``initialize`` itself."""
        return None

    async def run(
        self,
        agent_read: MemoryObjectReceiveStream[SessionMessage | Exception],
        agent_write: MemoryObjectSendStream[SessionMessage],
        initialization_options: None,
        stateless: bool = False,
    ) -> None:
        """Forward messages both ways until the agent or the upstream server closes.

        If the upstream connection fails, every agent request still awaiting an answer
        gets a JSON-RPC error instead of hanging, and the session ends.
        """
        # IDs of agent requests forwarded upstream but not yet answered. Both forwarding
        # tasks update it; if the upstream fails, these are the requests to fail.
        pending: set[RequestId] = set()
        try:
            # Opening the client sends nothing yet: the upstream session starts when the
            # agent's initialize request is forwarded, and closing the client ends it
            # with a DELETE.
            async with streamable_http_client(self._upstream.url) as (
                upstream_read,
                upstream_write,
                _,
            ):
                # One task per direction. Each cancels the group when its source closes,
                # so the session ends as soon as either side does.
                async with anyio.create_task_group() as tg:
                    tg.start_soon(
                        self._forward_to_upstream,
                        agent_read,
                        upstream_write,
                        pending,
                        tg.cancel_scope,
                    )
                    tg.start_soon(
                        self._forward_to_agent,
                        upstream_read,
                        agent_write,
                        pending,
                        tg.cancel_scope,
                    )
        except Exception:
            logger.exception("Upstream %r failed", self._upstream.name)
            await self._fail_pending(agent_write, pending)

    async def _forward_to_upstream(
        self,
        agent_read: MemoryObjectReceiveStream[SessionMessage | Exception],
        upstream_write: MemoryObjectSendStream[SessionMessage],
        pending: set[RequestId],
        scope: anyio.CancelScope,
    ) -> None:
        """Forward the agent's messages to the upstream server until the agent closes.

        Runs until ``agent_read`` closes, which the SDK does when the agent ends its
        session (HTTP DELETE) or the session expires idle. It then cancels ``scope`` to
        stop the other direction too.

        Each request's ID is added to ``pending`` before it is sent, so a request is
        tracked even if the upstream fails while sending it. Notifications and the
        agent's responses to upstream requests carry no answer, so they are not tracked.
        """
        try:
            async for item in agent_read:
                if isinstance(item, Exception):
                    # The SDK transport has already answered the agent's HTTP request
                    # with an error; there is no message to forward.
                    logger.warning("Unreadable message from the agent: %s", item)
                    continue
                if isinstance(item.message.root, JSONRPCRequest):
                    pending.add(item.message.root.id)
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
        scope: anyio.CancelScope,
    ) -> None:
        """Forward the upstream server's messages to the agent until the upstream
        closes.

        Runs until ``upstream_read`` closes, which happens when the upstream session
        ends or the client transport shuts down. It then cancels ``scope`` to stop the
        other direction too.

        Responses and errors remove their request's ID from ``pending``. Everything else
        the upstream sends, such as notifications or its own requests to the agent, is
        forwarded as well. The SDK's server transport routes each message: responses
        back to the agent's HTTP request with the same ID, the rest to the agent's open
        GET stream.
        """
        try:
            async for item in upstream_read:
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
                await agent_write.send(SessionMessage(message=item.message))
        finally:
            scope.cancel()

    async def _fail_pending(
        self,
        agent_write: MemoryObjectSendStream[SessionMessage],
        pending: set[RequestId],
    ) -> None:
        """Answer every unanswered agent request with a JSON-RPC error.

        Called after the upstream connection fails. Without it, the agent would wait on
        those requests until its own timeout. Each error carries the original request's
        ID, so the agent's MCP client raises it as that call's failure.

        Stops early if the agent's session has already closed, since nothing can reach
        the agent any more.
        """
        error = ErrorData(
            code=INTERNAL_ERROR,
            message=f"Upstream MCP server {self._upstream.name!r} is unavailable",
        )
        for request_id in pending:
            message = JSONRPCMessage(
                JSONRPCError(jsonrpc="2.0", id=request_id, error=error)
            )
            try:
                await agent_write.send(SessionMessage(message=message))
            except (anyio.ClosedResourceError, anyio.BrokenResourceError):
                logger.warning(
                    "Agent session closed before request %r could be failed",
                    request_id,
                )
                return
