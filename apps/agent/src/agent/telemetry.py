"""The agent's telemetry: the join key on every tool call, and the agent trace.

This is the agent's only coupling to hindsight. Enforcement lives in the interposer; the
agent only reports what it did.

- **Join key.** Every MCP ``tools/call`` request carries the agent's tool-call ID in
  ``params._meta``, so the interposer can record it and each agent ``tool_call`` event
  joins exactly one proxy ``call`` event. The LangChain MCP adapter has no way to set
  ``_meta``, so an adapter interceptor (which knows the tool-call ID) hands the ID to a
  wrapper around the session's ``call_tool`` (which sends the request).
- **Agent trace.** A ``create_agent`` middleware writes user turns, model steps, tool
  calls and tool results as they happen. These are execution facts recorded in code,
  not a log the model chooses to write. Model reasoning is not recorded.

Tool descriptors and results are untrusted; they are recorded as received.
"""

import functools
from collections.abc import Awaitable, Callable
from contextvars import ContextVar
from pathlib import Path
from typing import Any

import trace_schema as ts
from langchain.agents.middleware import AgentMiddleware
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, ToolMessage
from langchain_mcp_adapters.interceptors import MCPToolCallRequest
from langgraph.prebuilt.tool_node import ToolCallRequest
from mcp import ClientSession

# The ID of the tool call being executed in this task. Each parallel tool call runs in
# its own task with its own copy of the context, so concurrent calls do not mix.
_current_tool_call_id: ContextVar[str | None] = ContextVar(
    "current_tool_call_id", default=None
)


async def stamp_tool_call_id(
    request: MCPToolCallRequest,
    handler: Callable[[MCPToolCallRequest], Awaitable[Any]],
) -> Any:
    """Adapter interceptor making the call's tool-call ID available to ``call_tool``."""
    tool_call_id = getattr(request.runtime, "tool_call_id", None)
    token = _current_tool_call_id.set(tool_call_id)
    try:
        return await handler(request)
    finally:
        _current_tool_call_id.reset(token)


def add_tool_call_meta(session: ClientSession) -> None:
    """Make every ``call_tool`` on ``session`` carry the current tool-call ID in _meta.

    Works with ``stamp_tool_call_id``, which sets the ID. A call made outside it (with
    no current tool-call ID) is sent unchanged.
    """
    original = session.call_tool

    @functools.wraps(original)
    async def call_tool(*args: Any, meta: dict[str, Any] | None = None, **kwargs: Any):
        tool_call_id = _current_tool_call_id.get()
        if tool_call_id is not None:
            meta = {**(meta or {}), ts.TOOL_CALL_ID_META_KEY: tool_call_id}
        return await original(*args, meta=meta, **kwargs)

    # An instance attribute shadows the method, for this session only.
    session.call_tool = call_tool


class AgentTrace:
    """The agent trace for one session: numbered events appended to a JSON Lines file.

    Each event is written and flushed as it happens, so the file is complete whenever
    the agent stops, even if it crashes mid-run.
    """

    def __init__(self, path: Path, session_id: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self._file = path.open("a", encoding="utf-8")
        self._session_id = session_id
        self._seq = 0

    def emit(self, event_type: ts.AgentEventType, fields: dict[str, Any]) -> None:
        """Write one event, numbered after every event already written."""
        self._seq += 1
        event = ts.make_event(self._session_id, self._seq, event_type, fields)
        self._file.write(ts.dumps(event))
        self._file.flush()

    def close(self) -> None:
        """Close the trace file."""
        self._file.close()


class TraceMiddleware(AgentMiddleware):
    """Writes the agent trace from inside the ``create_agent`` loop.

    Async-only, like the rest of the agent: MCP tools cannot run synchronously.
    """

    def __init__(self, trace: AgentTrace) -> None:
        super().__init__()
        self._trace = trace
        self._user_turns_recorded = 0

    async def abefore_agent(self, state: dict, runtime: Any) -> None:
        """Record user messages added since the last run, as ``user_turn`` events."""
        user_messages = [m for m in state["messages"] if isinstance(m, HumanMessage)]
        for message in user_messages[self._user_turns_recorded :]:
            parts = [{"type": "text", "content": text} for text in _texts(message)]
            self._trace.emit(
                "user_turn", {ts.INPUT_MESSAGES: [{"role": "user", "parts": parts}]}
            )
        self._user_turns_recorded = len(user_messages)

    async def aafter_model(self, state: dict, runtime: Any) -> None:
        """Record the model's step: its visible text and the tool calls it requested."""
        message = state["messages"][-1]
        if not isinstance(message, AIMessage):
            return
        parts: list[dict[str, Any]] = [
            {"type": "text", "content": text} for text in _texts(message)
        ]
        parts += [
            {
                "type": "tool_call",
                "id": call["id"],
                "name": call["name"],
                "arguments": call["args"],
            }
            for call in message.tool_calls
        ]
        output: dict[str, Any] = {"role": "assistant", "parts": parts}
        finish_reason = _finish_reason(message)
        if finish_reason is not None:
            output["finish_reason"] = finish_reason
        self._trace.emit("llm_step", {ts.OUTPUT_MESSAGES: [output]})

    async def awrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], Awaitable[Any]],
    ) -> Any:
        """Record a tool call as it starts and its result as it returns."""
        call = request.tool_call
        # The tool results the model had seen when it asked for this call. Parallel
        # calls from one step share the same context.
        context_ids = [
            m.tool_call_id
            for m in request.state["messages"]
            if isinstance(m, ToolMessage)
        ]
        fields = {ts.TOOL_NAME: call["name"], ts.TOOL_CALL_ID: call["id"]}
        self._trace.emit(
            "tool_call",
            {
                **fields,
                ts.TOOL_CALL_ARGUMENTS: call["args"],
                ts.CONTEXT_TOOL_CALL_IDS: context_ids,
            },
        )
        try:
            result = await handler(request)
        except Exception as error:
            self._trace.emit(
                "tool_result", {**fields, ts.ERROR_TYPE: type(error).__name__}
            )
            raise
        result_fields = dict(fields)
        if isinstance(result, ToolMessage):
            result_fields[ts.TOOL_CALL_RESULT] = result.content
            if result.status == "error":
                result_fields[ts.ERROR_TYPE] = "tool_error"
        self._trace.emit("tool_result", result_fields)
        return result


def _texts(message: BaseMessage) -> list[str]:
    """Return a message's visible text, leaving out reasoning and other blocks."""
    return [
        block["text"]
        for block in message.content_blocks
        if block.get("type") == "text" and block.get("text")
    ]


def _finish_reason(message: AIMessage) -> str | None:
    """Return why the model stopped, under whichever key its provider uses, if any."""
    metadata = message.response_metadata
    for key in ("finish_reason", "stop_reason", "done_reason"):
        if metadata.get(key):
            return str(metadata[key])
    return None
