"""Trace event format shared by the interposer and the agent.

Each session produces two JSON Lines files, one per producer, one event per line. An
event is a flat JSON object. Field names follow the OpenTelemetry GenAI and MCP semantic
conventions where one exists (``gen_ai.*``, ``mcp.*``, ``jsonrpc.*``, ``error.type``);
hindsight's own fields are under ``hindsight.*``. The events are a forensic record, not
OpenTelemetry spans: ``hindsight.seq`` gives each producer's total order, and proxy
events keep the JSON-RPC message exactly as it crossed the proxy.

Joining the two traces: the agent writes its tool-call ID into each ``tools/call``
request's ``params._meta`` under ``TOOL_CALL_ID_META_KEY``, and the proxy records it as
``gen_ai.tool.call.id`` on every event for that call. Every agent ``tool_call`` event
joins exactly one proxy ``call`` event on that field.
"""

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

# The params._meta key carrying the agent's tool-call ID. MCP reserves unprefixed keys,
# so it is prefixed.
TOOL_CALL_ID_META_KEY = "hindsight/tool_call_id"

PROXY_TRACE_FILE = "proxy.jsonl"
AGENT_TRACE_FILE = "agent.jsonl"

ProxyEventType = Literal[
    "registration",  # the session's registration payload, including trusted inputs
    "descriptors",  # a tools/list response as the server sent it
    "call",  # a tools/call request as the agent sent it
    "decision",  # the enforcement decision for a call
    "response",  # the server's answer to a call, matched by JSON-RPC request ID
    "timeout",  # an upstream call that timed out
    "crash",  # an upstream server that failed for the rest of the session
]
AgentEventType = Literal["user_turn", "llm_step", "tool_call", "tool_result"]

# Envelope, on every event.
SESSION_ID = "hindsight.session.id"
SEQ = "hindsight.seq"  # per producer per session, from 1, no gaps
EVENT_TYPE = "hindsight.event.type"
TIME = "hindsight.time"  # ISO 8601, UTC

# OpenTelemetry GenAI and MCP semantic conventions.
TOOL_NAME = "gen_ai.tool.name"
TOOL_CALL_ID = "gen_ai.tool.call.id"
TOOL_CALL_ARGUMENTS = "gen_ai.tool.call.arguments"
TOOL_CALL_RESULT = "gen_ai.tool.call.result"
INPUT_MESSAGES = "gen_ai.input.messages"
OUTPUT_MESSAGES = "gen_ai.output.messages"
MCP_METHOD = "mcp.method.name"
MCP_SESSION_ID = "mcp.session.id"
JSONRPC_REQUEST_ID = "jsonrpc.request.id"  # always a string, per the convention
ERROR_TYPE = "error.type"

# Hindsight's own fields.
SERVER = "hindsight.mcp.server"  # the server's name in the interposer config
UPSTREAM_SESSION_ID = "hindsight.mcp.upstream_session.id"
PAYLOAD = "hindsight.payload"  # the JSON-RPC message, as forwarded
DECISION = "hindsight.decision"  # "allow" or "block"
RULE_ID = "hindsight.rule.id"
TRUSTED_INPUTS = "hindsight.trusted_inputs"
CONTEXT_TOOL_CALL_IDS = "hindsight.context.tool_call_ids"  # results the model had seen


def make_event(
    session_id: str, seq: int, event_type: str, fields: dict[str, Any]
) -> dict[str, Any]:
    """Return an event: the envelope fields followed by ``fields``."""
    return {
        SESSION_ID: session_id,
        SEQ: seq,
        EVENT_TYPE: event_type,
        TIME: datetime.now(timezone.utc).isoformat(),
        **fields,
    }


def dumps(event: dict[str, Any]) -> str:
    """Serialize an event as one JSON Lines line, newline included."""
    return json.dumps(event, ensure_ascii=False) + "\n"


def read_events(path: Path) -> list[dict[str, Any]]:
    """Read every event from a trace file, in file order.

    Raises:
        json.JSONDecodeError: A line is not valid JSON, for example a final line cut
            short by a crash.
    """
    with path.open(encoding="utf-8") as file:
        return [json.loads(line) for line in file if line.strip()]
