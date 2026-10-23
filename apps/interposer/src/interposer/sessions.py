"""Hindsight sessions: one per agent run, registered and ended by the launcher.

A hindsight session groups everything one run does. The agent opens one MCP session per
server path under the session's prefix (``/s/<session-id>/mcp/<server>``), so one
hindsight session maps to several MCP sessions, which the session records.
"""

import logging
import time
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Literal, get_args

import anyio
from mcp.server.streamable_http import StreamableHTTPServerTransport

logger = logging.getLogger(__name__)

TrustedInputKind = Literal["user_prompt", "system_prompt", "attached_file", "turn"]
TRUSTED_INPUT_KINDS: frozenset[str] = frozenset(get_args(TrustedInputKind))

EndedBy = Literal["launcher", "idle_timeout", "shutdown"]

# How long ending a session waits for its upstream sessions to close before giving up on
# them. An unreachable upstream would otherwise hold the launcher's end call open.
_CLOSE_TIMEOUT_SECONDS = 10


@dataclass(frozen=True)
class TrustedInput:
    """Content the user supplied, which the agent may act on (from the registration)."""

    kind: TrustedInputKind
    content: str


@dataclass
class McpChannel:
    """One agent-facing MCP session on one server path, relayed to one upstream session.

    ``transport`` is the gateway's side of the agent's MCP session; its session ID is
    the ``Mcp-Session-Id`` the agent sends. ``upstream_session_id`` is the ID the
    upstream server issued for the relayed session, once it has issued one (stdio
    servers never do).
    """

    server: str
    transport: StreamableHTTPServerTransport
    upstream_session_id: str | None = None
    closed: anyio.Event = field(default_factory=anyio.Event)

    @property
    def mcp_session_id(self) -> str:
        """Return the ``Mcp-Session-Id`` the gateway issued to the agent."""
        assert self.transport.mcp_session_id is not None
        return self.transport.mcp_session_id


@dataclass
class Session:
    """One registered hindsight session and the MCP sessions opened under it.

    ``channels`` keeps every MCP session ever opened under this session, including ones
    already closed, so the mapping survives for the record.
    """

    id: str
    trusted_inputs: list[TrustedInput]
    channels: dict[str, McpChannel] = field(default_factory=dict)
    ended_by: EndedBy | None = None
    closed: anyio.Event = field(default_factory=anyio.Event)
    last_activity: float = field(default_factory=time.monotonic)
    in_flight: int = 0

    @contextmanager
    def activity(self, counts_as_in_flight: bool) -> Iterator[None]:
        """Mark a request on this session as running, for the idle timeout.

        A long-lived GET stream should pass ``counts_as_in_flight=False``: the agent
        holds one open for server notifications for its whole session, so counting it
        would keep every connected session from ever going idle.
        """
        self.last_activity = time.monotonic()
        if counts_as_in_flight:
            self.in_flight += 1
        try:
            yield
        finally:
            if counts_as_in_flight:
                self.in_flight -= 1
            self.last_activity = time.monotonic()

    def summary(self) -> dict:
        """Return the session's ID, how it ended, and its MCP sessions grouped by
        server."""
        mcp_sessions: dict[str, list[dict]] = {}
        for channel in self.channels.values():
            mcp_sessions.setdefault(channel.server, []).append(
                {
                    "mcp_session_id": channel.mcp_session_id,
                    "upstream_session_id": channel.upstream_session_id,
                }
            )
        return {
            "session_id": self.id,
            "ended_by": self.ended_by,
            "mcp_sessions": mcp_sessions,
        }


class SessionExistsError(Exception):
    """A session with this ID was already registered, whether or not it has ended."""


def normalize_session_id(raw: str) -> str | None:
    """Return the canonical lowercase form of a UUID session ID, or None if invalid."""
    try:
        return str(uuid.UUID(raw))
    except ValueError:
        return None


class SessionRegistry:
    """Every hindsight session the gateway has seen, active or ended.

    Ended sessions are kept, so a late request can be refused as coming from an ended
    session rather than an unknown one, and an ID is never registered twice.
    """

    def __init__(self, idle_timeout: float) -> None:
        self._sessions: dict[str, Session] = {}
        self._idle_timeout = idle_timeout

    def register(self, session_id: str, trusted_inputs: list[TrustedInput]) -> Session:
        """Register a new session.

        Raises:
            SessionExistsError: The ID was registered before.
        """
        if session_id in self._sessions:
            raise SessionExistsError(session_id)
        session = Session(id=session_id, trusted_inputs=trusted_inputs)
        self._sessions[session_id] = session
        logger.info(
            "Registered session %s with %d trusted inputs",
            session_id,
            len(trusted_inputs),
        )
        return session

    def lookup(self, session_id: str) -> Session | None:
        """Return the session with this ID, active or ended, or None if never
        registered."""
        return self._sessions.get(session_id)

    async def end(self, session: Session, ended_by: EndedBy) -> None:
        """End a session and close every MCP session under it.

        Returns once its upstream sessions are closed, or after a timeout if an upstream
        does not respond. Safe to call more than once and concurrently: the first call
        decides ``ended_by``, and every call waits for the same close.
        """
        if session.ended_by is None:
            session.ended_by = ended_by
            logger.info("Ending session %s (%s)", session.id, ended_by)
            await self._close_channels(session)
            session.closed.set()
        await session.closed.wait()

    async def end_all(self) -> None:
        """End every active session, as the gateway shuts down."""
        for session in list(self._sessions.values()):
            await self.end(session, "shutdown")

    async def expire_idle(self) -> None:
        """End sessions idle for longer than the idle timeout, checking until cancelled.

        A session is idle when no request is in flight and none has started or finished
        within the timeout. This ends sessions whose launcher never ended them.
        """
        interval = min(self._idle_timeout / 4, 30)
        while True:
            await anyio.sleep(interval)
            now = time.monotonic()
            for session in list(self._sessions.values()):
                if (
                    session.ended_by is None
                    and session.in_flight == 0
                    and now - session.last_activity > self._idle_timeout
                ):
                    await self.end(session, "idle_timeout")

    async def _close_channels(self, session: Session) -> None:
        # Terminating a transport closes the relay's streams, which ends the relay and
        # with it the upstream session. Shielded so a cancelled caller still closes them.
        with anyio.CancelScope(shield=True):
            for channel in session.channels.values():
                await channel.transport.terminate()
            with anyio.move_on_after(_CLOSE_TIMEOUT_SECONDS) as scope:
                for channel in session.channels.values():
                    await channel.closed.wait()
            if scope.cancelled_caught:
                logger.warning(
                    "Session %s: upstream sessions did not close within %d s",
                    session.id,
                    _CLOSE_TIMEOUT_SECONDS,
                )
