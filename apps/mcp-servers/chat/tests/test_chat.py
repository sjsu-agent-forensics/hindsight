"""Tests for chat-server: channels and direct messages, external flags and user management."""

import asyncio

import pytest
from fastmcp import Client
from fastmcp.exceptions import ToolError
from hindsight_server_kit import manifest
from hindsight_server_kit.testing import call, running

from chat_server.main import server, store

TOOLS = {
    "get_channels",
    "read_channel_messages",
    "read_inbox",
    "get_users_in_channel",
    "send_channel_message",
    "send_direct_message",
    "add_user_to_channel",
    "invite_user_to_slack",
    "remove_user_from_slack",
}
NOW = "2026-11-06T09:00:00-08:00"


@pytest.fixture(scope="module")
def live():
    with running(server, store) as live:
        yield live


def test_manifest_lists_the_agentdojo_slack_tools():
    assert {t["name"] for t in asyncio.run(manifest(server))["tools"]} == TOOLS


def test_channels_and_messages(live):
    assert call(Client(server), "get_channels")["channels"] == [
        "general",
        "sales",
        "random",
        "ext-ergo-supplies",
    ]
    messages = live.call("read_channel_messages", {"channel": "#ext-ergo-supplies"})["messages"]
    assert len(messages) == 11 and messages[0]["message_id"] == "m_003"
    assert {m["sender"] for m in messages} == {"alex", "dana", "luis", "casey"}
    users = live.call("get_users_in_channel", {"channel": "ext-ergo-supplies"})["users"]
    assert users == ["alex", "casey", "jamie", "dana", "luis"]


def test_direct_messages(live):
    inbox = live.call("read_inbox", {"user": "alex"})["messages"]
    assert [m["message_id"] for m in inbox][:2] == ["m_013", "m_023"]
    live.seed("s-dm")
    sent = live.call("send_direct_message", {"recipient": "@Jamie", "body": "hi"}, "s-dm")
    assert sent == {
        "message_id": "m_061",
        "sender": "alex",
        "body": "hi",
        "timestamp": NOW,
        "recipient": "jamie",
    }
    assert live.call("read_inbox", {"user": "jamie"}, "s-dm")["messages"][-1] == sent
    assert live.effects("s-dm") == [
        {
            "seq": 1,
            "tool": "send_direct_message",
            "at": NOW,
            "message_id": "m_061",
            "recipient": "jamie",
            "body": "hi",
            "external": False,
        }
    ]


def test_external_channel_posts_are_flagged(live):
    live.seed("s-post")
    live.seed("s-other")
    live.call("send_channel_message", {"channel": "sales", "body": "internal"}, "s-post")
    live.call("send_channel_message", {"channel": "ext-ergo-supplies", "body": "out"}, "s-post")
    live.call("send_direct_message", {"recipient": "dana", "body": "out too"}, "s-post")
    assert [(e["message_id"], e["external"]) for e in live.effects("s-post")] == [
        ("m_061", False),
        ("m_062", True),
        ("m_063", True),
    ]
    assert (
        len(live.call("read_channel_messages", {"channel": "sales"}, "s-other")["messages"]) == 16
    )
    assert live.effects("s-other") == []


def test_user_management_is_logged(live):
    live.seed("s-users")
    added = live.call("add_user_to_channel", {"user": "dana", "channel": "sales"}, "s-users")
    assert added["users"][-1] == "dana"
    new = live.call(
        "invite_user_to_slack", {"user": "Pat", "user_email": "pat@example.com"}, "s-users"
    )
    assert new == {"user": "pat", "name": "pat", "email": "pat@example.com", "external": True}
    removed = live.call("remove_user_from_slack", {"user": "luis"}, "s-users")
    assert removed == {"removed": True, "user": "luis"}
    assert (
        "luis"
        not in live.call("get_users_in_channel", {"channel": "ext-ergo-supplies"}, "s-users")[
            "users"
        ]
    )
    assert [(e["tool"], e["external"]) for e in live.effects("s-users")] == [
        ("add_user_to_channel", True),
        ("invite_user_to_slack", True),
        ("remove_user_from_slack", True),
    ]
    assert live.effects("s-users")[2]["channels"] == ["ext-ergo-supplies"]


def test_errors(live):
    with pytest.raises(ToolError, match="No channel"):
        live.call("read_channel_messages", {"channel": "secret"})
    with pytest.raises(ToolError, match="No user"):
        live.call("send_direct_message", {"recipient": "nobody", "body": "x"})
    with pytest.raises(ToolError, match="already in"):
        live.call("add_user_to_channel", {"user": "alex", "channel": "sales"})
    with pytest.raises(ToolError, match="already in the workspace"):
        live.call("invite_user_to_slack", {"user": "jamie", "user_email": "j@example.com"})
    with pytest.raises(ToolError, match="cannot remove themselves"):
        live.call("remove_user_from_slack", {"user": "alex"})
