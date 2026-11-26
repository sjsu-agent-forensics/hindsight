"""Tests for fixture overlays and id allocation."""

import json

import pytest

from hindsight_server_kit import apply_overlay, find_item, load_json_fixture, next_id

BENIGN = {
    "owner": "a@x.example",
    "emails": [{"email_id": "e_001", "body": "hi"}, {"email_id": "e_002", "body": "yo"}],
    "channels": [{"channel": "general", "messages": [{"message_id": "m_001", "text": "hey"}]}],
}


@pytest.fixture
def benign_file(tmp_path):
    path = tmp_path / "benign.json"
    path.write_text(json.dumps(BENIGN), encoding="utf-8")
    return path


def write(tmp_path, data):
    path = tmp_path / "fixture.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


def test_none_loads_the_benign_fixture(benign_file):
    assert load_json_fixture(None, benign_file) == BENIGN


def test_a_fixture_without_base_replaces_the_benign_one(benign_file, tmp_path):
    assert load_json_fixture(write(tmp_path, {"emails": []}), benign_file) == {"emails": []}


def test_overlay_adds_and_patches_without_touching_the_original(benign_file, tmp_path):
    overlay = {
        "base": "benign",
        "add": {"emails": [{"email_id": "e_900", "body": "added"}]},
        "patch": {"e_002": {"body": "patched"}, "m_001": {"text": "nested patch"}},
    }
    data = load_json_fixture(write(tmp_path, overlay), benign_file)
    assert [e["body"] for e in data["emails"]] == ["hi", "patched", "added"]
    assert data["channels"][0]["messages"][0]["text"] == "nested patch"
    assert load_json_fixture(None, benign_file) == BENIGN


def test_overlay_errors_name_the_problem():
    with pytest.raises(ValueError, match="e_404"):
        apply_overlay(BENIGN, {"patch": {"e_404": {"body": "x"}}})
    with pytest.raises(ValueError, match="owner"):
        apply_overlay(BENIGN, {"add": {"owner": [{}]}})


def test_find_item():
    assert find_item(BENIGN, "m_001")["text"] == "hey"
    assert find_item(BENIGN, "nope") is None


def test_next_id_keeps_width_and_skips_gaps():
    assert next_id([], "email_id", "e_") == "e_001"
    assert next_id([{"email_id": "e_001"}, {"email_id": "e_900"}], "email_id", "e_") == "e_901"
    assert next_id([{"order_id": "o_0009"}], "order_id", "o_") == "o_0010"
