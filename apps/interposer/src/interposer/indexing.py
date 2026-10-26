"""Keeps a session's provenance index up to date from its MCP sessions' responses.

The relay shows each upstream message to the indexer before forwarding it, so a
response's values are indexed before the agent can see them: any call the agent makes
after reading a response is matched against that response.
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


class ChannelIndexer:
    """Indexes one MCP session's tool results into its hindsight session's index.

    The index is shared by every MCP session in the hindsight session, so a value one
    server returned is matched in a call to any other.
    """

    def __init__(self, index: ProvenanceIndex, server: str) -> None:
        self._index = index
        self._server = server
        # Each in-flight tools/call: its tool name and the agent's tool-call ID.
        self._calls: dict[RequestId, tuple[str | None, str | None]] = {}

    def on_agent_message(self, message: JSONRPCMessage) -> None:
        """Remember a ``tools/call`` request, to know whose result arrives later."""
        request = message.root
        if isinstance(request, JSONRPCRequest) and request.method == "tools/call":
            params = request.params or {}
            meta = params.get("_meta")
            tool_call_id = (
                meta.get(ts.TOOL_CALL_ID_META_KEY) if isinstance(meta, dict) else None
            )
            self._calls[request.id] = (params.get("name"), tool_call_id)

    def on_upstream_message(self, message: JSONRPCMessage) -> None:
        """Index a ``tools/call`` result, with that call as its source."""
        response = message.root
        if not isinstance(response, JSONRPCResponse | JSONRPCError):
            return
        call = self._calls.pop(response.id, None)
        if call is None or isinstance(response, JSONRPCError):
            return
        tool, tool_call_id = call
        self._index.add_tool_result(
            response.result, self._server, tool, str(response.id), tool_call_id
        )

    def on_timeout(self, request_id: RequestId) -> None:
        """Forget a call that will never be answered; a late answer is dropped."""
        self._calls.pop(request_id, None)

    def on_upstream_lost(self, error_type: str) -> None:
        """Forget every call; none of them will be answered now."""
        self._calls.clear()


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
