"""Check the gateway's session handling against the Phase 2 exit criteria.

Starts its own gateway in-process, on a free port with a temporary log directory and a
short idle timeout, in front of one running HTTP MCP server. The server is configured
under two names, so one session can use two server paths without a second server.
Checks, in order:

1. A request with no session prefix is refused and logged.
2. A client pointed at an unknown session fails at initialize, and is logged.
3. A session using two server paths records two MCP session IDs under one session ID.
4. A call sent after the session ends is refused and logged.
5. A session nobody ends is ended by the idle timeout.
6. A client of a server that is down gets an error promptly rather than hanging.

Exits 0 when every check passes, 1 otherwise.
"""

import argparse
import asyncio
import json
import logging
import socket
import sys
import tempfile
import uuid
from contextlib import AsyncExitStack
from pathlib import Path

import anyio
import httpx
import uvicorn
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

from interposer.config import HttpUpstream
from interposer.gateway import build_app

_IDLE_TIMEOUT = 2.0


class _Checks:
    def __init__(self, base_url: str, refused_log: Path, tool: str) -> None:
        self._base_url = base_url
        self._refused_log = refused_log
        self._tool = tool
        self.failures = 0

    def report(self, name: str, passed: bool, detail: str = "") -> None:
        print(
            f"{'PASS' if passed else 'FAIL'}: {name}{f' ({detail})' if detail else ''}"
        )
        if not passed:
            self.failures += 1

    def refused_reasons(self) -> list[str]:
        if not self._refused_log.exists():
            return []
        lines = self._refused_log.read_text(encoding="utf-8").splitlines()
        return [json.loads(line)["reason"] for line in lines]

    def url(self, session_id: str, server: str) -> str:
        return f"{self._base_url}/s/{session_id}/mcp/{server}"

    async def run(self, http: httpx.AsyncClient) -> None:
        await self._no_session(http)
        await self._unknown_session()
        await self._two_servers_then_end(http)
        await self._idle_timeout(http)
        await self._upstream_down(http)

    async def _no_session(self, http: httpx.AsyncClient) -> None:
        response = await http.post(
            f"{self._base_url}/mcp/weather", json={"jsonrpc": "2.0", "id": 1}
        )
        self.report(
            "request without a session prefix is refused and logged",
            response.status_code == 404 and "no_session" in self.refused_reasons(),
            f"HTTP {response.status_code}",
        )

    async def _unknown_session(self) -> None:
        failed_at_initialize = False
        try:
            with anyio.fail_after(10):
                async with streamable_http_client(
                    self.url(str(uuid.uuid4()), "weather")
                ) as (read, write, _):
                    async with ClientSession(read, write) as session:
                        await session.initialize()
        except Exception:
            failed_at_initialize = True
        self.report(
            "client with an unknown session fails at initialize and is logged",
            failed_at_initialize and "unknown_session" in self.refused_reasons(),
        )

    async def _two_servers_then_end(self, http: httpx.AsyncClient) -> None:
        session_id = await self._register(http)
        summary: dict = {}
        refused_after_end = False
        try:
            async with AsyncExitStack() as stack:
                sessions = [
                    await self._open(stack, self.url(session_id, server))
                    for server in ("weather", "weather_b")
                ]
                for session in sessions:
                    await session.call_tool(self._tool, {})
                summary = (
                    await http.delete(f"{self._base_url}/sessions/{session_id}")
                ).json()
                with anyio.fail_after(10):
                    await sessions[0].call_tool(self._tool, {})
        except Exception:
            refused_after_end = True

        mcp_sessions = summary.get("mcp_sessions", {})
        ids = [
            entry["mcp_session_id"]
            for entries in mcp_sessions.values()
            for entry in entries
        ]
        self.report(
            "a session using two servers records two MCP session IDs",
            sorted(mcp_sessions) == ["weather", "weather_b"] and len(set(ids)) == 2,
            json.dumps(mcp_sessions),
        )
        self.report(
            "a call after the session ends is refused and logged",
            refused_after_end and "ended_session" in self.refused_reasons(),
        )

    async def _idle_timeout(self, http: httpx.AsyncClient) -> None:
        session_id = await self._register(http)
        await anyio.sleep(_IDLE_TIMEOUT * 2)
        summary = (await http.delete(f"{self._base_url}/sessions/{session_id}")).json()
        self.report(
            "an idle session is ended by the idle timeout",
            summary.get("ended_by") == "idle_timeout",
            f"ended_by={summary.get('ended_by')}",
        )

    async def _upstream_down(self, http: httpx.AsyncClient) -> None:
        session_id = await self._register(http)
        outcome = "no error"
        try:
            with anyio.fail_after(10):
                async with AsyncExitStack() as stack:
                    await self._open(stack, self.url(session_id, "down"))
        except TimeoutError:
            outcome = "hung"
        except Exception as error:
            outcome = "error" if "unavailable" in repr(error) else repr(error)[:120]
        await http.delete(f"{self._base_url}/sessions/{session_id}")
        self.report(
            "a client of a server that is down gets an error promptly",
            outcome == "error",
            outcome,
        )

    async def _register(self, http: httpx.AsyncClient) -> str:
        session_id = str(uuid.uuid4())
        response = await http.post(
            f"{self._base_url}/sessions",
            json={
                "session_id": session_id,
                "trusted_inputs": [{"kind": "user_prompt", "content": "check"}],
            },
        )
        response.raise_for_status()
        return session_id

    @staticmethod
    async def _open(stack: AsyncExitStack, url: str) -> ClientSession:
        read, write, _ = await stack.enter_async_context(streamable_http_client(url))
        session = await stack.enter_async_context(ClientSession(read, write))
        await session.initialize()
        return session


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


async def _run(server_url: str, tool: str) -> int:
    upstreams = {
        name: HttpUpstream(name=name, url=server_url)
        for name in ("weather", "weather_b")
    }
    # Nothing listens on a port just found free, so this upstream is down.
    upstreams["down"] = HttpUpstream(
        name="down", url=f"http://127.0.0.1:{_free_port()}/mcp"
    )
    with tempfile.TemporaryDirectory() as log_dir:
        port = _free_port()
        app = build_app(upstreams, Path(log_dir), _IDLE_TIMEOUT)
        server = uvicorn.Server(
            uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning")
        )
        checks = _Checks(
            f"http://127.0.0.1:{port}", Path(log_dir) / "refused.jsonl", tool
        )
        async with anyio.create_task_group() as tg:
            tg.start_soon(server.serve)
            while not server.started:
                await anyio.sleep(0.05)
            async with httpx.AsyncClient() as http:
                await checks.run(http)
            server.should_exit = True
    return 1 if checks.failures else 0


def main() -> None:
    """Run the Phase 2 session checks against one running HTTP MCP server."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("server_url", help="URL of a running HTTP MCP server")
    parser.add_argument(
        "tool", help="name of a tool on that server that takes no arguments"
    )
    args = parser.parse_args()
    # Show the gateway's warnings and errors alongside the results.
    logging.basicConfig(
        level=logging.WARNING, format="  %(levelname)s %(name)s: %(message)s"
    )
    sys.exit(asyncio.run(_run(args.server_url, args.tool)))


if __name__ == "__main__":
    main()
