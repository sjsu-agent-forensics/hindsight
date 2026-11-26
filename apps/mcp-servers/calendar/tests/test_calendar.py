"""Tests for calendar-server: the scenario date, listings, event changes and their effects."""

import asyncio

import pytest
from fastmcp import Client
from fastmcp.exceptions import ToolError
from hindsight_server_kit import manifest
from hindsight_server_kit.testing import call, running

from calendar_server.main import server, store

TOOLS = {
    "get_current_day",
    "search_calendar_events",
    "get_day_calendar_events",
    "create_calendar_event",
    "cancel_calendar_event",
    "reschedule_calendar_event",
    "add_calendar_event_participants",
}
ALEX = "alex.morgan@northwind.example"
NOW = "2026-11-06T09:00:00-08:00"


@pytest.fixture(scope="module")
def live():
    with running(server, store) as live:
        yield live


def test_manifest_lists_the_agentdojo_workspace_tools():
    assert {t["name"] for t in asyncio.run(manifest(server))["tools"]} == TOOLS


def test_current_day_listing_and_search(live):
    assert call(Client(server), "get_current_day") == {"date": "2026-11-06", "now": NOW}
    today = live.call("get_day_calendar_events", {"day": "2026-11-06"})["events"]
    assert [e["event_id"] for e in today] == ["ev_011", "ev_012", "ev_013"]
    found = live.call("search_calendar_events", {"query": "PIPELINE"})["events"]
    assert [e["event_id"] for e in found] == ["ev_003", "ev_012", "ev_014"]
    found = live.call("search_calendar_events", {"query": "pipeline", "date": "2026-11-02"})
    assert [e["event_id"] for e in found["events"]] == ["ev_003"]


def test_create_reschedule_cancel_are_logged(live):
    live.seed("s-cal")
    live.seed("s-other")
    created = live.call(
        "create_calendar_event",
        {
            "title": "Ergo delivery check",
            "start_time": "2026-11-09T14:00:00-08:00",
            "end_time": "2026-11-09T14:30:00-08:00",
            "participants": ["casey.nguyen@northwind.example"],
        },
        "s-cal",
    )
    assert created["event_id"] == "ev_026"
    assert created["participants"] == [ALEX, "casey.nguyen@northwind.example"]
    moved = live.call(
        "reschedule_calendar_event",
        {"event_id": "ev_026", "new_start_time": "2026-11-10T13:00:00-08:00"},
        "s-cal",
    )
    assert (moved["start_time"], moved["end_time"]) == (
        "2026-11-10T13:00:00-08:00",
        "2026-11-10T13:30:00-08:00",
    )
    assert live.call("cancel_calendar_event", {"event_id": "ev_026"}, "s-cal")["canceled"]
    assert [e["tool"] for e in live.effects("s-cal")] == [
        "create_calendar_event",
        "reschedule_calendar_event",
        "cancel_calendar_event",
    ]
    assert live.effects("s-cal")[0] == {
        "seq": 1,
        "tool": "create_calendar_event",
        "at": NOW,
        "event_id": "ev_026",
        "title": "Ergo delivery check",
        "description": "",
        "start_time": "2026-11-09T14:00:00-08:00",
        "end_time": "2026-11-09T14:30:00-08:00",
        "location": "",
        "participants": [ALEX, "casey.nguyen@northwind.example"],
        "external": False,
    }
    assert live.call("get_day_calendar_events", {"day": "2026-11-09"}, "s-other")["events"][-1][
        "event_id"
    ] == ("ev_016")
    assert live.effects("s-other") == []


def test_add_participants_flags_outsiders(live):
    live.seed("s-invite")
    event = live.call(
        "add_calendar_event_participants",
        {"event_id": "ev_012", "participants": ["AVERY.PATEL@northwind.example", "x@example.com"]},
        "s-invite",
    )
    assert event["participants"] == [ALEX, "avery.patel@northwind.example", "x@example.com"]
    effect = live.effects("s-invite")[0]
    assert (effect["added"], effect["external"]) == (["x@example.com"], True)


def test_errors(live):
    with pytest.raises(ToolError, match="needs a UTC offset"):
        live.call(
            "create_calendar_event",
            {"title": "t", "start_time": "2026-11-09T14:00", "end_time": "2026-11-09T15:00"},
        )
    with pytest.raises(ToolError, match="before it starts"):
        live.call(
            "create_calendar_event",
            {
                "title": "t",
                "start_time": "2026-11-09T14:00:00-08:00",
                "end_time": "2026-11-09T13:00:00-08:00",
            },
        )
    with pytest.raises(ToolError, match="is canceled"):
        live.call("cancel_calendar_event", {"event_id": "ev_023"})
    with pytest.raises(ToolError, match="No event"):
        live.call("cancel_calendar_event", {"event_id": "ev_999"})
    with pytest.raises(ToolError, match="not a date"):
        live.call("get_day_calendar_events", {"day": "Friday"})
