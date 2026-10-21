"""Tests for email-server: reading the mailbox, sending, and per-session isolation."""

import pytest
from fastmcp import Client
from fastmcp.exceptions import ToolError
from hindsight_server_kit.testing import call, running

from email_server.main import server, store


@pytest.fixture(scope="module")
def live():
    with running(server, store) as live:
        yield live


def test_default_inbox_lists_newest_first_without_bodies():
    inbox = call(Client(server), "list_emails")["emails"]
    assert [e["email_id"] for e in inbox] == ["e_001", "e_002", "e_003", "e_004"]
    assert "body" not in inbox[0]
    sent = call(Client(server), "list_emails", {"folder": "sent"})["emails"]
    assert [e["email_id"] for e in sent] == ["e_005"]


def test_read_email_marks_it_read(live):
    live.seed("s-read")
    email = live.call("read_email", {"email_id": "e_002"}, "s-read")
    assert email["from"]["name"] == "Rosa Singh" and "tracking" in email["body"]
    inbox = live.call("list_emails", {}, "s-read")["emails"]
    assert next(e for e in inbox if e["email_id"] == "e_002")["read"] is True


def test_search_matches_body_and_sender(live):
    found = live.call("search_emails", {"query": "RENEWAL"})["emails"]
    assert {e["email_id"] for e in found} == {"e_001", "e_005"}
    assert live.call("search_emails", {"query": "ergosupplies"})["emails"][0]["email_id"] == "e_003"


def test_errors(live):
    with pytest.raises(ToolError, match="No email"):
        live.call("read_email", {"email_id": "e_999"})
    with pytest.raises(ToolError, match="Unknown folder"):
        live.call("list_emails", {"folder": "spam"})
    with pytest.raises(ToolError, match="Not an email address"):
        live.call("send_email", {"to": ["bob"], "subject": "s", "body": "b"})


def test_send_is_stored_logged_and_isolated(live):
    live.seed("s-send")
    live.seed("s-other")
    sent = live.call(
        "send_email",
        {"to": ["dana@ergosupplies.example"], "subject": "Order update", "body": "Can you confirm the EC-200 delivery date?"},
        "s-send",
    )
    assert sent == {
        "sent": True,
        "email_id": "e_006",
        "to": ["dana@ergosupplies.example"],
        "cc": [],
    }
    assert (
        live.call("list_emails", {"folder": "sent"}, "s-send")["emails"][0]["email_id"] == "e_006"
    )
    assert live.effects("s-send") == [
        {
            "seq": 1,
            "tool": "send_email",
            "at": "2026-11-06T09:00:00-08:00",
            "email_id": "e_006",
            "to": ["dana@ergosupplies.example"],
            "cc": [],
            "subject": "Order update",
            "body": "Can you confirm the EC-200 delivery date?",
        }
    ]
    assert len(live.call("list_emails", {"folder": "sent"}, "s-other")["emails"]) == 1
    assert live.effects("s-other") == []


def test_reset_gives_identical_ids(live):
    args = {"to": ["jamie.park@northwind.example"], "subject": "hi", "body": "hi"}
    live.seed("s-rerun")
    first = live.call("send_email", args, "s-rerun")
    live.seed("s-rerun")
    assert live.call("send_email", args, "s-rerun") == first
