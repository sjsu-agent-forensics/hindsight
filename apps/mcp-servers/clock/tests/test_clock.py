"""Tests for clock-server: the scenario time comes back, per session, and never moves."""

import asyncio

import pytest
from fastmcp import Client
from hindsight_server_kit.testing import running

from clock_server.main import server, store


def call(client: Client) -> dict:
    async def go():
        async with client:
            return (await client.call_tool("get_datetime")).structured_content

    return asyncio.run(go())


@pytest.fixture(scope="module")
def live():
    with running(server, store) as live:
        yield live


def test_default_is_the_fixed_scenario_time():
    assert call(Client(server)) == {
        "datetime": "2026-11-06T09:00:00-08:00",
        "date": "2026-11-06",
        "time": "09:00:00",
        "weekday": "Friday",
        "utc_offset": "-08:00",
    }


def test_each_session_gets_its_own_time(live):
    live.seed("s-utc", time="2026-12-24T23:30:00+00:00")
    live.seed("s-ist", time="2026-12-25T05:00:00+05:30")
    assert call(live.client("s-utc"))["weekday"] == "Thursday"
    assert call(live.client("s-ist")) == {
        "datetime": "2026-12-25T05:00:00+05:30",
        "date": "2026-12-25",
        "time": "05:00:00",
        "weekday": "Friday",
        "utc_offset": "+05:30",
    }


def test_time_does_not_advance(live):
    live.seed("s-still")
    assert call(live.client("s-still")) == call(live.client("s-still"))
    assert live.effects("s-still") == []
