"""Phase 1: the gateway relays a server's tools unchanged."""

import json
from contextlib import AsyncExitStack

import pytest

from interposer.config import HttpUpstream
from interposer.testing import (
    list_tools_raw,
    open_session,
    run,
    running_gateway,
    running_test_server,
)


@pytest.fixture(scope="module")
def upstream_url():
    with running_test_server() as url:
        yield url


@pytest.fixture(scope="module")
def gateway(upstream_url):
    with running_gateway({"tools": HttpUpstream("tools", upstream_url)}) as gateway:
        yield gateway


def test_tools_list_matches_the_server_exactly(upstream_url, gateway):
    """Descriptors reach the agent as the server sent them, key order included."""
    session = gateway.register()
    proxied = run(list_tools_raw, gateway.mcp_url(session.id, "tools"))
    gateway.end(session.id)
    direct = run(list_tools_raw, upstream_url)
    assert json.dumps(proxied) == json.dumps(direct)
    assert {tool["name"] for tool in proxied["tools"]} == {"forecast", "echo", "lookup"}


def test_tool_calls_are_relayed(gateway):
    session = gateway.register()

    async def call() -> str:
        async with AsyncExitStack() as stack:
            mcp = await open_session(stack, gateway.mcp_url(session.id, "tools"))
            result = await mcp.call_tool("echo", {"text": "relayed"})
            return result.content[0].text

    assert run(call) == "relayed"
    gateway.end(session.id)
