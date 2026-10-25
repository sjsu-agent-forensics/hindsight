"""Check that a session's proxy and agent traces are complete and join exactly.

Phase 3 exit check, run on a session's trace directory after the session has ended.
Checks:

1. Each file numbers its events 1, 2, 3, ... with no gaps, all in one session.
2. The proxy trace starts with the registration event.
3. Every proxy ``call`` has exactly one ``decision`` and one ``response``, paired by MCP
   session and JSON-RPC request ID, all carrying the same tool-call ID.
4. Every agent ``tool_call`` joins exactly one proxy ``call`` on the tool-call ID, and
   every proxy ``call`` joins exactly one agent ``tool_call``.
5. Every agent ``tool_call`` has one ``tool_result``.

It also reports model steps that asked for several tools at once, and whether those calls
were in flight at the proxy at the same time.

Exits 0 when every check passes, 1 otherwise.
"""

import argparse
import sys
from collections import Counter, defaultdict
from pathlib import Path

import trace_schema as ts


def check_session(trace_dir: Path) -> list[str]:
    """Return a description of every problem found in a session's traces."""
    proxy = ts.read_events(trace_dir / ts.PROXY_TRACE_FILE)
    agent_path = trace_dir / ts.AGENT_TRACE_FILE
    agent = ts.read_events(agent_path) if agent_path.exists() else []
    problems = _check_numbering("proxy", proxy) + _check_numbering("agent", agent)
    if not proxy or proxy[0][ts.EVENT_TYPE] != "registration":
        problems.append("proxy trace does not start with a registration event")
    problems += _check_proxy_pairing(proxy)
    problems += _check_join(proxy, agent)
    problems += _check_agent_results(agent)
    return problems


def parallel_steps(trace_dir: Path) -> list[tuple[list[str], bool]]:
    """Return each model step with several tool calls, and if they overlapped upstream.

    Calls overlapped if every one of them reached the proxy before any was answered.
    """
    proxy = ts.read_events(trace_dir / ts.PROXY_TRACE_FILE)
    agent = ts.read_events(trace_dir / ts.AGENT_TRACE_FILE)
    seq_of = {
        (event[ts.EVENT_TYPE], event[ts.TOOL_CALL_ID]): event[ts.SEQ]
        for event in proxy
        if event[ts.EVENT_TYPE] in ("call", "response")
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
        responses = [seq_of.get(("response", i)) for i in ids]
        overlapped = None not in calls + responses and max(calls) < min(responses)
        steps.append((ids, overlapped))
    return steps


def _check_numbering(name: str, events: list[dict]) -> list[str]:
    problems = []
    seqs = [event[ts.SEQ] for event in events]
    if seqs != list(range(1, len(events) + 1)):
        problems.append(f"{name} trace events are not numbered 1..{len(events)}")
    if len({event[ts.SESSION_ID] for event in events}) > 1:
        problems.append(f"{name} trace mixes events from more than one session")
    return problems


def _check_proxy_pairing(proxy: list[dict]) -> list[str]:
    by_request: dict[tuple, dict[str, list[dict]]] = defaultdict(
        lambda: defaultdict(list)
    )
    for event in proxy:
        if event[ts.EVENT_TYPE] in ("call", "decision", "response"):
            key = (event[ts.MCP_SESSION_ID], event[ts.JSONRPC_REQUEST_ID])
            by_request[key][event[ts.EVENT_TYPE]].append(event)
    problems = []
    for key, events in by_request.items():
        counts = {kind: len(events[kind]) for kind in ("call", "decision", "response")}
        if counts != {"call": 1, "decision": 1, "response": 1}:
            problems.append(f"request {key}: expected one of each, found {counts}")
            continue
        ids = {events[kind][0][ts.TOOL_CALL_ID] for kind in events}
        if len(ids) != 1:
            problems.append(f"request {key}: events disagree on tool-call ID {ids}")
    return problems


def _check_join(proxy: list[dict], agent: list[dict]) -> list[str]:
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


def _check_agent_results(agent: list[dict]) -> list[str]:
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


def main() -> None:
    """Check one session's trace directory."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("trace_dir", type=Path, help="the session's trace directory")
    args = parser.parse_args()

    problems = check_session(args.trace_dir)
    for problem in problems:
        print(f"FAIL: {problem}")
    agent = ts.read_events(args.trace_dir / ts.AGENT_TRACE_FILE)
    calls = sum(1 for e in agent if e[ts.EVENT_TYPE] == "tool_call")
    if not problems:
        print(f"PASS: {calls} tool calls; traces complete and joined exactly")
    for ids, overlapped in parallel_steps(args.trace_dir):
        print(
            f"  parallel step: {len(ids)} tool calls, "
            f"{'in flight together' if overlapped else 'not overlapping'} at the proxy"
        )
    sys.exit(1 if problems else 0)


if __name__ == "__main__":
    main()
