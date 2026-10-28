"""Integration tests for StdioProcessClient communicating with mock public tool server."""

import sys
import pytest

from public_bridge.stdio_process import StdioProcessClient


@pytest.mark.asyncio
async def test_stdio_lifecycle() -> None:
    """Test full stdio lifecycle: handshake, tool discovery, execution, ping, shutdown."""
    client = StdioProcessClient(
        name="test_mock",
        command=sys.executable,
        args=["-m", "public_bridge.mock_server"],
    )
    await client.start()
    assert client.is_running

    try:
        # Liveness ping
        assert await client.ping() is True

        # Tool discovery
        tools = await client.list_tools()
        tool_names = [t["name"] for t in tools]
        assert "fetch" in tool_names
        assert "read_file" in tool_names
        assert "write_file" in tool_names

        # Call fetch tool
        fetch_res = await client.call_tool(
            "fetch", {"url": "https://api.example.com/data"}
        )
        content = fetch_res.get("content", [])
        assert len(content) > 0
        assert "https://api.example.com/data" in content[0]["text"]

        # Call write_file and read_file tools
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as td:
            target_path = str(Path(td) / "test.txt")
            write_res = await client.call_tool(
                "write_file", {"path": target_path, "content": "forensics payload"}
            )
            assert "written" in write_res["content"][0]["text"]

            read_res = await client.call_tool("read_file", {"path": target_path})
            assert read_res["content"][0]["text"] == "forensics payload"
    finally:
        await client.stop()
        assert not client.is_running
