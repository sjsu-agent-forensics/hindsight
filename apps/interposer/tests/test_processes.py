"""Phase 4: stdio servers run as each session's own children, with timeouts and crashes.

Uses the faults test server (``apps/mcp-servers/faults``) and skips if it is not set up.
Slow (about a minute): FastMCP servers take seconds to start, and the timeout test waits
out a real timeout.
"""

import os
import signal
import sys
import time
from contextlib import AsyncExitStack
from pathlib import Path

import anyio
import httpx
import pytest
import trace_schema as ts

from interposer.config import load_config
from interposer.testing import open_session, run, running_gateway

pytestmark = pytest.mark.slow

FAULTS_PROJECT = Path(__file__).resolve().parents[2] / "mcp-servers" / "faults"

# Longer than a FastMCP server takes to answer its first request on a slow machine (about
# 6 s measured on Windows), so startup alone never times out.
UPSTREAM_TIMEOUT = 10.0


@pytest.fixture(scope="module")
def gateway(tmp_path_factory):
    config = tmp_path_factory.mktemp("config") / "servers.toml"
    config.write_text(
        f'[servers.faults]\nproject = "{FAULTS_PROJECT.as_posix()}"\n'
        'program = "faults-server"\n',
        encoding="utf-8",
    )
    try:
        upstreams = load_config(config)
    except ValueError as error:
        pytest.skip(f"faults server not set up: {error}")
    with running_gateway(upstreams, upstream_timeout=UPSTREAM_TIMEOUT) as gateway:
        yield gateway


def _pid(result) -> int:
    return int(result.content[0].text)


def _alive(pid: int) -> bool:
    """Return whether a process is running, without signalling it."""
    if sys.platform == "win32":
        import ctypes

        kernel32 = ctypes.windll.kernel32
        handle = kernel32.OpenProcess(0x1000, False, pid)  # QUERY_LIMITED_INFORMATION
        if not handle:
            return False
        exit_code = ctypes.c_ulong()
        kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code))
        kernel32.CloseHandle(handle)
        return exit_code.value == 259  # STILL_ACTIVE
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


def _gone_within(pid: int, seconds: float) -> bool:
    deadline = time.monotonic() + seconds
    while _alive(pid):
        if time.monotonic() > deadline:
            return False
        time.sleep(0.1)
    return True


def _kill(pid: int) -> None:
    """Kill a process outright, as a crash would."""
    # On Windows, any signal but the console ones terminates the process.
    os.kill(pid, signal.SIGTERM if sys.platform == "win32" else signal.SIGKILL)


def _events(trace_dir: Path, event_type: str) -> list[dict]:
    events = ts.read_events(trace_dir / ts.PROXY_TRACE_FILE)
    return [e for e in events if e[ts.EVENT_TYPE] == event_type]


def test_each_session_gets_its_own_child_until_it_ends(gateway):
    a, b = gateway.register(), gateway.register()

    async def pids() -> list[int]:
        async with AsyncExitStack() as stack:
            return [
                _pid(
                    await (
                        await open_session(stack, gateway.mcp_url(s.id, "faults"))
                    ).call_tool("pid", {})
                )
                for s in (a, b)
            ]

    pid_a, pid_b = run(pids)
    assert pid_a != pid_b
    gateway.end(a.id)
    assert _gone_within(pid_a, 6)
    assert _alive(pid_b)
    gateway.end(b.id)
    assert _gone_within(pid_b, 6)


def test_a_second_mcp_session_to_a_stdio_server_is_refused_and_logged(gateway):
    session = gateway.register()

    async def open_twice() -> int:
        async with AsyncExitStack() as stack:
            await open_session(stack, gateway.mcp_url(session.id, "faults"))
            second = await anyio.to_thread.run_sync(
                lambda: httpx.post(
                    gateway.mcp_url(session.id, "faults"),
                    json={"jsonrpc": "2.0", "id": 1, "method": "initialize"},
                    headers={"Accept": "application/json, text/event-stream"},
                )
            )
            return second.status_code

    assert run(open_twice) == 409
    assert "stdio_server_in_use" in gateway.refusals()
    gateway.end(session.id)


def test_an_unanswered_call_times_out_without_being_a_block(gateway):
    session = gateway.register()

    async def call_slow() -> None:
        async with AsyncExitStack() as stack:
            mcp = await open_session(stack, gateway.mcp_url(session.id, "faults"))
            await mcp.call_tool("slow", {"seconds": UPSTREAM_TIMEOUT + 5})

    started = time.monotonic()
    with pytest.raises(Exception) as raised:
        run(call_slow)
    elapsed = time.monotonic() - started
    gateway.end(session.id)
    assert "did not answer" in repr(raised.value)
    assert elapsed < UPSTREAM_TIMEOUT * 2
    [timeout] = _events(session.trace_dir, "timeout")
    assert timeout[ts.TOOL_NAME] == "slow" and timeout[ts.ERROR_TYPE] == "timeout"
    assert [d[ts.DECISION] for d in _events(session.trace_dir, "decision")] == ["allow"]
    assert _events(session.trace_dir, "response") == []


def test_after_a_child_is_killed_every_call_fails_with_one_crash_event(gateway):
    session = gateway.register()

    async def kill_then_call() -> list[str]:
        outcomes = []
        async with AsyncExitStack() as stack:
            mcp = await open_session(stack, gateway.mcp_url(session.id, "faults"))
            pid = _pid(await mcp.call_tool("pid", {}))
            _kill(pid)
            await anyio.to_thread.run_sync(_gone_within, pid, 6)
            for _ in range(2):
                started = time.monotonic()
                try:
                    with anyio.fail_after(5):
                        await mcp.call_tool("pid", {})
                    outcomes.append("answered")
                except Exception as error:
                    fast = time.monotonic() - started < 1
                    outcomes.append(
                        "failed fast"
                        if fast and "unavailable" in str(error)
                        else repr(error)
                    )
        return outcomes

    assert run(kill_then_call) == ["failed fast", "failed fast"]
    gateway.end(session.id)
    assert len(_events(session.trace_dir, "crash")) == 1
