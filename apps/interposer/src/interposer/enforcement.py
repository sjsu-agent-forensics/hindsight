"""In-path enforcement: each tool call is checked against the session's rules first.

The decision comes from ``rule_engine.evaluate`` on the provenance index's matches for
the call's arguments, the same function replay uses, so a recorded session replays to
the same decisions. Deterministic and model-free.
"""

from dataclasses import dataclass, field
from typing import Any

import trace_schema as ts
from mcp.types import JSONRPCMessage, JSONRPCRequest
from provenance import IndexEntry, ProvenanceIndex
from rule_engine import Decision, Rule, evaluate


@dataclass(frozen=True)
class CallDecision:
    """The decision on one ``tools/call``, and the index matches it was based on."""

    decision: Decision
    matches: dict[str, list[IndexEntry]] = field(default_factory=dict)

    @property
    def blocked(self) -> bool:
        """Return whether the call must not be forwarded."""
        return self.decision.effect == "block"


class ChannelEnforcer:
    """Decides each ``tools/call`` on one MCP session under its session's rules."""

    def __init__(self, index: ProvenanceIndex, rules: list[Rule], server: str) -> None:
        self._index = index
        self._rules = rules
        self._server = server

    def decide(self, message: JSONRPCMessage) -> CallDecision | None:
        """Return the decision on a ``tools/call`` request; None for anything else.

        Only tool calls are checked: other requests and notifications carry no tool
        arguments and are always forwarded.
        """
        request = message.root
        if not isinstance(request, JSONRPCRequest) or request.method != "tools/call":
            return None
        params = request.params or {}
        arguments: Any = params.get("arguments")
        matches = self._index.match(arguments) if isinstance(arguments, dict) else {}
        decision = evaluate(self._rules, self._server, params.get("name"), matches)
        return CallDecision(decision, matches)


def tool_call_id_of(request: JSONRPCRequest) -> str | None:
    """Return the agent's tool-call ID from a request's ``_meta``, if it sent one."""
    meta = (request.params or {}).get("_meta")
    return meta.get(ts.TOOL_CALL_ID_META_KEY) if isinstance(meta, dict) else None
