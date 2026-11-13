"""Integration tests for StdioProcessClient communicating with mock workspace server."""

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

        # Tool discovery — verify personal workspace tools
        tools = await client.list_tools()
        tool_names = [t["name"] for t in tools]
        assert "gdrive_search" in tool_names
        assert "gdrive_read_file" in tool_names
        assert "gdrive_list_files" in tool_names
        assert "gcalendar_list_events" in tool_names
        assert "gcalendar_create_event" in tool_names
        assert "gmail_search" in tool_names
        assert "gmail_read" in tool_names
        assert "gmail_draft" in tool_names

        # Call gdrive_search tool
        search_res = await client.call_tool(
            "gdrive_search", {"query": "budget"}
        )
        content = search_res.get("content", [])
        assert len(content) > 0
        assert "Budget 2026" in content[0]["text"]

        # Call gdrive_read_file tool
        read_res = await client.call_tool(
            "gdrive_read_file", {"file_id": "doc_001"}
        )
        read_content = read_res.get("content", [])
        assert len(read_content) > 0
        assert "Q4 Planning Notes" in read_content[0]["text"]

        # Call gcalendar_list_events tool
        events_res = await client.call_tool("gcalendar_list_events", {})
        events_content = events_res.get("content", [])
        assert len(events_content) > 0
        assert "Team Standup" in events_content[0]["text"]

        # Call gmail_search tool
        mail_res = await client.call_tool(
            "gmail_search", {"query": "planning"}
        )
        mail_content = mail_res.get("content", [])
        assert len(mail_content) > 0
        assert "Q4 Planning" in mail_content[0]["text"]

        # Call gmail_read tool
        read_mail_res = await client.call_tool(
            "gmail_read", {"message_id": "msg_001"}
        )
        read_mail_content = read_mail_res.get("content", [])
        assert len(read_mail_content) > 0
        assert "bob@example.com" in read_mail_content[0]["text"]

        # Call gmail_draft tool
        draft_res = await client.call_tool(
            "gmail_draft",
            {
                "to": "bob@example.com",
                "subject": "Re: Q4 Planning",
                "body": "Thanks for the feedback!",
            },
        )
        draft_content = draft_res.get("content", [])
        assert len(draft_content) > 0
        assert "draft" in draft_content[0]["text"].lower()
    finally:
        await client.stop()
        assert not client.is_running
