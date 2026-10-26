"""Replay a recorded proxy trace through the rule engine.

Rebuilds the session's provenance index from the trace, in ``hindsight.seq`` order, and
evaluates each ``call`` against it, as the live proxy did: trusted inputs and extraction
settings from the ``registration`` event, descriptors from ``descriptors`` events and
tool results from ``response`` events. The live proxy updates its index and writes these
events at the same moment, so the replayed index at each call is the live one.

Replaying under the session's own rules (recorded at registration) must reproduce its
live decisions; replaying under candidate rules shows what they would have decided.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

import trace_schema as ts
from provenance import ExtractionConfig, ProvenanceIndex

from rule_engine import Decision, Rule, evaluate, parse_rule


@dataclass(frozen=True)
class ReplayedCall:
    """One replayed call and the decision the rules give it."""

    seq: int
    mcp_session_id: str | None
    jsonrpc_id: str | None
    tool_call_id: str | None
    server: str | None
    tool: str | None
    decision: Decision


def replay(
    proxy_events: Sequence[dict[str, Any]], rules: Sequence[Rule] | None = None
) -> list[ReplayedCall]:
    """Replay a session's proxy trace and return each call's decision, in order.

    Args:
        rules: The rules to evaluate. If None, the session's own rules, as recorded in
            its registration event.

    Raises:
        ValueError: The trace does not start with a registration event.
    """
    events = sorted(proxy_events, key=lambda event: event[ts.SEQ])
    if not events or events[0][ts.EVENT_TYPE] != "registration":
        raise ValueError("a proxy trace starts with its registration event")
    registration = events[0]
    if rules is None:
        rules = [parse_rule(rule) for rule in registration.get(ts.RULES, [])]
    config = registration.get(ts.EXTRACTION)
    index = ProvenanceIndex(
        ExtractionConfig.from_dict(config) if config else ExtractionConfig()
    )
    for trusted in registration.get(ts.TRUSTED_INPUTS, []):
        index.add_trusted(trusted["content"])

    calls = []
    for event in events[1:]:
        event_type = event[ts.EVENT_TYPE]
        payload = event.get(ts.PAYLOAD, {})
        if event_type == "descriptors" and "result" in payload:
            index.add_descriptors(
                payload["result"], event[ts.SERVER], event[ts.JSONRPC_REQUEST_ID]
            )
        elif event_type == "response" and "result" in payload:
            index.add_tool_result(
                payload["result"],
                event[ts.SERVER],
                event[ts.TOOL_NAME],
                event[ts.JSONRPC_REQUEST_ID],
                event[ts.TOOL_CALL_ID],
            )
        elif event_type == "call":
            arguments = payload.get("params", {}).get("arguments")
            matches = index.match(arguments) if isinstance(arguments, dict) else {}
            calls.append(
                ReplayedCall(
                    seq=event[ts.SEQ],
                    mcp_session_id=event.get(ts.MCP_SESSION_ID),
                    jsonrpc_id=event.get(ts.JSONRPC_REQUEST_ID),
                    tool_call_id=event.get(ts.TOOL_CALL_ID),
                    server=event.get(ts.SERVER),
                    tool=event.get(ts.TOOL_NAME),
                    decision=evaluate(
                        rules, event[ts.SERVER], event.get(ts.TOOL_NAME), matches
                    ),
                )
            )
    return calls
