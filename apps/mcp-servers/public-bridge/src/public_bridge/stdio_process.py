"""Subprocess supervisor and client for stdio-based public MCP tool servers.

Spawns third-party MCP servers as local subprocesses, multiplexes JSON-RPC communication
over stdin/stdout, captures and tags stderr, and provides graceful process lifecycle management.
"""

import asyncio
from collections.abc import AsyncGenerator
import itertools
import json
import logging
from pathlib import Path
import sys
from typing import Any

logger = logging.getLogger(__name__)


class StdioProcessClient:
    """Async supervisor and JSON-RPC client for a stdio MCP server subprocess."""

    def __init__(
        self,
        name: str,
        command: str,
        args: list[str] | None = None,
        env: dict[str, str] | None = None,
        cwd: Path | None = None,
        startup_timeout: float = 30.0,
    ) -> None:
        self.name = name
        self.command = command
        self.args = args or []
        self.env = env or {}
        self.cwd = cwd
        self.startup_timeout = startup_timeout

        self._process: asyncio.subprocess.Process | None = None
        self._pending: dict[int, asyncio.Future[dict[str, Any]]] = {}
        self._id_counter = itertools.count(1)
        self._reader_task: asyncio.Task[None] | None = None
        self._stderr_task: asyncio.Task[None] | None = None
        self._server_info: dict[str, Any] = {}
        self._capabilities: dict[str, Any] = {}
        self._stopped = False

    @property
    def is_running(self) -> bool:
        """Return True if the subprocess is active and listening."""
        return self._process is not None and self._process.returncode is None

    async def start(self) -> None:
        """Spawn the subprocess and perform the MCP initialization handshake.

        Raises:
            RuntimeError: Process failed to spawn or initialization timed out.
        """
        import os

        merged_env = dict(os.environ)
        merged_env.update(self.env)

        cmd = [self.command, *self.args]
        try:
            self._process = await asyncio.create_subprocess_exec(
                *cmd,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=merged_env,
                cwd=str(self.cwd) if self.cwd else None,
            )
        except Exception as exc:
            raise RuntimeError(
                f"Failed to spawn public server {self.name!r}: {exc}"
            ) from exc

        self._reader_task = asyncio.create_task(self._read_stdout())
        self._stderr_task = asyncio.create_task(self._read_stderr())

        try:
            await asyncio.wait_for(self._initialize(), timeout=self.startup_timeout)
        except Exception as exc:
            await self.stop()
            raise RuntimeError(
                f"Initialization of public server {self.name!r} failed: {exc}"
            ) from exc

    async def _initialize(self) -> None:
        """Perform MCP initialize handshake and send initialized notification."""
        init_params = {
            "protocolVersion": "2024-11-05",
            "capabilities": {
                "tools": {"listChanged": True},
            },
            "clientInfo": {
                "name": "hindsight-public-bridge",
                "version": "0.1.0",
            },
        }
        res = await self.send_request("initialize", init_params)
        self._server_info = res.get("serverInfo", {})
        self._capabilities = res.get("capabilities", {})
        await self.send_notification("notifications/initialized")

    async def send_request(
        self, method: str, params: dict[str, Any] | None = None, timeout: float = 30.0
    ) -> dict[str, Any]:
        """Send a JSON-RPC request to the subprocess and wait for the response.

        Args:
            method: MCP JSON-RPC method name.
            params: Optional method parameters.
            timeout: Seconds to await response before raising TimeoutError.

        Returns:
            The 'result' dictionary from the JSON-RPC response.

        Raises:
            RuntimeError: Subprocess is not running or returned a JSON-RPC error.
            TimeoutError: Response was not received within the timeout window.
        """
        if not self.is_running or self._process is None or self._process.stdin is None:
            raise RuntimeError(f"Server {self.name!r} subprocess is not running")

        req_id = next(self._id_counter)
        loop = asyncio.get_running_loop()
        future: asyncio.Future[dict[str, Any]] = loop.create_future()
        self._pending[req_id] = future

        msg: dict[str, Any] = {"jsonrpc": "2.0", "id": req_id, "method": method}
        if params is not None:
            msg["params"] = params

        payload = json.dumps(msg) + "\n"
        try:
            self._process.stdin.write(payload.encode("utf-8"))
            await self._process.stdin.drain()
        except Exception as exc:
            self._pending.pop(req_id, None)
            raise RuntimeError(
                f"Failed writing to server {self.name!r} stdin: {exc}"
            ) from exc

        try:
            return await asyncio.wait_for(future, timeout=timeout)
        except asyncio.TimeoutError:
            self._pending.pop(req_id, None)
            raise TimeoutError(
                f"Request {method!r} (id={req_id}) to {self.name!r} timed out after {timeout}s"
            )

    async def send_notification(
        self, method: str, params: dict[str, Any] | None = None
    ) -> None:
        """Send a JSON-RPC notification (no id, expects no answer)."""
        if not self.is_running or self._process is None or self._process.stdin is None:
            return

        msg: dict[str, Any] = {"jsonrpc": "2.0", "method": method}
        if params is not None:
            msg["params"] = params

        payload = json.dumps(msg) + "\n"
        try:
            self._process.stdin.write(payload.encode("utf-8"))
            await self._process.stdin.drain()
        except Exception:
            logger.warning("Failed sending notification %r to %s", method, self.name)

    async def list_tools(self) -> list[dict[str, Any]]:
        """Query tools/list from the public MCP server."""
        res = await self.send_request("tools/list")
        return res.get("tools", [])

    async def call_tool(
        self, tool_name: str, arguments: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """Execute a tools/call request on the public MCP server."""
        params = {"name": tool_name, "arguments": arguments or {}}
        return await self.send_request("tools/call", params)

    async def ping(self) -> bool:
        """Send a ping request to verify liveness."""
        try:
            await self.send_request("ping", timeout=3.0)
            return True
        except Exception:
            return False

    async def _read_stdout(self) -> None:
        """Read newline-delimited JSON-RPC responses from subprocess stdout."""
        if self._process is None or self._process.stdout is None:
            return

        while True:
            line = await self._process.stdout.readline()
            if not line:
                break
            text = line.decode("utf-8", errors="replace").strip()
            if not text:
                continue

            try:
                data = json.loads(text)
            except json.JSONDecodeError:
                logger.warning("[%s stdout unparsed]: %s", self.name, text)
                continue

            req_id = data.get("id")
            if req_id is not None and req_id in self._pending:
                fut = self._pending.pop(req_id)
                if not fut.done():
                    if "error" in data:
                        err = data["error"]
                        fut.set_exception(
                            RuntimeError(
                                f"MCP error from {self.name}: {err.get('message', err)}"
                            )
                        )
                    else:
                        fut.set_result(data.get("result", {}))

        # Subprocess closed stdout: fail any remaining pending requests.
        for fut in self._pending.values():
            if not fut.done():
                fut.set_exception(
                    RuntimeError(
                        f"Server {self.name!r} terminated while awaiting response"
                    )
                )
        self._pending.clear()

    async def _read_stderr(self) -> None:
        """Relay subprocess stderr lines to logs with a server prefix."""
        if self._process is None or self._process.stderr is None:
            return

        prefix = f"[public:{self.name}]"
        while True:
            line = await self._process.stderr.readline()
            if not line:
                break
            text = line.decode("utf-8", errors="replace").rstrip()
            print(f"{prefix} {text}", file=sys.stderr, flush=True)

    async def stop(self, grace: float = 3.0) -> None:
        """Terminate the subprocess cleanly and cancel background tasks."""
        if self._stopped:
            return
        self._stopped = True

        if self._process is not None and self._process.returncode is None:
            try:
                self._process.terminate()
                await asyncio.wait_for(self._process.wait(), timeout=grace)
            except (asyncio.TimeoutError, ProcessLookupError):
                try:
                    self._process.kill()
                    await self._process.wait()
                except ProcessLookupError:
                    pass

        if self._reader_task and not self._reader_task.done():
            self._reader_task.cancel()
        if self._stderr_task and not self._stderr_task.done():
            self._stderr_task.cancel()
