"""Check the interposer's proxy trace without an agent, against Phase 3 exit criteria.

Starts its own gateway in-process (free port, temporary log and trace directories) in
front of one running HTTP MCP server, registers a session, lists tools, then sends two
``tools/call`` requests at once on one MCP session, each with its own tool-call ID in
``_meta``, as the agent does. Checks:

1. The trace starts with the registration event, holding the trusted inputs.
2. ``tools/list`` produced a ``descriptors`` event.
3. Each concurrent call has a call, an allow decision and a response, paired by JSON-RPC
   request ID and carrying its own tool-call ID.
4. The trace is complete as soon as the end-session call returns.

Exits 0 when every check passes, 1 otherwise. ``check_traces.py`` checks the join with a
real agent's trace.
"""

import argparse
import asyncio
import logging
import sys
import tempfile
import uuid
from pathlib import Path

import anyio
import httpx
import trace_schema as ts
import uvicorn
from check_sessions import _free_port
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

from interposer.config import HttpUpstream
from interposer.gateway import build_app

_PROMPT = "check tracing"


class _Checks:
    def __init__(self) -> None:
        self.failures = 0

    def report(self, name: str, passed: bool, detail: str = "") -> None:
        print(
            f"{'PASS' if passed else 'FAIL'}: {name}{f' ({detail})' if detail else ''}"
        )
        if not passed:
            self.failures += 1


async def _exercise(base_url: str, tool: str) -> Path:
    """Run the session against the gateway and return its trace directory."""
    session_id = str(uuid.uuid4())
    async with httpx.AsyncClient(base_url=base_url) as http:
        registration = await http.post(
            "/sessions",
            json={
                "session_id": session_id,
                "trusted_inputs": [{"kind": "user_prompt", "content": _PROMPT}],
            },
        )
        registration.raise_for_status()
        url = f"{base_url}/s/{session_id}/mcp/weather"
        async with streamable_http_client(url) as (read, write, _):
            async with ClientSession(read, write) as session:
                await session.initialize()
                await session.list_tools()
                await asyncio.gather(
                    *(
                        session.call_tool(
                            tool, {}, meta={ts.TOOL_CALL_ID_META_KEY: call_id}
                        )
                        for call_id in ("call-a", "call-b")
                    )
                )
                # Ended while the MCP session is still open, as the launcher does.
                (await http.delete(f"/sessions/{session_id}")).raise_for_status()
                # Read before anything else can run: the trace must already be complete.
                return Path(registration.json()["trace_dir"])


def _check(trace_dir: Path, checks: _Checks) -> None:
    events = ts.read_events(trace_dir / ts.PROXY_TRACE_FILE)
    by_type: dict[str, list[dict]] = {}
    for event in events:
        by_type.setdefault(event[ts.EVENT_TYPE], []).append(event)

    first = events[0] if events else {}
    checks.report(
        "trace starts with the registration and its trusted inputs",
        first.get(ts.EVENT_TYPE) == "registration"
        and first.get(ts.TRUSTED_INPUTS)
        == [{"kind": "user_prompt", "content": _PROMPT}],
    )
    descriptors = by_type.get("descriptors", [])
    tools = descriptors[0][ts.PAYLOAD]["result"]["tools"] if descriptors else []
    checks.report(
        "tools/list produced a descriptors event",
        len(descriptors) == 1 and len(tools) > 0,
        f"{len(tools)} tools",
    )

    paired = True
    for call_id in ("call-a", "call-b"):
        found = {
            kind: [e for e in by_type.get(kind, []) if e[ts.TOOL_CALL_ID] == call_id]
            for kind in ("call", "decision", "response")
        }
        request_ids = {
            e[ts.JSONRPC_REQUEST_ID] for kinds in found.values() for e in kinds
        }
        paired &= (
            all(len(found[kind]) == 1 for kind in found)
            and len(request_ids) == 1
            and found["decision"][0][ts.DECISION] == "allow"
        )
    calls = [e[ts.SEQ] for e in by_type.get("call", [])]
    responses = [e[ts.SEQ] for e in by_type.get("response", [])]
    overlapped = bool(calls and responses) and max(calls) < min(responses)
    checks.report(
        "concurrent calls each have a call, allow decision and response, paired by ID",
        paired,
        "both in flight together" if overlapped else "did not overlap this run",
    )
    checks.report(
        "trace is complete when the end-session call returns",
        len(responses) == 2,
        f"{len(events)} events",
    )


async def _run(server_url: str, tool: str) -> int:
    with tempfile.TemporaryDirectory() as temp_dir:
        port = _free_port()
        app = build_app(
            {"weather": HttpUpstream(name="weather", url=server_url)},
            Path(temp_dir) / "logs",
            Path(temp_dir) / "traces",
            idle_timeout=60,
            upstream_timeout=30,
        )
        server = uvicorn.Server(
            uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning")
        )
        checks = _Checks()
        async with anyio.create_task_group() as tg:
            tg.start_soon(server.serve)
            while not server.started:
                await anyio.sleep(0.05)
            trace_dir = await _exercise(f"http://127.0.0.1:{port}", tool)
            _check(trace_dir, checks)
            server.should_exit = True
    return 1 if checks.failures else 0


def main() -> None:
    """Run the Phase 3 proxy-trace checks against one running HTTP MCP server."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("server_url", help="URL of a running HTTP MCP server")
    parser.add_argument(
        "tool", help="name of a tool on that server that takes no arguments"
    )
    args = parser.parse_args()
    logging.basicConfig(
        level=logging.WARNING, format="  %(levelname)s %(name)s: %(message)s"
    )
    sys.exit(asyncio.run(_run(args.server_url, args.tool)))


if __name__ == "__main__":
    main()
