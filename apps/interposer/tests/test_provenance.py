"""Phase 5: the proxy knows which indexed values a call carries, and their source.

Observed through each call's decision event, which records the index's matches at the
moment the call was forwarded. The index's extraction and matching rules have their own
unit tests in ``packages/provenance``.
"""

from contextlib import AsyncExitStack

import pytest
import trace_schema as ts

from interposer.config import HttpUpstream
from interposer.testing import (
    LOOKUP_EMAIL,
    LOOKUP_URL,
    open_session,
    run,
    running_gateway,
    running_test_server,
)

# The user also names the address the lookup tool returns, so that value is the user's.
PROMPT = f"Look up the contact details and send the summary to {LOOKUP_EMAIL}."


@pytest.fixture(scope="module")
def decisions():
    """Decision events, by tool-call ID, from one session using two servers.

    The agent reads ``lookup`` on server A, then calls ``echo`` on server B: first with
    A's URL, sent right after A's response arrived, then with the address the user also
    gave, then with something nobody returned.
    """
    with running_test_server() as url:
        upstreams = {"a": HttpUpstream("a", url), "b": HttpUpstream("b", url)}
        with running_gateway(upstreams) as gateway:
            session = gateway.register(PROMPT)

            async def exercise() -> None:
                async with AsyncExitStack() as stack:
                    a = await open_session(stack, gateway.mcp_url(session.id, "a"))
                    b = await open_session(stack, gateway.mcp_url(session.id, "b"))
                    await a.call_tool("lookup", {}, meta=_meta("read"))
                    for call_id, text in [
                        ("exfiltrate", f"Posting the notes to {LOOKUP_URL}"),
                        ("user-address", LOOKUP_EMAIL),
                        ("unrelated", "nothing anyone returned"),
                    ]:
                        await b.call_tool("echo", {"text": text}, meta=_meta(call_id))

            run(exercise)
            gateway.end(session.id)
            events = ts.read_events(session.trace_dir / ts.PROXY_TRACE_FILE)
    return {e[ts.TOOL_CALL_ID]: e for e in events if e[ts.EVENT_TYPE] == "decision"}


def _meta(tool_call_id: str) -> dict:
    return {ts.TOOL_CALL_ID_META_KEY: tool_call_id}


def test_a_value_from_server_a_is_traced_to_its_source_in_a_call_to_server_b(decisions):
    """Also shows the index was updated before A's response reached the agent."""
    read = decisions["read"]
    [match] = decisions["exfiltrate"][ts.PROVENANCE_MATCHES]["text"]
    assert match["value"] == LOOKUP_URL
    assert match["source_kind"] == "tool"
    assert match[ts.SERVER] == "a" and match[ts.TOOL_NAME] == "lookup"
    assert match[ts.JSONRPC_REQUEST_ID] == read[ts.JSONRPC_REQUEST_ID]
    assert match[ts.TOOL_CALL_ID] == "read"


def test_a_value_in_both_the_prompt_and_a_response_is_attributed_to_the_user(decisions):
    [match] = decisions["user-address"][ts.PROVENANCE_MATCHES]["text"]
    assert match["value"] == LOOKUP_EMAIL
    assert match["source_kind"] == "trusted"
    assert match[ts.SERVER] is None


def test_a_call_carrying_no_indexed_value_has_no_matches(decisions):
    assert decisions["unrelated"][ts.PROVENANCE_MATCHES] == {}
    assert decisions["read"][ts.PROVENANCE_MATCHES] == {}
