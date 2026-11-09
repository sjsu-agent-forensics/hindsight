"""Keeps a session's provenance index up to date from its MCP sessions' answers.

Tool results are indexed as ``tool`` values, and the URLs, emails and paths in tool
descriptors (``tools/list`` answers) as ``descriptor`` values. The relay shows each
upstream message to the indexer before forwarding it, so an answer's values are indexed
before the agent can see them: any call the agent makes after reading an answer is
matched against it.
"""

from typing import Any

import trace_schema as ts
from mcp.types import (
    JSONRPCError,
    JSONRPCMessage,
    JSONRPCRequest,
    JSONRPCResponse,
    RequestId,
)
from provenance import IndexEntry, ProvenanceIndex

from interposer.enforcement import CallDecision, tool_call_id_of


class ChannelIndexer:
    """Indexes one MCP session's descriptors and tool results into its session's index.

    The index is shared by every MCP session in the hindsight session, so a value one
    server returned is matched in a call to any other.
    """

    def __init__(self, index: ProvenanceIndex, server: str) -> None:
        self._index = index
        self._server = server
        # In-flight tools/list requests, whose answers carry descriptors.
        self._lists: set[RequestId] = set()
        # Each in-flight tools/call: its tool name and the agent's tool-call ID.
        self._calls: dict[RequestId, tuple[str | None, str | None]] = {}

    def on_agent_message(
        self, message: JSONRPCMessage, decision: CallDecision | None
    ) -> None:
        """Remember a request whose answer will be indexed; skip a blocked call."""
        request = message.root
        if not isinstance(request, JSONRPCRequest):
            return
        if request.method == "tools/list":
            self._lists.add(request.id)
        elif request.method == "tools/call" and not (decision and decision.blocked):
            name = (request.params or {}).get("name")
            self._calls[request.id] = (name, tool_call_id_of(request))

    def on_upstream_message(self, message: JSONRPCMessage) -> None:
        """Index a ``tools/list`` answer's descriptors or a ``tools/call`` result."""
        response = message.root
        if not isinstance(response, JSONRPCResponse | JSONRPCError):
            return
        call = self._calls.pop(response.id, None)
        listed = response.id in self._lists
        self._lists.discard(response.id)
        if isinstance(response, JSONRPCError):
            return
        if listed:
            self._index.add_descriptors(response.result, self._server, str(response.id))
        elif call is not None:
            tool, tool_call_id = call
            self._index.add_tool_result(
                response.result, self._server, tool, str(response.id), tool_call_id
            )

    def on_timeout(self, request_id: RequestId) -> None:
        """Forget a request that will never be answered; a late answer is dropped."""
        self._calls.pop(request_id, None)
        self._lists.discard(request_id)

    def on_upstream_lost(self, error_type: str) -> None:
        """Forget every request; none of them will be answered now."""
        self._calls.clear()
        self._lists.clear()


def matches_to_trace(matches: dict[str, list[IndexEntry]]) -> dict[str, list[dict]]:
    """Return index matches in the trace's form, with trace field names."""
    return {
        argument: [_entry_to_trace(entry) for entry in entries]
        for argument, entries in matches.items()
    }


def _entry_to_trace(entry: IndexEntry) -> dict[str, Any]:
    return {
        "value": entry.value,
        "source_kind": entry.source_kind,
        ts.SERVER: entry.server,
        ts.TOOL_NAME: entry.tool,
        ts.JSONRPC_REQUEST_ID: entry.jsonrpc_id,
        ts.TOOL_CALL_ID: entry.tool_call_id,
    }
