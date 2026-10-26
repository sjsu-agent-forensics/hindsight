"""Phase 3: each session's proxy trace is complete, ordered and paired.

The join with a real agent's trace is checked by ``scripts/check_traces.py`` after a
launcher run, since it needs a model; ``trace_schema.validate`` has its own unit tests.
"""

import asyncio
from contextlib import AsyncExitStack

import pytest
import trace_schema as ts

from interposer.config import HttpUpstream
from interposer.testing import open_session, run, running_gateway, running_test_server

PROMPT = "check tracing"


@pytest.fixture(scope="module")
def events():
    """The proxy trace of one session that lists tools, then makes two calls at once.

    The calls carry their own tool-call IDs in _meta, as the agent's do. The trace is
    read right after the end call returns, which is when it must already be complete.
    """
    with running_test_server() as url:
        with running_gateway({"tools": HttpUpstream("tools", url)}) as gateway:
            session = gateway.register(PROMPT)

            async def exercise() -> None:
                async with AsyncExitStack() as stack:
                    mcp = await open_session(
                        stack, gateway.mcp_url(session.id, "tools")
                    )
                    await mcp.list_tools()
                    await asyncio.gather(
                        *(
                            mcp.call_tool(
                                "forecast", {}, meta={ts.TOOL_CALL_ID_META_KEY: call_id}
                            )
                            for call_id in ("call-a", "call-b")
                        )
                    )
                    # Ended while the MCP session is still open, as the launcher does.
                    gateway.end(session.id)

            run(exercise)
            return ts.read_events(session.trace_dir / ts.PROXY_TRACE_FILE)


def _of_type(events: list[dict], event_type: str) -> list[dict]:
    return [e for e in events if e[ts.EVENT_TYPE] == event_type]


def test_the_trace_starts_with_the_registration_and_its_trusted_inputs(events):
    first = events[0]
    assert first[ts.EVENT_TYPE] == "registration"
    assert first[ts.TRUSTED_INPUTS] == [{"kind": "user_prompt", "content": PROMPT}]


def test_events_are_numbered_in_order(events):
    assert [e[ts.SEQ] for e in events] == list(range(1, len(events) + 1))


def test_tools_list_produces_a_descriptors_event(events):
    [descriptors] = _of_type(events, "descriptors")
    tools = descriptors[ts.PAYLOAD]["result"]["tools"]
    assert {tool["name"] for tool in tools} == {"forecast", "echo"}


@pytest.mark.parametrize("call_id", ["call-a", "call-b"])
def test_each_concurrent_call_is_paired_by_request_id(events, call_id):
    """A call, an allow decision and a response, on one request and tool-call ID."""
    found = {
        kind: [e for e in _of_type(events, kind) if e[ts.TOOL_CALL_ID] == call_id]
        for kind in ("call", "decision", "response")
    }
    assert all(len(found[kind]) == 1 for kind in found)
    assert len({e[ts.JSONRPC_REQUEST_ID] for e in sum(found.values(), [])}) == 1
    assert found["decision"][0][ts.DECISION] == "allow"
    sent = found["call"][0][ts.PAYLOAD]["params"]["_meta"]
    assert sent == {ts.TOOL_CALL_ID_META_KEY: call_id}


def test_the_trace_is_complete_when_the_end_call_returns(events):
    assert len(_of_type(events, "response")) == 2
