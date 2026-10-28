"""End-to-end tests for PublicBridgeServer exposing tools over Streamable HTTP."""

import json
from pathlib import Path
import sys
import httpx
import pytest

from public_bridge.bridge import PublicBridgeServer
from public_bridge.config import StdioPublicServerConfig
from public_bridge.sandbox import SessionSandboxManager
from public_bridge.stdio_process import StdioProcessClient


@pytest.mark.asyncio
async def test_bridge_streamable_http(tmp_path: Path) -> None:
    """Test tools/list and tools/call over the HTTP bridge using mock server."""
    cfg = StdioPublicServerConfig(
        name="mock",
        command=sys.executable,
        args=["-m", "public_bridge.mock_server"],
        port=8021,
        control_port=8121,
    )

    stdio_client = StdioProcessClient(
        name=cfg.name,
        command=cfg.command,
        args=cfg.args,
    )
    await stdio_client.start()

    sandbox_mgr = SessionSandboxManager(
        server_name=cfg.name, base_dir=tmp_path / "sandboxes"
    )
    bridge = PublicBridgeServer(
        config=cfg, sandbox_manager=sandbox_mgr, stdio_client=stdio_client
    )

    try:
        app = bridge.build_app()
        async with app.router.lifespan_context(app):
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(
                transport=transport, base_url="http://testserver"
            ) as client:
                # 1. MCP Initialize
                init_resp = await client.post(
                    "/api/mcp",
                    json={
                        "jsonrpc": "2.0",
                        "id": 1,
                        "method": "initialize",
                        "params": {
                            "protocolVersion": "2024-11-05",
                            "capabilities": {},
                            "clientInfo": {"name": "test_client", "version": "1.0"},
                        },
                    },
                )
                assert init_resp.status_code == 200
                session_id = init_resp.headers.get("mcp-session-id")
                headers = {"mcp-session-id": session_id} if session_id else {}

                # 2. tools/list
                list_resp = await client.post(
                    "/api/mcp",
                    headers=headers,
                    json={"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
                )
                assert list_resp.status_code == 200
                assert "fetch" in list_resp.text
                assert "read_file" in list_resp.text

                # 3. tools/call
                call_resp = await client.post(
                    "/api/mcp",
                    headers=headers,
                    json={
                        "jsonrpc": "2.0",
                        "id": 3,
                        "method": "tools/call",
                        "params": {
                            "name": "fetch",
                            "arguments": {"url": "http://127.0.0.1:8000/report"},
                        },
                    },
                )
                assert call_resp.status_code == 200
                assert "Simulated content fetched" in call_resp.text

                # 4. Verify effect recorded in sandbox
                effects = sandbox_mgr.get_or_create("default").effects
                assert len(effects) == 1
                assert effects[0]["tool"] == "fetch"
    finally:
        await bridge.close()
