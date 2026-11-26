"""Tests for email-server: reading and searching mail, sending, deleting and session isolation."""

import asyncio

import pytest
from fastmcp import Client
from fastmcp.exceptions import ToolError
from hindsight_server_kit import manifest
from hindsight_server_kit.testing import call, running

from email_server.main import server, store

TOOLS = {
    "send_email",
    "delete_email",
    "get_unread_emails",
    "get_received_emails",
    "get_sent_emails",
    "get_draft_emails",
    "search_emails",
    "search_contacts_by_name",
    "search_contacts_by_email",
}


@pytest.fixture(scope="module")
def live():
    with running(server, store) as live:
        yield live


def test_manifest_lists_the_agentdojo_workspace_tools():
    assert {t["name"] for t in asyncio.run(manifest(server))["tools"]} == TOOLS


def test_received_and_unread(live):
    received = call(Client(server), "get_received_emails")["emails"]
    assert len(received) == 29 and received[0]["email_id"] == "e_040"
    assert received[1]["subject"] == "Newsletter goes out Monday"
    live.seed("s-unread")
    unread = live.call("get_unread_emails", {}, "s-unread")["emails"]
    assert [e["email_id"] for e in unread] == [
        "e_039",
        "e_038",
        "e_034",
        "e_033",
        "e_031",
        "e_030",
        "e_029",
    ]
    assert live.call("get_unread_emails", {}, "s-unread")["emails"] == []
    assert len(live.call("get_draft_emails", {}, "s-unread")["emails"]) == 3
    assert len(live.call("get_sent_emails", {}, "s-unread")["emails"]) == 8


def test_search_and_contacts(live):
    found = live.call("search_emails", {"query": "EC-200"})["emails"]
    assert [e["email_id"] for e in found][:2] == ["e_038", "e_029"]
    from_dana = live.call(
        "search_emails", {"query": "order", "sender": "dana.whitfield@ergosupplies.example"}
    )["emails"]
    assert {e["email_id"] for e in from_dana} == {"e_002", "e_005", "e_021", "e_038"}
    contacts = live.call("search_contacts_by_name", {"query": "park"})["contacts"]
    assert [c["email"] for c in contacts] == ["jamie.park@northwind.example"]
    contacts = live.call("search_contacts_by_email", {"query": "ergosupplies"})["contacts"]
    assert [c["name"] for c in contacts] == ["Dana Whitfield", "Luis Ortega"]


def test_send_is_stored_logged_and_isolated(live):
    live.seed("s-send")
    live.seed("s-other")
    sent = live.call(
        "send_email",
        {
            "recipients": ["dana.whitfield@ergosupplies.example"],
            "subject": "Order update",
            "body": "Can you confirm the delivery date?",
            "attachments": ["f_016"],
        },
        "s-send",
    )
    assert sent["email_id"] == "e_041" and sent["folder"] == "sent"
    assert sent["sender"] == "alex.morgan@northwind.example"
    assert sent["timestamp"] == "2026-11-06T09:00:00-08:00"
    assert live.call("get_sent_emails", {}, "s-send")["emails"][0]["email_id"] == "e_041"
    assert live.effects("s-send") == [
        {
            "seq": 1,
            "tool": "send_email",
            "at": "2026-11-06T09:00:00-08:00",
            "email_id": "e_041",
            "recipients": ["dana.whitfield@ergosupplies.example"],
            "cc": [],
            "bcc": [],
            "subject": "Order update",
            "body": "Can you confirm the delivery date?",
            "attachments": ["f_016"],
            "external": True,
        }
    ]
    assert len(live.call("get_sent_emails", {}, "s-other")["emails"]) == 8
    assert live.effects("s-other") == []
    args = {"recipients": ["jamie.park@northwind.example"], "subject": "hi", "body": "hi"}
    live.seed("s-rerun")
    first = live.call("send_email", args, "s-rerun")
    live.seed("s-rerun")
    assert live.call("send_email", args, "s-rerun") == first
    assert live.effects("s-rerun")[0]["external"] is False


def test_delete_moves_to_trash(live):
    live.seed("s-delete")
    assert live.call("delete_email", {"email_id": "e_009"}, "s-delete")["folder"] == "trash"
    received = live.call("get_received_emails", {}, "s-delete")["emails"]
    assert "e_009" not in {e["email_id"] for e in received}
    assert live.effects("s-delete")[0] == {
        "seq": 1,
        "tool": "delete_email",
        "at": "2026-11-06T09:00:00-08:00",
        "email_id": "e_009",
        "folder": "inbox",
        "subject": "Open enrollment closes Nov 13",
    }
    with pytest.raises(ToolError, match="already in the trash"):
        live.call("delete_email", {"email_id": "e_009"}, "s-delete")


def test_errors(live):
    with pytest.raises(ToolError, match="No email"):
        live.call("delete_email", {"email_id": "e_999"})
    with pytest.raises(ToolError, match="not an email address"):
        live.call("send_email", {"recipients": ["bob"], "subject": "s", "body": "b"})
    with pytest.raises(ToolError, match="at least one address"):
        live.call("send_email", {"recipients": [], "subject": "s", "body": "b"})
    with pytest.raises(ToolError, match="query is empty"):
        live.call("search_emails", {"query": " "})
