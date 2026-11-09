"""Check that a session's proxy and agent traces are complete and join exactly.

Run on a session's traces after the session has ended. ``check_events`` returns every
problem found:

1. Each trace numbers its events 1, 2, 3, ... with no gaps, all in one session.
2. The proxy trace starts with the registration event.
3. Every proxy ``call`` has exactly one ``decision`` and one answer, a ``response`` or a
   ``timeout``, all paired by MCP session and JSON-RPC request ID and carrying the same
   tool-call ID. A call has no answer if its decision blocked it, and may have none if
   its MCP session recorded a ``crash``.
4. Every agent ``tool_call`` joins exactly one proxy ``call`` on the tool-call ID, and
   every proxy ``call`` joins exactly one agent ``tool_call``.
5. Every agent ``tool_call`` has one ``tool_result``.

An unmatched event is a bug to fix, not a disagreement to tolerate: either a producer
missed an event, or one producer's account is false.
"""

from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

import trace_schema as ts

Event = dict[str, Any]


def check_session(trace_dir: Path) -> list[str]:
    """Return every problem found in the traces in a session's trace directory.

    A missing agent trace counts as an empty one, so every proxy call is unmatched.
    """
    proxy, agent = read_session(trace_dir)
    return check_events(proxy, agent)


def read_session(trace_dir: Path) -> tuple[list[Event], list[Event]]:
    """Return a session's proxy and agent events; a missing agent trace is empty."""
    agent_path = trace_dir / ts.AGENT_TRACE_FILE
    agent = ts.read_events(agent_path) if agent_path.exists() else []
    return ts.read_events(trace_dir / ts.PROXY_TRACE_FILE), agent


def check_events(proxy: list[Event], agent: list[Event]) -> list[str]:
    """Return a description of every problem found in one session's two traces."""
    problems = _check_numbering("proxy", proxy) + _check_numbering("agent", agent)
    if not proxy or proxy[0][ts.EVENT_TYPE] != "registration":
        problems.append("proxy trace does not start with a registration event")
    problems += _check_proxy_pairing(proxy)
    problems += _check_join(proxy, agent)
    problems += _check_agent_results(agent)
    return problems


def parallel_steps(
    proxy: list[Event], agent: list[Event]
) -> list[tuple[list[str], bool]]:
    """Return each model step with several tool calls, and whether they overlapped.

    Calls overlapped if every one of them reached the proxy before any was answered.
    """
    seq_of = {
        (event[ts.EVENT_TYPE], event[ts.TOOL_CALL_ID]): event[ts.SEQ]
        for event in proxy
        if event[ts.EVENT_TYPE] in ("call", "response", "timeout")
    }
    steps = []
    for event in agent:
        if event[ts.EVENT_TYPE] != "llm_step":
            continue
        parts = event[ts.OUTPUT_MESSAGES][0]["parts"]
        ids = [part["id"] for part in parts if part["type"] == "tool_call"]
        if len(ids) < 2:
            continue
        calls = [seq_of.get(("call", i)) for i in ids]
        answers = [
            seq_of.get(("response", i)) or seq_of.get(("timeout", i)) for i in ids
        ]
        overlapped = None not in calls + answers and max(calls) < min(answers)
        steps.append((ids, overlapped))
    return steps


def _check_numbering(name: str, events: list[Event]) -> list[str]:
    problems = []
    seqs = [event[ts.SEQ] for event in events]
    if seqs != list(range(1, len(events) + 1)):
        problems.append(f"{name} trace events are not numbered 1..{len(events)}")
    if len({event[ts.SESSION_ID] for event in events}) > 1:
        problems.append(f"{name} trace mixes events from more than one session")
    return problems


def _check_proxy_pairing(proxy: list[Event]) -> list[str]:
    crashed = {e[ts.MCP_SESSION_ID] for e in proxy if e[ts.EVENT_TYPE] == "crash"}
    by_request: dict[tuple, dict[str, list[Event]]] = defaultdict(
        lambda: defaultdict(list)
    )
    for event in proxy:
        if event[ts.EVENT_TYPE] in ("call", "decision", "response", "timeout"):
            key = (event[ts.MCP_SESSION_ID], event[ts.JSONRPC_REQUEST_ID])
            by_request[key][event[ts.EVENT_TYPE]].append(event)
    problems = []
    for key, events in by_request.items():
        calls, decisions = len(events["call"]), len(events["decision"])
        answers = len(events["response"]) + len(events["timeout"])
        blocked = any(e.get(ts.DECISION) == "block" for e in events["decision"])
        if blocked:
            allowed_answers = {0}
        elif key[0] in crashed:
            allowed_answers = {0, 1}
        else:
            allowed_answers = {1}
        if calls != 1 or decisions != 1 or answers not in allowed_answers:
            counts = {kind: len(found) for kind, found in events.items()}
            problems.append(f"request {key}: unexpected events {counts}")
            continue
        ids = {found[0][ts.TOOL_CALL_ID] for found in events.values() if found}
        if len(ids) != 1:
            problems.append(f"request {key}: events disagree on tool-call ID {ids}")
    return problems


def _check_join(proxy: list[Event], agent: list[Event]) -> list[str]:
    proxy_ids = Counter(
        event[ts.TOOL_CALL_ID] for event in proxy if event[ts.EVENT_TYPE] == "call"
    )
    agent_ids = Counter(
        event[ts.TOOL_CALL_ID] for event in agent if event[ts.EVENT_TYPE] == "tool_call"
    )
    problems = []
    if None in proxy_ids:
        problems.append(f"{proxy_ids[None]} proxy calls carry no tool-call ID")
    for tool_call_id in agent_ids | proxy_ids:
        if tool_call_id is None:
            continue
        in_agent, in_proxy = agent_ids[tool_call_id], proxy_ids[tool_call_id]
        if (in_agent, in_proxy) != (1, 1):
            problems.append(
                f"tool call {tool_call_id}: {in_agent} agent tool_call event(s), "
                f"{in_proxy} proxy call event(s); expected one of each"
            )
    return problems


def _check_agent_results(agent: list[Event]) -> list[str]:
    calls = Counter(
        e[ts.TOOL_CALL_ID] for e in agent if e[ts.EVENT_TYPE] == "tool_call"
    )
    results = Counter(
        e[ts.TOOL_CALL_ID] for e in agent if e[ts.EVENT_TYPE] == "tool_result"
    )
    return [
        f"agent tool call {tool_call_id} has {results[tool_call_id]} tool_result events"
        for tool_call_id in calls
        if results[tool_call_id] != 1
    ]
