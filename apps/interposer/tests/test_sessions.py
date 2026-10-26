"""Phase 2: every connection belongs to a registered session; nothing runs without."""

import time
import uuid
from contextlib import AsyncExitStack

import anyio
import httpx
import pytest

from interposer.config import HttpUpstream
from interposer.testing import (
    free_port,
    open_session,
    run,
    running_gateway,
    running_test_server,
)


@pytest.fixture(scope="module")
def gateway():
    with running_test_server() as url:
        # One server under two names, so one session can use two server paths; and a
        # server that is down, since nothing listens on a port just found free.
        upstreams = {
            "tools": HttpUpstream("tools", url),
            "tools_b": HttpUpstream("tools_b", url),
            "down": HttpUpstream("down", f"http://127.0.0.1:{free_port()}/mcp"),
        }
        with running_gateway(upstreams) as gateway:
            yield gateway


async def _initialize(url: str) -> None:
    with anyio.fail_after(10):
        async with AsyncExitStack() as stack:
            await open_session(stack, url)


def test_a_request_without_a_session_prefix_is_refused_and_logged(gateway):
    response = httpx.post(f"{gateway.url}/mcp/tools", json={"jsonrpc": "2.0", "id": 1})
    assert response.status_code == 404
    assert "no_session" in gateway.refusals()


def test_an_unknown_session_fails_at_initialize_and_is_logged(gateway):
    with pytest.raises(Exception):
        run(_initialize, gateway.mcp_url(str(uuid.uuid4()), "tools"))
    assert "unknown_session" in gateway.refusals()


def test_two_server_paths_record_two_mcp_sessions_under_one_session(gateway):
    session = gateway.register()

    async def use_both() -> None:
        async with AsyncExitStack() as stack:
            for server in ("tools", "tools_b"):
                mcp = await open_session(stack, gateway.mcp_url(session.id, server))
                await mcp.call_tool("forecast", {})

    run(use_both)
    mcp_sessions = gateway.end(session.id)["mcp_sessions"]
    assert sorted(mcp_sessions) == ["tools", "tools_b"]
    ids = [
        entry["mcp_session_id"]
        for entries in mcp_sessions.values()
        for entry in entries
    ]
    assert len(set(ids)) == 2
    assert all(
        entry["upstream_session_id"]
        for entries in mcp_sessions.values()
        for entry in entries
    )


def test_a_call_after_the_session_ends_is_refused_and_logged(gateway):
    session = gateway.register()

    async def call_after_end() -> None:
        async with AsyncExitStack() as stack:
            mcp = await open_session(stack, gateway.mcp_url(session.id, "tools"))
            await mcp.call_tool("forecast", {})
            await anyio.to_thread.run_sync(gateway.end, session.id)
            with anyio.fail_after(10):
                await mcp.call_tool("forecast", {})

    with pytest.raises(Exception):
        run(call_after_end)
    assert "ended_session" in gateway.refusals()


def test_a_server_that_is_down_fails_calls_promptly(gateway):
    session = gateway.register()
    started = time.monotonic()
    # The MCP client raises the gateway's error inside exception groups; their repr
    # includes every nested exception.
    with pytest.raises(Exception) as raised:
        run(_initialize, gateway.mcp_url(session.id, "down"))
    assert "is unavailable" in repr(raised.value)
    assert time.monotonic() - started < 5
    gateway.end(session.id)


def test_an_idle_session_is_ended_by_the_idle_timeout():
    with running_test_server() as url:
        with running_gateway(
            {"tools": HttpUpstream("tools", url)}, idle_timeout=1
        ) as gateway:
            session = gateway.register()
            time.sleep(2.5)
            assert gateway.end(session.id)["ended_by"] == "idle_timeout"
