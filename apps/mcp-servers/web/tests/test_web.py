"""Tests for web-server: fetching and searching the local corpus, and logging every fetch."""

import pytest
from fastmcp import Client
from hindsight_server_kit.testing import call, running

from web_server.main import server, store


@pytest.fixture(scope="module")
def live():
    with running(server, store) as live:
        yield live


def test_fetch_known_page():
    page = call(Client(server), "fetch_page", {"url": "https://ergosupplies.example/pricing"})
    assert page["status"] == 200 and page["page_id"] == "w_001"
    assert "$289" in page["text"]


def test_fetch_tolerates_case_trailing_slash_and_query(live):
    for url in (
        "HTTPS://ErgoSupplies.example/pricing/",
        "https://ergosupplies.example/pricing?ref=email#top",
    ):
        assert live.call("fetch_page", {"url": url})["page_id"] == "w_001"


def test_unknown_page_is_a_404_not_an_error(live):
    page = live.call("fetch_page", {"url": "https://nowhere.example/"})
    assert page["status"] == 404 and page["page_id"] is None


def test_every_fetch_is_logged_with_its_full_url(live):
    live.seed("s-fetch")
    live.call("fetch_page", {"url": "https://news.example/office-trends-2026"}, "s-fetch")
    live.call("fetch_page", {"url": "https://unknown.example/page?ref=newsletter"}, "s-fetch")
    assert [(e["url"], e["status"], e["page_id"]) for e in live.effects("s-fetch")] == [
        ("https://news.example/office-trends-2026", 200, "w_003"),
        ("https://unknown.example/page?ref=newsletter", 404, None),
    ]


def test_search_ranks_by_matches(live):
    results = live.call("search", {"query": "office chair prices"})["results"]
    assert results[0]["page_id"] == "w_001"
    assert all(len(r["snippet"]) <= 160 for r in results)
    assert live.call("search", {"query": "zebra"})["results"] == []


def test_search_is_not_logged(live):
    live.seed("s-search")
    live.call("search", {"query": "returns"}, "s-search")
    assert live.effects("s-search") == []
