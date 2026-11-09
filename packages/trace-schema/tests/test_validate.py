"""Tests for trace_schema.validate: what joins, and each way two traces can disagree."""

import trace_schema as ts
from trace_schema.validate import check_events, parallel_steps

SESSION = "11111111-1111-1111-1111-111111111111"
MCP_SESSION = "mcp-1"


def _numbered(events: list[tuple[str, dict]]) -> list[dict]:
    """Build a trace from (event type, fields) pairs, numbered from 1."""
    return [
        ts.make_event(SESSION, seq, event_type, fields)
        for seq, (event_type, fields) in enumerate(events, start=1)
    ]


def _proxy_call(request_id: str, tool_call_id: str | None) -> dict:
    return {
        ts.MCP_SESSION_ID: MCP_SESSION,
        ts.JSONRPC_REQUEST_ID: request_id,
        ts.TOOL_NAME: "forecast",
        ts.TOOL_CALL_ID: tool_call_id,
    }


def _proxy(*steps: tuple[str, str, str | None]) -> list[dict]:
    """Build a proxy trace: a registration, then one event per step given."""
    events = [("registration", {ts.TRUSTED_INPUTS: []})]
    for event_type, request_id, tool_call_id in steps:
        events.append((event_type, _proxy_call(request_id, tool_call_id)))
    return _numbered(events)


def _agent(*tool_call_ids: str, results: bool = True) -> list[dict]:
    """An agent trace with one tool_call (and tool_result) per ID."""
    events = []
    for tool_call_id in tool_call_ids:
        events.append(("tool_call", {ts.TOOL_CALL_ID: tool_call_id}))
        if results:
            events.append(("tool_result", {ts.TOOL_CALL_ID: tool_call_id}))
    return _numbered(events)


def _answered(request_id: str, tool_call_id: str, answer: str = "response") -> list:
    return [
        ("call", request_id, tool_call_id),
        ("decision", request_id, tool_call_id),
        (answer, request_id, tool_call_id),
    ]


def test_matching_traces_have_no_problems():
    proxy = _proxy(*_answered("2", "a"), *_answered("3", "b"))
    assert check_events(proxy, _agent("a", "b")) == []


def test_a_timeout_answers_a_call():
    proxy = _proxy(*_answered("2", "a", answer="timeout"))
    assert check_events(proxy, _agent("a")) == []


def test_an_unanswered_call_is_allowed_only_after_a_crash():
    unanswered = [("call", "2", "a"), ("decision", "2", "a")]
    assert check_events(_proxy(*unanswered), _agent("a"))
    crashed = _proxy(*unanswered) + [
        ts.make_event(SESSION, 4, "crash", {ts.MCP_SESSION_ID: MCP_SESSION})
    ]
    assert check_events(crashed, _agent("a")) == []


def test_a_call_the_agent_never_logged_is_reported():
    problems = check_events(_proxy(*_answered("2", "a")), _agent())
    assert any("tool call a: 0 agent tool_call" in p for p in problems)


def test_a_call_the_proxy_never_saw_is_reported():
    problems = check_events(_proxy(), _agent("a"))
    assert any(
        "tool call a: 1 agent tool_call event(s), 0 proxy" in p for p in problems
    )


def test_a_proxy_call_without_a_tool_call_id_is_reported():
    problems = check_events(_proxy(*_answered("2", None)), _agent())
    assert any("carry no tool-call ID" in p for p in problems)


def test_events_disagreeing_on_the_tool_call_id_are_reported():
    proxy = _proxy(("call", "2", "a"), ("decision", "2", "a"), ("response", "2", "b"))
    problems = check_events(proxy, _agent("a"))
    assert any("disagree on tool-call ID" in p for p in problems)


def test_a_missing_tool_result_is_reported():
    problems = check_events(_proxy(*_answered("2", "a")), _agent("a", results=False))
    assert any("has 0 tool_result events" in p for p in problems)


def test_a_gap_in_numbering_is_reported():
    proxy = _proxy(*_answered("2", "a"))
    del proxy[2]
    assert any("not numbered" in p for p in check_events(proxy, _agent("a")))


def test_a_trace_must_start_with_registration():
    proxy = _proxy(*_answered("2", "a"))[1:]
    for seq, event in enumerate(proxy, start=1):
        event[ts.SEQ] = seq
    assert any("registration" in p for p in check_events(proxy, _agent("a")))


def test_parallel_steps_report_whether_calls_overlapped():
    step = _numbered(
        [
            (
                "llm_step",
                {
                    ts.OUTPUT_MESSAGES: [
                        {
                            "role": "assistant",
                            "parts": [
                                {"type": "tool_call", "id": "a"},
                                {"type": "tool_call", "id": "b"},
                            ],
                        }
                    ]
                },
            )
        ]
    )
    overlapping = _proxy(
        ("call", "2", "a"),
        ("call", "3", "b"),
        ("response", "2", "a"),
        ("response", "3", "b"),
    )
    one_after_another = _proxy(*_answered("2", "a"), *_answered("3", "b"))
    assert parallel_steps(overlapping, step) == [(["a", "b"], True)]
    assert parallel_steps(one_after_another, step) == [(["a", "b"], False)]


def test_a_blocked_call_has_no_answer():
    blocked = _proxy(("call", "2", "a"), ("decision", "2", "a"))
    blocked[2][ts.DECISION] = "block"
    assert check_events(blocked, _agent("a")) == []
    answered = _proxy(*_answered("2", "a"))
    answered[2][ts.DECISION] = "block"
    assert check_events(answered, _agent("a"))
