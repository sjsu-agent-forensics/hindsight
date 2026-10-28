"""Streamable HTTP bridge fronting third-party public MCP tools.

Exposes public MCP tools over streamable HTTP at /api/mcp bound to 127.0.0.1, translating
incoming agent/interposer requests to the underlying third-party process (stdio or HTTP) and
recording tool effects in the session sandbox (E-10, N-06).
"""

import asyncio
from collections.abc import Sequence
import logging
from typing import Any

import httpx
from mcp.server.mcpserver import Context, MCPServer
from mcp.server.transport_security import TransportSecuritySettings
from mcp.types import CallToolResult, TextContent, Tool as MCPTool
from starlette.applications import Starlette

from public_bridge.config import (
    HttpPublicServerConfig,
    PublicServerConfig,
    StdioPublicServerConfig,
)
from public_bridge.sandbox import SessionSandboxManager
from public_bridge.stdio_process import StdioProcessClient

logger = logging.getLogger(__name__)

# Loopback host patterns allowed through DNS rebinding validation.
_ALLOWED_HOSTS = [
    "127.0.0.1:*",
    "localhost:*",
    "127.0.0.1",
    "localhost",
    "testserver",
]


class PublicBridgeServer(MCPServer):
    """MCP Streamable HTTP server bridging requests to an underlying public tool."""

    def __init__(
        self,
        config: PublicServerConfig,
        sandbox_manager: SessionSandboxManager,
        stdio_client: StdioProcessClient | None = None,
    ) -> None:
        super().__init__(name=config.name)
        self.config = config
        self.sandbox_manager = sandbox_manager
        self.stdio_client = stdio_client
        self._cached_tools: dict[str, dict[str, Any]] = {}
        self._http_client: httpx.AsyncClient | None = None

    async def list_tools(self) -> list[MCPTool]:
        """Fetch and return tools exposed by the upstream public server."""
        raw_tools = await self._fetch_upstream_tools()
        allowed = set(self.config.allowed_tools) if self.config.allowed_tools else None

        tools: list[MCPTool] = []
        self._cached_tools.clear()

        for tool_dict in raw_tools:
            name = tool_dict.get("name", "")
            if allowed is not None and name not in allowed:
                continue

            schema = tool_dict.get("inputSchema", {"type": "object"})
            desc = tool_dict.get("description", "")
            self._cached_tools[name] = {"description": desc, "schema": schema}

            tools.append(
                MCPTool(
                    name=name,
                    description=desc,
                    input_schema=schema,
                )
            )
        return tools

    def _tool_input_schema(self, name: str) -> dict[str, Any] | None:
        """Return the cached input schema for tool parameter validation."""
        tool_info = self._cached_tools.get(name)
        if tool_info is not None:
            return tool_info.get("schema")
        return {"type": "object"}

    async def call_tool(
        self,
        name: str,
        arguments: dict[str, Any],
        context: Context[Any, Any] | None = None,
    ) -> CallToolResult:
        """Execute a tool call against the upstream public server and log effects.

        Args:
            name: Tool name being invoked.
            arguments: Arguments payload for the tool.
            context: Server request context.

        Returns:
            CallToolResult with formatted output and error status.
        """
        allowed = set(self.config.allowed_tools) if self.config.allowed_tools else None
        if allowed is not None and name not in allowed:
            return CallToolResult(
                content=[
                    TextContent(type="text", text=f"Tool {name!r} is not allowed")
                ],
                is_error=True,
            )

        sid = self._resolve_session_id(context)
        self.sandbox_manager.record_effect(
            sid,
            "call_tool",
            tool=name,
            arguments=arguments,
        )

        try:
            result = await self._execute_upstream_tool(name, arguments, sid)
            return _format_call_result(result)
        except Exception as exc:
            logger.exception("Upstream tool execution failed for %r", name)
            return CallToolResult(
                content=[
                    TextContent(type="text", text=f"Tool execution failed: {exc}")
                ],
                is_error=True,
            )

    async def _fetch_upstream_tools(self) -> Sequence[dict[str, Any]]:
        """Query upstream tools list via stdio process or HTTP upstream."""
        if isinstance(self.config, StdioPublicServerConfig):
            if self.stdio_client is None:
                raise RuntimeError(
                    f"Stdio client not initialized for {self.config.name}"
                )
            return await self.stdio_client.list_tools()

        if isinstance(self.config, HttpPublicServerConfig):
            client = self._get_http_client()
            resp = await client.post(
                self.config.target_url,
                json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
                headers=self.config.headers,
            )
            resp.raise_for_status()
            data = resp.json()
            return data.get("result", {}).get("tools", [])

        return []

    async def _execute_upstream_tool(
        self, name: str, arguments: dict[str, Any], session_id: str
    ) -> dict[str, Any]:
        """Dispatch tool call to stdio client or HTTP target."""
        if isinstance(self.config, StdioPublicServerConfig):
            if self.stdio_client is None:
                raise RuntimeError(
                    f"Stdio client not initialized for {self.config.name}"
                )
            return await self.stdio_client.call_tool(name, arguments)

        if isinstance(self.config, HttpPublicServerConfig):
            client = self._get_http_client()
            headers = dict(self.config.headers)
            headers["Hindsight-Session"] = session_id
            resp = await client.post(
                self.config.target_url,
                json={
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "tools/call",
                    "params": {"name": name, "arguments": arguments},
                },
                headers=headers,
            )
            resp.raise_for_status()
            data = resp.json()
            if "error" in data:
                raise RuntimeError(data["error"].get("message", "HTTP upstream error"))
            return data.get("result", {})

        raise ValueError(f"Unsupported config transport: {self.config.transport}")

    def _get_http_client(self) -> httpx.AsyncClient:
        """Lazily initialize an AsyncClient for HTTP upstreams."""
        if self._http_client is None:
            self._http_client = httpx.AsyncClient(timeout=self.config.startup_timeout)
        return self._http_client

    def _resolve_session_id(self, context: Context[Any, Any] | None) -> str:
        """Extract hindsight session ID from request headers or return default session."""
        if context is not None and hasattr(context, "request_context"):
            req_ctx = context.request_context
            if hasattr(req_ctx, "headers") and req_ctx.headers:
                header_val = req_ctx.headers.get("hindsight-session")
                if header_val:
                    return str(header_val)
        return "default"

    def build_app(self) -> Starlette:
        """Construct the Starlette ASGI app exposing /api/mcp."""
        security = TransportSecuritySettings(
            allowed_hosts=_ALLOWED_HOSTS,
            enable_dns_rebinding_protection=True,
        )
        return self.streamable_http_app(
            streamable_http_path="/api/mcp",
            transport_security=security,
        )

    async def close(self) -> None:
        """Clean up background resources and clients."""
        if self._http_client is not None:
            await self._http_client.aclose()
        if self.stdio_client is not None:
            await self.stdio_client.stop()


def _format_call_result(raw_result: dict[str, Any]) -> CallToolResult:
    """Convert upstream result dictionary into a typed CallToolResult."""
    contents: list[TextContent] = []
    raw_content = raw_result.get("content", [])

    if isinstance(raw_content, list):
        for item in raw_content:
            if isinstance(item, dict) and item.get("type") == "text":
                contents.append(
                    TextContent(type="text", text=str(item.get("text", "")))
                )
            elif isinstance(item, str):
                contents.append(TextContent(type="text", text=item))
    elif isinstance(raw_content, str):
        contents.append(TextContent(type="text", text=raw_content))

    if not contents and "text" in raw_result:
        contents.append(TextContent(type="text", text=str(raw_result["text"])))

    is_error = bool(raw_result.get("isError", False))
    return CallToolResult(content=contents, is_error=is_error)
