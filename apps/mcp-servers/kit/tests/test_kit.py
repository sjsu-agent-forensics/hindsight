"""Tests for session routing, the control endpoint and the effect log, against a toy server."""

import asyncio
import json

import httpx2
import pytest
from fastmcp import Client, FastMCP
from fastmcp.exceptions import ToolError

from hindsight_server_kit import DEFAULT_TIME, SessionStore, manifest, read_json
from hindsight_server_kit.testing import running


def load_counter(path):
    """A fixture is {"start": n}; the benign default starts at 0."""
    return {"count": read_json(path)["start"] if path else 0}


server = FastMCP("Counter Server")
store = SessionStore("counter", load_counter)


@server.tool()
def bump() -> dict:
    """Add one to the counter."""
    session = store.get()
    with session.lock:
        session.data["count"] += 1
        session.record_effect("bump", count=session.data["count"])
        return {"count": session.data["count"]}


@server.tool()
def roll() -> dict:
    """Return the scenario time and a draw from the session's random source."""
    session = store.get()
    return {"now": session.now.isoformat(), "roll": session.random.randint(1, 10**6)}


def call(client: Client, tool: str) -> dict:
    async def go():
        async with client:
            return (await client.call_tool(tool)).structured_content

    return asyncio.run(go())


@pytest.fixture(scope="module")
def live():
    with running(server, store) as live:
        yield live


@pytest.fixture
def fixture_file(tmp_path):
    path = tmp_path / "counter.json"
    path.write_text(json.dumps({"start": 41}), encoding="utf-8")
    return path


def test_no_header_uses_the_default_state(live):
    first = call(live.client(), "bump")["count"]
    assert call(live.client(), "bump")["count"] == first + 1


def test_unseeded_session_is_an_error_not_the_default(live):
    with pytest.raises(ToolError, match="has not been set up"):
        call(live.client("never-seeded"), "bump")


def test_seeded_session_loads_its_fixture(live, fixture_file):
    live.seed("s-fixture", fixture=str(fixture_file))
    assert call(live.client("s-fixture"), "bump") == {"count": 42}


def test_sessions_do_not_see_each_other(live):
    live.seed("s-a")
    live.seed("s-b")
    for _ in range(3):
        call(live.client("s-a"), "bump")
    assert call(live.client("s-b"), "bump") == {"count": 1}
    assert len(live.effects("s-a")) == 3
    assert len(live.effects("s-b")) == 1


def test_effect_log_records_each_change(live):
    live.seed("s-effects", time="2026-12-01T10:00:00+00:00")
    call(live.client("s-effects"), "bump")
    call(live.client("s-effects"), "bump")
    assert live.effects("s-effects") == [
        {"seq": 1, "tool": "bump", "at": "2026-12-01T10:00:00+00:00", "count": 1},
        {"seq": 2, "tool": "bump", "at": "2026-12-01T10:00:00+00:00", "count": 2},
    ]


def test_reseeding_resets_and_reruns_identically(live):
    live.seed("s-rerun", seed=7)
    first = [call(live.client("s-rerun"), "roll") for _ in range(3)]
    call(live.client("s-rerun"), "bump")
    live.seed("s-rerun", seed=7)
    assert [call(live.client("s-rerun"), "roll") for _ in range(3)] == first
    assert live.effects("s-rerun") == []
    assert first[0]["now"] == DEFAULT_TIME


def test_scenario_id_resolves_to_this_servers_file(live, tmp_path, monkeypatch):
    (tmp_path / "attack-1").mkdir()
    (tmp_path / "attack-1" / "counter.json").write_text('{"start": 100}', encoding="utf-8")
    monkeypatch.setenv("HINDSIGHT_SCENARIOS", str(tmp_path))
    live.seed("s-scenario", scenario="attack-1")
    assert call(live.client("s-scenario"), "bump") == {"count": 101}
    # A scenario with no file for this server leaves it on the benign fixture.
    live.seed("s-other", scenario="does-not-touch-counter")
    assert call(live.client("s-other"), "bump") == {"count": 1}


def test_discard(live):
    live.seed("s-gone")
    assert live.discard("s-gone") == 204
    assert live.discard("s-gone") == 404
    with pytest.raises(ToolError):
        call(live.client("s-gone"), "bump")


def test_bad_seed_requests_are_rejected(live):
    url = f"{live.control_url}/sessions/s-bad"
    assert httpx2.post(url, json={"fixture": "/no/such/file.json"}).status_code == 400
    assert httpx2.post(url, json={"time": "2026-11-06T09:00:00"}).status_code == 400
    assert httpx2.post(url, content=b"not json").status_code == 400
    assert httpx2.get(f"{live.control_url}/sessions/s-bad/effects").status_code == 404


def test_control_routes_are_not_on_the_mcp_port(live):
    base = live.mcp_url.removesuffix("/api/mcp")
    assert httpx2.post(f"{base}/control/sessions/x").status_code == 404


def test_default_state_reads_the_environment(tmp_path, fixture_file, monkeypatch):
    monkeypatch.setenv("HINDSIGHT_FIXTURE", str(fixture_file))
    monkeypatch.setenv("HINDSIGHT_TIME", "2027-01-01T00:00:00+00:00")
    session = SessionStore("counter", load_counter).default()
    assert session.data == {"count": 41}
    assert session.now.year == 2027


def test_manifest_hashes_are_stable():
    first = asyncio.run(manifest(server))
    assert [t["name"] for t in first["tools"]] == ["bump", "roll"]
    assert first == asyncio.run(manifest(server))
    assert all(len(t["descriptor_sha256"]) == 64 for t in first["tools"])
