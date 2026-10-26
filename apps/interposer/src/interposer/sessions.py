"""Hindsight sessions: one per agent run, registered and ended by the launcher.

A hindsight session groups everything one run does. The agent opens one MCP session per
server path under the session's prefix (``/s/<session-id>/mcp/<server>``), so one
hindsight session maps to several MCP sessions, which the session records. Each session
has one proxy trace, which is complete once the session has ended.
"""

import logging
import time
import uuid
from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
from typing import Literal, get_args

import anyio
import trace_schema as ts
from anyio.abc import TaskGroup
from mcp.server.streamable_http import StreamableHTTPServerTransport
from provenance import ExtractionConfig, ProvenanceIndex
from rule_engine import Rule
from rule_engine.sources import RuleSource

from interposer.children import ProcessLauncher, StdioChild
from interposer.config import StdioUpstream
from interposer.tracing import SessionTrace, TraceWriter

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
    """One registered hindsight session, its stdio children and its MCP sessions.

    ``channels`` keeps every MCP session ever opened under this session, including ones
    already closed, so the mapping survives for the record. ``children`` holds the
    session's own process for each stdio server, keyed by server name. ``index`` is the
    session's provenance index, shared by all its MCP sessions; it is discarded (set to
    None) when the session ends. ``rules`` is the rule set read at registration, which
    the session keeps for its whole life.
    """

    id: str
    trusted_inputs: list[TrustedInput]
    trace: SessionTrace
    index: ProvenanceIndex | None
    rules: list[Rule] = field(default_factory=list)
    children: dict[str, StdioChild] = field(default_factory=dict)
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
        """Return the session's ID, how it ended, and its MCP sessions by server."""
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


class ChildStartError(Exception):
    """A stdio server's process could not be started for a new session."""

    def __init__(self, server: str, cause: Exception) -> None:
        super().__init__(f"Could not start MCP server {server!r}: {cause}")
        self.server = server


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

    def __init__(
        self,
        idle_timeout: float,
        trace_writer: TraceWriter,
        stdio_upstreams: dict[str, StdioUpstream],
        launcher: ProcessLauncher,
        extraction: ExtractionConfig,
        rule_source: RuleSource,
    ) -> None:
        self._sessions: dict[str, Session] = {}
        # IDs whose registration is starting children, so a concurrent registration of
        # the same ID is refused too.
        self._registering: set[str] = set()
        self._idle_timeout = idle_timeout
        self._trace_writer = trace_writer
        self._stdio_upstreams = stdio_upstreams
        self._launcher = launcher
        self._extraction = extraction
        self._rule_source = rule_source
        # Set by the app's lifespan; children run in it so they outlive the request
        # that registered their session.
        self.task_group: TaskGroup | None = None

    async def register(
        self, session_id: str, trusted_inputs: list[TrustedInput]
    ) -> Session:
        """Register a new session: read its rules, start its children, index its inputs.

        The session runs under the rules in force now, for its whole life. Every stdio
        server gets its own child for the session, and the session gets a provenance
        index holding only its trusted inputs.

        Raises:
            SessionExistsError: The ID was registered before.
            RuleError: A stored rule is malformed. Nothing is started, and the session
                is not registered: running without a deny rule would be a silent hole.
            ChildStartError: A stdio server could not be started. Any children already
                started are stopped, and the session is not registered.
        """
        if session_id in self._sessions or session_id in self._registering:
            raise SessionExistsError(session_id)
        rules = self._rule_source.load_rules()
        self._registering.add(session_id)
        try:
            children = await self._start_children()
        finally:
            self._registering.discard(session_id)
        index = ProvenanceIndex(self._extraction)
        for trusted in trusted_inputs:
            index.add_trusted(trusted.content)
        session = Session(
            id=session_id,
            trusted_inputs=trusted_inputs,
            trace=SessionTrace(session_id, self._trace_writer),
            index=index,
            rules=rules,
            children=children,
        )
        self._sessions[session_id] = session
        # Replay rebuilds the session's index and decisions from this event: its trusted
        # inputs, its rules and its extraction settings.
        session.trace.emit(
            "registration",
            {
                ts.TRUSTED_INPUTS: [asdict(trusted) for trusted in trusted_inputs],
                ts.RULES: [rule.to_dict() for rule in rules],
                ts.EXTRACTION: self._extraction.to_dict(),
            },
        )
        logger.info(
            "Registered session %s with %d trusted inputs and %d rules",
            session_id,
            len(trusted_inputs),
            len(rules),
        )
        return session

    def lookup(self, session_id: str) -> Session | None:
        """Return the session with this ID, active or ended, or None if unknown."""
        return self._sessions.get(session_id)

    async def end(self, session: Session, ended_by: EndedBy) -> None:
        """End a session: close its MCP sessions, stop its children, complete its trace.

        Returns once its upstream sessions are closed and its children have exited (or
        after a timeout for any that do not) and every trace event is written, so the
        trace is complete when this returns. Safe to call more than once and
        concurrently: the first call decides ``ended_by``, and every call waits for the
        same close.
        """
        if session.ended_by is None:
            session.ended_by = ended_by
            logger.info("Ending session %s (%s)", session.id, ended_by)
            await self._close_channels(session)
            with anyio.CancelScope(shield=True):
                # After the channels, so a child's exit is never mistaken for a crash.
                await _stop_children(session.children.values())
                # Last: closing channels can still produce events.
                await self._trace_writer.flush()
            # No call can reach this session any more, so its index is no longer needed.
            session.index = None
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

    async def _start_children(self) -> dict[str, StdioChild]:
        """Start one child per stdio server, or none at all if any fails to start."""
        assert self.task_group is not None, "the app's lifespan has not started"
        started: dict[str, StdioChild] = {}
        for name, upstream in self._stdio_upstreams.items():
            child = StdioChild(upstream, self._launcher)
            try:
                await self.task_group.start(child.run)
            except Exception as error:
                with anyio.CancelScope(shield=True):
                    await _stop_children(started.values())
                raise ChildStartError(name, error) from error
            started[name] = child
        return started

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


async def _stop_children(children: Iterable[StdioChild]) -> None:
    """Stop children concurrently and wait for each to exit, up to its timeout."""

    async with anyio.create_task_group() as tg:
        for child in children:
            tg.start_soon(child.stop)
