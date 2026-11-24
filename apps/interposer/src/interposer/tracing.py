"""The proxy trace: what crossed the gateway in each session, written in the background.

Events are built on the request path but written by one background task, so disk I/O
never delays a message. Ending a session waits for its events to be written, so its
trace is complete before anyone is told the session is over. If the gateway process
dies, the last few events may be lost.

Message content in the trace (descriptors, arguments, results) comes from the agent and
the upstream servers and is untrusted; it is recorded exactly as forwarded, never acted
on.
"""

import logging
import math
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import anyio
import trace_schema as ts
from mcp.types import (
    JSONRPCError,
    JSONRPCMessage,
    JSONRPCRequest,
    JSONRPCResponse,
    RequestId,
)

from interposer.enforcement import CallDecision, tool_call_id_of
from interposer.indexing import matches_to_trace

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class _Line:
    path: Path
    text: str


@dataclass(frozen=True)
class _FlushMarker:
    written: anyio.Event = field(default_factory=anyio.Event)


class TraceWriter:
    """Appends trace events to their session's file from one background task.

    ``run()`` must be running (in the app's task group) for anything to be written.
    """

    def __init__(self, trace_dir: Path) -> None:
        self._trace_dir = trace_dir
        # Unbounded, so emitting never blocks or fails on the request path.
        self._send, self._receive = anyio.create_memory_object_stream[
            _Line | _FlushMarker
        ](math.inf)

    def session_dir(self, session_id: str) -> Path:
        """Return the directory holding a session's trace files."""
        return self._trace_dir / session_id

    def write(self, session_id: str, text: str) -> None:
        """Queue one serialized event for the session's proxy trace."""
        path = self.session_dir(session_id) / ts.PROXY_TRACE_FILE
        self._send.send_nowait(_Line(path, text))

    async def flush(self) -> None:
        """Return once every event queued before this call is on disk."""
        marker = _FlushMarker()
        self._send.send_nowait(marker)
        await marker.written.wait()

    async def run(self) -> None:
        """Write queued events until cancelled, batching whatever has queued up."""

        async with self._receive:
            async for item in self._receive:
                batch = [item]
                while True:
                    try:
                        batch.append(self._receive.receive_nowait())
                    except anyio.WouldBlock:
                        break
                lines = [entry for entry in batch if isinstance(entry, _Line)]
                if lines:
                    await anyio.to_thread.run_sync(_append_lines, lines)
                # Markers are released only after the lines queued before them are
                # written, since the queue is first in, first out.
                for entry in batch:
                    if isinstance(entry, _FlushMarker):
                        entry.written.set()


def _append_lines(lines: list[_Line]) -> None:
    by_path: dict[Path, list[str]] = defaultdict(list)
    for line in lines:
        by_path[line.path].append(line.text)
    for path, texts in by_path.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as file:
            file.writelines(texts)


class SessionTrace:
    """One session's proxy trace: numbers its events and queues them for writing."""

    def __init__(self, session_id: str, writer: TraceWriter) -> None:
        self._session_id = session_id
        self._writer = writer
        self._seq = 0

    def emit(self, event_type: ts.ProxyEventType, fields: dict[str, Any]) -> None:
        """Record one event, numbered after every event already recorded."""
        self._seq += 1
        event = ts.make_event(self._session_id, self._seq, event_type, fields)
        self._writer.write(self._session_id, ts.dumps(event))


class ChannelTracer:
    """Turns one MCP session's relayed messages into proxy trace events.

    The relay calls ``on_agent_message`` and ``on_upstream_message`` for every message,
    before forwarding it, and reports the calls it answers itself (timeouts, a lost
    upstream). Requests are remembered by JSON-RPC ID, so each answer is matched to its
    own request even when several calls are in flight at once.

    Each call's decision event records the enforcement decision, the rules that fired,
    and the provenance matches it was based on: which indexed values the call's
    arguments contained, and their sources.
    """

    def __init__(self, trace: SessionTrace, server: str, mcp_session_id: str) -> None:
        self._trace = trace
        self._server = server
        self._mcp_session_id = mcp_session_id
        self.upstream_session_id: str | None = None
        # Each in-flight request's method, so a timeout can say what timed out.
        self._methods: dict[RequestId, str] = {}
        # Each in-flight tools/call: its tool name and the agent's tool-call ID.
        self._calls: dict[RequestId, tuple[str | None, str | None]] = {}

    def on_agent_message(
        self, message: JSONRPCMessage, decision: CallDecision | None
    ) -> None:
        """Record a ``tools/call`` request and its decision; remember every request.

        A blocked call is never answered by the server, so it is not remembered.
        """
        request = message.root
        if not isinstance(request, JSONRPCRequest):
            return
        if request.method != "tools/call" or decision is None:
            self._methods[request.id] = request.method
            return
        tool = (request.params or {}).get("name")
        tool_call_id = tool_call_id_of(request)
        fields = self._call_fields(request.id, tool, tool_call_id)
        self._trace.emit("call", {**fields, ts.PAYLOAD: _dump(message)})
        rule_ids = decision.decision.rule_ids
        self._trace.emit(
            "decision",
            {
                **fields,
                ts.DECISION: decision.decision.effect,
                ts.RULE_ID: rule_ids[0] if rule_ids else None,
                ts.RULE_IDS: list(rule_ids),
                ts.PROVENANCE_MATCHES: matches_to_trace(decision.matches),
            },
        )
        if not decision.blocked:
            self._methods[request.id] = request.method
            self._calls[request.id] = (tool, tool_call_id)

    def on_upstream_message(self, message: JSONRPCMessage) -> None:
        """Record the response to a remembered ``tools/list`` or ``tools/call``."""
        response = message.root
        if not isinstance(response, JSONRPCResponse | JSONRPCError):
            return
        method = self._methods.pop(response.id, None)
        if method == "tools/list":
            fields = self._request_fields(response.id, method)
            self._trace.emit("descriptors", {**fields, ts.PAYLOAD: _dump(message)})
        elif method == "tools/call":
            tool, tool_call_id = self._calls.pop(response.id)
            fields = self._call_fields(response.id, tool, tool_call_id)
            error_type = _error_type(response)
            if error_type is not None:
                fields[ts.ERROR_TYPE] = error_type
            self._trace.emit("response", {**fields, ts.PAYLOAD: _dump(message)})

    def on_timeout(self, request_id: RequestId) -> None:
        """Record that a request got no upstream answer in time.

        The agent got a timeout error instead. A timeout is not a block: the call's
        decision was ``allow`` and it was forwarded; the upstream just never answered.
        """
        method = self._methods.pop(request_id, None)
        if method == "tools/call":
            tool, tool_call_id = self._calls.pop(request_id)
            fields = self._call_fields(request_id, tool, tool_call_id)
        else:
            fields = self._request_fields(request_id, method)
        self._trace.emit("timeout", {**fields, ts.ERROR_TYPE: "timeout"})

    def on_upstream_lost(self, error_type: str) -> None:
        """Record that the upstream failed or closed; its calls fail from now on.

        ``error_type`` is the failure's exception type, or ``closed`` when the upstream
        simply went away (a stdio child exiting, for example).
        """
        self._trace.emit("crash", {**self._session_fields(), ts.ERROR_TYPE: error_type})

    def _session_fields(self) -> dict[str, Any]:
        return {
            ts.SERVER: self._server,
            ts.MCP_SESSION_ID: self._mcp_session_id,
            ts.UPSTREAM_SESSION_ID: self.upstream_session_id,
        }

    def _request_fields(
        self, request_id: RequestId, method: str | None
    ) -> dict[str, Any]:
        return {
            **self._session_fields(),
            ts.MCP_METHOD: method,
            ts.JSONRPC_REQUEST_ID: str(request_id),
        }

    def _call_fields(
        self, request_id: RequestId, tool: str | None, tool_call_id: str | None
    ) -> dict[str, Any]:
        return {
            **self._request_fields(request_id, "tools/call"),
            ts.TOOL_NAME: tool,
            ts.TOOL_CALL_ID: tool_call_id,
        }


def _dump(message: JSONRPCMessage) -> dict[str, Any]:
    """Return the message as the SDK transports serialize it onto the wire."""
    return message.model_dump(by_alias=True, mode="json", exclude_none=True)


def _error_type(response: JSONRPCResponse | JSONRPCError) -> str | None:
    """Classify a failed call as the OpenTelemetry MCP convention does, else None.

    A JSON-RPC error is its error code; a successful response whose result has
    ``isError`` set is ``tool_error``.
    """
    if isinstance(response, JSONRPCError):
        return str(response.error.code)
    if response.result.get("isError") is True:
        return "tool_error"
    return None
