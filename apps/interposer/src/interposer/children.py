"""Local stdio MCP servers, run as each session's own child processes.

Each hindsight session gets its own child for every stdio server, started when the
session is registered and stopped when it ends, so no server state carries from one run
to the next. A child is never restarted: if it dies, calls to it fail for the rest of
the session.

All spawning goes through a ``ProcessLauncher``: server config in, stdio streams out. The
one implementation runs a plain child process. A containerized server (``docker run -i
--rm <image>``) needs only a different command in the config, not a new launcher.
"""

import logging
from collections.abc import AsyncIterator, Callable
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from typing import Protocol

import anyio
from anyio.abc import TaskStatus
from anyio.streams.memory import MemoryObjectReceiveStream, MemoryObjectSendStream
from mcp.client.stdio import StdioServerParameters, stdio_client
from mcp.shared.message import SessionMessage

from interposer.config import StdioUpstream

logger = logging.getLogger(__name__)

ReadStream = MemoryObjectReceiveStream[SessionMessage | Exception]
WriteStream = MemoryObjectSendStream[SessionMessage]

# How long stopping a child waits for it to exit. The SDK itself closes the child's
# stdin, waits 2 s, then kills its whole process tree.
_STOP_TIMEOUT_SECONDS = 10


class ProcessLauncher(Protocol):
    """Starts a stdio MCP server and gives back its message streams."""

    def open(
        self, upstream: StdioUpstream
    ) -> AbstractAsyncContextManager[tuple[ReadStream, WriteStream]]:
        """Start the server; on exit, stop it and everything it started."""


class SubprocessLauncher:
    """Runs each server as a plain child process of the gateway.

    The SDK's stdio client puts the child in a Windows job object (or, on POSIX, its own
    process group), so stopping it also stops anything the child started.
    """

    def open(
        self, upstream: StdioUpstream
    ) -> AbstractAsyncContextManager[tuple[ReadStream, WriteStream]]:
        """Start the server with the SDK's stdio client."""
        parameters = StdioServerParameters(
            command=upstream.command, args=upstream.args, cwd=upstream.cwd
        )
        return stdio_client(parameters)


class StdioChild:
    """One session's child process for one stdio server.

    A child can serve one MCP session: a stdio server has no way to start a second one
    on the same process. ``claimed`` records that an MCP session has taken it.
    """

    def __init__(self, upstream: StdioUpstream, launcher: ProcessLauncher) -> None:
        self.upstream = upstream
        self.claimed = False
        self._launcher = launcher
        self._streams: tuple[ReadStream, WriteStream] | None = None
        self._stop = anyio.Event()
        self._stopped = anyio.Event()

    async def run(self, *, task_status: TaskStatus[None] = anyio.TASK_STATUS_IGNORED):
        """Start the child and keep it until ``stop()``; run in a task group.

        Reports started once the process is running, so a failure to start (for example
        a missing program) is raised to whoever started this task. A failure after that
        is logged, not raised: it must not take down the task group, which the whole
        gateway shares. The relay sees the child's streams close and reports the crash.
        """
        started = False
        try:
            async with self._launcher.open(self.upstream) as streams:
                self._streams = streams
                started = True
                task_status.started()
                await self._stop.wait()
        except Exception:
            if not started:
                raise
            logger.exception("MCP server %r failed", self.upstream.name)
        finally:
            self._stopped.set()

    def connector(
        self,
    ) -> AbstractAsyncContextManager[
        tuple[ReadStream, WriteStream, Callable[[], str | None]]
    ]:
        """Return the child's streams in the shape the relay connects to upstreams with.

        The streams stay open when the relay is done with them; the child lives until
        ``stop()``. A stdio server has no session ID, so the ID callback returns None.
        """
        return self._connect()

    @asynccontextmanager
    async def _connect(
        self,
    ) -> AsyncIterator[tuple[ReadStream, WriteStream, Callable[[], str | None]]]:
        assert self._streams is not None, "the child has not started"
        read, write = self._streams
        yield read, write, lambda: None

    async def stop(self) -> None:
        """Stop the child and wait for it to exit, up to a timeout."""
        self._stop.set()
        with anyio.move_on_after(_STOP_TIMEOUT_SECONDS) as scope:
            await self._stopped.wait()
        if scope.cancelled_caught:
            logger.warning(
                "MCP server %r did not exit within %d s",
                self.upstream.name,
                _STOP_TIMEOUT_SECONDS,
            )
