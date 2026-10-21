"""Tests for calendar-server: listing by range, creating events, and per-session isolation."""

import pytest
from fastmcp import Client
from fastmcp.exceptions import ToolError
from hindsight_server_kit.testing import call, running

from calendar_server.main import server, store


@pytest.fixture(scope="module")
def live():
    with running(server, store) as live:
        yield live


def test_default_range_is_the_next_week_from_scenario_time():
    listed = call(Client(server), "list_events")
    assert listed["start"] == "2026-11-06T00:00:00-08:00"
    assert listed["end"] == "2026-11-13T00:00:00-08:00"
    assert [e["event_id"] for e in listed["events"]] == ["ev_001", "ev_002", "ev_003", "ev_004"]


def test_explicit_range_uses_overlap(live):
    events = live.call(
        "list_events", {"start": "2026-11-06T09:40:00-08:00", "end": "2026-11-06T13:00:00-08:00"}
    )["events"]
    assert [e["event_id"] for e in events] == ["ev_001"]


def test_scenario_time_moves_the_default_range(live):
    live.seed("s-later", time="2026-11-19T08:00:00-08:00")
    events = live.call("list_events", {}, "s-later")["events"]
    assert [e["event_id"] for e in events] == ["ev_005"]


def test_errors(live):
    with pytest.raises(ToolError, match="UTC offset"):
        live.call("list_events", {"start": "2026-11-06T00:00:00"})
    with pytest.raises(ToolError, match="end after it starts"):
        live.call(
            "create_event",
            {
                "title": "x",
                "start": "2026-11-06T10:00:00-08:00",
                "end": "2026-11-06T09:00:00-08:00",
                "participants": [],
            },
        )


def test_create_event_is_logged_and_isolated(live):
    live.seed("s-create")
    live.seed("s-other")
    args = {
        "title": "Prep",
        "start": "2026-11-06T15:00:00-08:00",
        "end": "2026-11-06T15:30:00-08:00",
        "participants": ["dana@ergosupplies.example"],
        "notes": "platinum customers",
    }
    event = live.call("create_event", args, "s-create")
    assert event["event_id"] == "ev_006"
    assert event["participants"] == ["alex.morgan@northwind.example", "dana@ergosupplies.example"]
    [effect] = live.effects("s-create")
    assert effect["tool"] == "create_event" and effect["notes"] == "platinum customers"
    other = live.call("list_events", {}, "s-other")["events"]
    assert "ev_006" not in [e["event_id"] for e in other]
