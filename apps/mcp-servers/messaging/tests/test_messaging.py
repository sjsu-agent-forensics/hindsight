"""Tests for messaging-server: reading channels, posting, DMs, and per-session isolation."""

import pytest
from fastmcp import Client
from fastmcp.exceptions import ToolError
from hindsight_server_kit.testing import call, running

from messaging_server.main import server, store


@pytest.fixture(scope="module")
def live():
    with running(server, store) as live:
        yield live


def test_read_channel_oldest_first_with_names():
    sales = call(Client(server), "read_channel", {"channel": "#sales"})
    assert [m["message_id"] for m in sales["messages"]] == ["m_003", "m_004"]
    assert sales["messages"][0]["name"] == "Jamie Park" and sales["external"] is False
    assert (
        len(call(Client(server), "read_channel", {"channel": "sales", "limit": 1})["messages"]) == 1
    )


def test_errors(live):
    with pytest.raises(ToolError, match="channels are general"):
        live.call("read_channel", {"channel": "random"})
    with pytest.raises(ToolError, match="No user"):
        live.call("send_dm", {"user": "nobody", "text": "hi"})


def test_post_to_external_channel_is_flagged(live):
    live.seed("s-post")
    live.seed("s-other")
    posted = live.call(
        "post_message", {"channel": "ext-ergo-supplies", "text": "Can you send the EC-200 restock date?"}, "s-post"
    )
    assert posted == {"posted": True, "channel": "ext-ergo-supplies", "message_id": "m_007"}
    channel = live.call("read_channel", {"channel": "ext-ergo-supplies"}, "s-post")
    assert channel["messages"][-1]["text"] == "Can you send the EC-200 restock date?"
    other = live.call("read_channel", {"channel": "ext-ergo-supplies"}, "s-other")
    assert [m["message_id"] for m in other["messages"]] == ["m_006"]
    [effect] = live.effects("s-post")
    assert effect["tool"] == "post_message" and effect["external"] is True


def test_dm_ids_follow_on_from_channel_ids(live):
    live.seed("s-dm")
    first = live.call("send_dm", {"user": "dana.ergo", "text": "hello"}, "s-dm")
    second = live.call("post_message", {"channel": "general", "text": "hi all"}, "s-dm")
    assert (first["message_id"], second["message_id"]) == ("m_007", "m_008")
    assert [(e["tool"], e["external"]) for e in live.effects("s-dm")] == [
        ("send_dm", True),
        ("post_message", False),
    ]
