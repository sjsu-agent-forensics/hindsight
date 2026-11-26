"""Tests for web-server: the page corpus, URL matching, 404s and that every request is logged."""

import asyncio

import pytest
from fastmcp import Client
from fastmcp.exceptions import ToolError
from hindsight_server_kit import manifest
from hindsight_server_kit.testing import call, running

from web_server.main import server, store

TOOLS = {"get_webpage", "post_webpage"}
NOW = "2026-11-06T09:00:00-08:00"


@pytest.fixture(scope="module")
def live():
    with running(server, store) as live:
        yield live


def test_manifest_lists_the_agentdojo_slack_tools():
    assert {t["name"] for t in asyncio.run(manifest(server))["tools"]} == TOOLS


def test_known_page(live):
    page = call(Client(server), "get_webpage", {"url": "https://www.northwind.example/about"})
    assert (page["status"], page["page_id"], page["title"]) == (200, "w_007", "About Northwind")


def test_url_normalisation(live):
    for url in (
        "HTTPS://WWW.ERGOSUPPLIES.EXAMPLE/products/ec-200/",
        "www.ergosupplies.example/products/ec-200#specs",
        "https://www.ergosupplies.example/products/ec-200?ref=email",
    ):
        assert live.call("get_webpage", {"url": url})["page_id"] == "w_002", url
    # The query string picks a page when one matches with it.
    order = live.call(
        "get_webpage", {"url": "https://www.ergosupplies.example/order-status?order=ES-13136"}
    )
    assert order["page_id"] == "w_003"


def test_unknown_pages_are_404_and_every_request_is_logged(live):
    live.seed("s-web")
    missing = live.call("get_webpage", {"url": "https://www.example.com/lookup?q=notes"}, "s-web")
    assert (missing["status"], missing["page_id"]) == (404, None)
    live.call("get_webpage", {"url": "https://www.northwind.example/about"}, "s-web")
    assert live.effects("s-web") == [
        {
            "seq": 1,
            "tool": "get_webpage",
            "at": NOW,
            "url": "https://www.example.com/lookup?q=notes",
            "status": 404,
            "page_id": None,
            "external": True,
        },
        {
            "seq": 2,
            "tool": "get_webpage",
            "at": NOW,
            "url": "https://www.northwind.example/about",
            "status": 200,
            "page_id": "w_007",
            "external": False,
        },
    ]


def test_posted_pages_are_stored_per_session(live):
    live.seed("s-post")
    live.seed("s-other")
    posted = live.call(
        "post_webpage", {"url": "https://www.example.com/notes", "content": "hello"}, "s-post"
    )
    assert posted == {
        "posted": True,
        "url": "https://www.example.com/notes",
        "page_id": "w_011",
        "created": True,
    }
    assert live.call("get_webpage", {"url": "www.example.com/notes"}, "s-post")["text"] == "hello"
    again = live.call(
        "post_webpage", {"url": "https://www.example.com/notes", "content": "v2"}, "s-post"
    )
    assert (again["page_id"], again["created"]) == ("w_011", False)
    assert [e["tool"] for e in live.effects("s-post")] == [
        "post_webpage",
        "get_webpage",
        "post_webpage",
    ]
    assert live.call("get_webpage", {"url": "www.example.com/notes"}, "s-other")["status"] == 404


def test_errors(live):
    with pytest.raises(ToolError, match="not a URL"):
        live.call("get_webpage", {"url": "  "})
