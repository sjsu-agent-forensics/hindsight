"""Check how the gateway supervises stdio servers, against Phase 4 exit criteria.

Starts its own gateway in-process (free port, temporary directories, a 10 s upstream
timeout) serving the faults test server (``apps/mcp-servers/faults``) over stdio, then:

1. Two concurrent sessions get separate child processes, and each set is gone after its
   session ends. A second MCP session to the same stdio server in one session is refused
   and logged.
2. A call the server does not answer in time gets an MCP error and a ``timeout`` event,
   distinct from a block: the call's decision is still ``allow``.
3. Killing a child mid-session makes every later call to it fail, with one ``crash``
   event in the trace.

Needs ``uv sync --project apps/mcp-servers/faults`` first. Exits 0 when every check
passes, 1 otherwise.
"""

import asyncio
import json
import logging
import os
import signal
import sys
import tempfile
import time
import uuid
from contextlib import AsyncExitStack
from pathlib import Path

import anyio
import httpx
import trace_schema as ts
import uvicorn
from check_sessions import _free_port
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

from interposer.config import load_config
from interposer.gateway import build_app

_FAULTS_PROJECT = Path(__file__).resolve().parents[2] / "mcp-servers" / "faults"
# Longer than a FastMCP server takes to answer its first request on a slow machine
# (about 6 s measured on Windows), so startup alone never times out.
_UPSTREAM_TIMEOUT = 10.0


class _Checks:
    def __init__(self, base_url: str, log_dir: Path) -> None:
        self._base_url = base_url
        self._log_dir = log_dir
        self.failures = 0

    def report(self, name: str, passed: bool, detail: str = "") -> None:
        print(
            f"{'PASS' if passed else 'FAIL'}: {name}{f' ({detail})' if detail else ''}"
        )
        if not passed:
            self.failures += 1

    async def run(self, http: httpx.AsyncClient) -> None:
        await self._separate_children(http)
        await self._timeout(http)
        await self._crash(http)

    async def _separate_children(self, http: httpx.AsyncClient) -> None:
        (a, _), (b, _) = [await self._register(http) for _ in range(2)]
        async with AsyncExitStack() as stack:
            pids = [
                _pid(await (await self._open(stack, s)).call_tool("pid", {}))
                for s in (a, b)
            ]
            second = await http.post(
                f"/s/{a}/mcp/faults",
                json={"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
                headers={"Accept": "application/json, text/event-stream"},
            )
            await http.delete(f"/sessions/{a}")
            a_gone = await _gone_within(pids[0], 6)
            b_alive_meanwhile = _alive(pids[1])
            await http.delete(f"/sessions/{b}")
            b_gone = await _gone_within(pids[1], 6)
        self.report(
            "two sessions get separate children",
            pids[0] != pids[1] and b_alive_meanwhile,
            f"pids {pids[0]} and {pids[1]}",
        )
        self.report(
            "each session's child is gone after the session ends", a_gone and b_gone
        )
        self.report(
            "a second MCP session to a stdio server is refused and logged",
            second.status_code == 409 and "stdio_server_in_use" in self._refusals(),
            f"HTTP {second.status_code}",
        )

    async def _timeout(self, http: httpx.AsyncClient) -> None:
        session_id, trace_dir = await self._register(http)
        started = time.monotonic()
        outcome = "answered"
        async with AsyncExitStack() as stack:
            session = await self._open(stack, session_id)
            try:
                await session.call_tool("slow", {"seconds": _UPSTREAM_TIMEOUT + 5})
            except Exception as error:
                outcome = (
                    "timeout error" if "did not answer" in str(error) else repr(error)
                )
            elapsed = time.monotonic() - started
            await http.delete(f"/sessions/{session_id}")
        events = _events(trace_dir)
        timeouts = [e for e in events if e[ts.EVENT_TYPE] == "timeout"]
        decisions = [e for e in events if e[ts.EVENT_TYPE] == "decision"]
        self.report(
            "an unanswered call gets an MCP error, then a timeout event, not a block",
            outcome == "timeout error"
            and elapsed < _UPSTREAM_TIMEOUT * 2
            and len(timeouts) == 1
            and timeouts[0][ts.TOOL_NAME] == "slow"
            and [d[ts.DECISION] for d in decisions] == ["allow"]
            and not any(e[ts.EVENT_TYPE] == "response" for e in events),
            f"{outcome} after {elapsed:.1f} s",
        )

    async def _crash(self, http: httpx.AsyncClient) -> None:
        session_id, trace_dir = await self._register(http)
        failures = []
        async with AsyncExitStack() as stack:
            session = await self._open(stack, session_id)
            pid = _pid(await session.call_tool("pid", {}))
            _kill(pid)
            await _gone_within(pid, 6)
            for _ in range(2):
                started = time.monotonic()
                try:
                    with anyio.fail_after(5):
                        await session.call_tool("pid", {})
                    failures.append("answered")
                except TimeoutError:
                    failures.append("hung")
                except Exception as error:
                    failed_fast = time.monotonic() - started < 1
                    failures.append(
                        "unavailable"
                        if "unavailable" in str(error) and failed_fast
                        else repr(error)[:80]
                    )
            await http.delete(f"/sessions/{session_id}")
        crashes = [e for e in _events(trace_dir) if e[ts.EVENT_TYPE] == "crash"]
        self.report(
            "after the child is killed, every call fails, with one crash event",
            failures == ["unavailable", "unavailable"] and len(crashes) == 1,
            f"calls: {failures}; crash events: {len(crashes)}",
        )

    async def _register(self, http: httpx.AsyncClient) -> tuple[str, Path]:
        session_id = str(uuid.uuid4())
        response = await http.post(
            "/sessions",
            json={
                "session_id": session_id,
                "trusted_inputs": [{"kind": "user_prompt", "content": "check"}],
            },
        )
        response.raise_for_status()
        return session_id, Path(response.json()["trace_dir"])

    async def _open(self, stack: AsyncExitStack, session_id: str) -> ClientSession:
        url = f"{self._base_url}/s/{session_id}/mcp/faults"
        read, write, _ = await stack.enter_async_context(streamable_http_client(url))
        session = await stack.enter_async_context(ClientSession(read, write))
        await session.initialize()
        return session

    def _refusals(self) -> list[str]:
        path = self._log_dir / "refused.jsonl"
        lines = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
        return [json.loads(line)["reason"] for line in lines]


def _pid(result) -> int:
    return int(result.content[0].text)


def _events(trace_dir: Path) -> list[dict]:
    return ts.read_events(trace_dir / ts.PROXY_TRACE_FILE)


def _alive(pid: int) -> bool:
    """Return whether a process is running, without signalling it."""
    if sys.platform == "win32":
        import ctypes

        kernel32 = ctypes.windll.kernel32
        handle = kernel32.OpenProcess(0x1000, False, pid)  # QUERY_LIMITED_INFORMATION
        if not handle:
            return False
        exit_code = ctypes.c_ulong()
        kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code))
        kernel32.CloseHandle(handle)
        return exit_code.value == 259  # STILL_ACTIVE
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


def _kill(pid: int) -> None:
    """Kill a process outright, as a crash would."""
    # On Windows, any signal but the console ones terminates the process.
    os.kill(pid, signal.SIGTERM if sys.platform == "win32" else signal.SIGKILL)


async def _gone_within(pid: int, seconds: float) -> bool:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if not _alive(pid):
            return True
        await anyio.sleep(0.1)
    return not _alive(pid)


async def _run() -> int:
    with tempfile.TemporaryDirectory() as temp_dir:
        temp = Path(temp_dir)
        config = temp / "servers.toml"
        config.write_text(
            f'[servers.faults]\nproject = "{_FAULTS_PROJECT.as_posix()}"\n'
            'program = "faults-server"\n',
            encoding="utf-8",
        )
        port = _free_port()
        app = build_app(
            load_config(config),
            temp / "logs",
            temp / "traces",
            idle_timeout=60,
            upstream_timeout=_UPSTREAM_TIMEOUT,
        )
        server = uvicorn.Server(
            uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning")
        )
        base_url = f"http://127.0.0.1:{port}"
        checks = _Checks(base_url, temp / "logs")
        async with anyio.create_task_group() as tg:
            tg.start_soon(server.serve)
            while not server.started:
                await anyio.sleep(0.05)
            async with httpx.AsyncClient(base_url=base_url, timeout=30) as http:
                await checks.run(http)
            server.should_exit = True
    return 1 if checks.failures else 0


def main() -> None:
    """Run the Phase 4 process-management checks."""
    logging.basicConfig(
        level=logging.WARNING, format="  %(levelname)s %(name)s: %(message)s"
    )
    sys.exit(asyncio.run(_run()))


if __name__ == "__main__":
    main()
