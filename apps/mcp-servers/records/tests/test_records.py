"""Tests for records-server: the generator, the lookup tools, and per-session isolation."""

import asyncio
import json
import sqlite3

import pytest
from fastmcp import Client
from fastmcp.exceptions import ToolError
from hindsight_server_kit.testing import running

from records_server.generate import generate
from records_server.main import load_records, server, store


def call(client: Client, tool: str, args: dict | None = None) -> dict:
    async def go():
        async with client:
            return (await client.call_tool(tool, args or {})).structured_content

    return asyncio.run(go())


def dump(db: sqlite3.Connection) -> list[tuple]:
    return list(db.iterdump())


@pytest.fixture(scope="module")
def live():
    with running(server, store) as live:
        yield live


def test_generator_is_deterministic():
    assert dump(generate(295, 50)) == dump(generate(295, 50))
    assert dump(generate(295, 50)) != dump(generate(296, 50))


def test_generated_people_are_synthetic():
    rows = generate(295, 50).execute("SELECT email, phone FROM customers").fetchall()
    assert len(rows) == 50
    assert all(email.rsplit("@", 1)[1].startswith("example.") for email, _ in rows)
    assert all(phone.startswith("408-555-01") for _, phone in rows)
    assert len({email for email, _ in rows}) == 50


def test_default_state_works_without_a_header():
    # The in-memory client sends no header, like `fastmcp call`.
    customer = call(Client(server), "get_customer", {"id": "c_001"})
    assert customer["customer_id"] == "c_001"
    assert set(customer) >= {"name", "email", "account_number", "notes"}


def test_find_customer(live):
    name = call(live.client(), "get_customer", {"id": "c_007"})["name"]
    found = call(live.client(), "find_customer", {"query": name.split()[0].upper()})
    assert "c_007" in [c["customer_id"] for c in found["customers"]]
    assert set(found["customers"][0]) == {"customer_id", "name", "email", "city"}
    assert call(live.client(), "find_customer", {"query": "zzzz"})["customers"] == []


def test_list_orders(live):
    orders = call(live.client(), "list_orders", {"customer_id": "c_003"})["orders"]
    assert all(o["customer_id"] == "c_003" for o in orders)
    assert [o["order_date"] for o in orders] == sorted(o["order_date"] for o in orders)


def test_unknown_customer_is_an_error(live):
    with pytest.raises(ToolError, match="No customer"):
        call(live.client(), "get_customer", {"id": "c_999"})
    with pytest.raises(ToolError, match="No customer"):
        call(live.client(), "list_orders", {"customer_id": "c_999"})


def test_update_is_logged_and_stays_in_its_session(live):
    live.seed("s-update")
    live.seed("s-other")
    before = call(live.client("s-update"), "get_customer", {"id": "c_002"})
    after = call(
        live.client("s-update"), "update_customer", {"id": "c_002", "fields": {"notes": "VIP"}}
    )
    assert after["notes"] == "VIP"
    assert call(live.client("s-other"), "get_customer", {"id": "c_002"}) == before
    assert live.effects("s-update") == [
        {
            "seq": 1,
            "tool": "update_customer",
            "at": "2026-11-06T09:00:00-08:00",
            "customer_id": "c_002",
            "changes": {"notes": {"from": before["notes"], "to": "VIP"}},
        }
    ]
    assert live.effects("s-other") == []


def test_protected_fields_cannot_be_updated(live):
    live.seed("s-protected")
    with pytest.raises(ToolError, match="Cannot update account_number"):
        call(
            live.client("s-protected"),
            "update_customer",
            {"id": "c_001", "fields": {"account_number": "0"}},
        )
    assert live.effects("s-protected") == []


def test_reset_restores_the_original_data(live):
    live.seed("s-reset")
    original = call(live.client("s-reset"), "get_customer", {"id": "c_004"})
    call(live.client("s-reset"), "update_customer", {"id": "c_004", "fields": {"city": "Nowhere"}})
    live.seed("s-reset")
    assert call(live.client("s-reset"), "get_customer", {"id": "c_004"}) == original


def test_overlay_changes_one_customer(live, tmp_path):
    notes = "Prefers a phone call before any change to the order"
    fixture = tmp_path / "records.json"
    fixture.write_text(json.dumps({"overlays": {"c_005": {"notes": notes}}}), encoding="utf-8")
    live.seed("s-overlay", fixture=str(fixture))
    live.seed("s-benign")
    assert call(live.client("s-overlay"), "get_customer", {"id": "c_005"})["notes"] == notes
    assert call(live.client("s-benign"), "get_customer", {"id": "c_005"})["notes"] != notes


def test_fixture_can_point_at_a_database_file(tmp_path):
    with sqlite3.connect(tmp_path / "small.db") as out:
        generate(1, 3).backup(out)
    out.close()
    (tmp_path / "records.json").write_text('{"database": "small.db"}', encoding="utf-8")
    db = load_records(tmp_path / "records.json")
    assert db.execute("SELECT count(*) FROM customers").fetchone()[0] == 3
    # Writes go to the in-memory copy, never back to the file.
    db.execute("DELETE FROM customers")
    with sqlite3.connect(tmp_path / "small.db") as check:
        assert check.execute("SELECT count(*) FROM customers").fetchone()[0] == 3
    check.close()
