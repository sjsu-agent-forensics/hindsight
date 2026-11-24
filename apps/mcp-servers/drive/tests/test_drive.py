"""Tests for drive-server: listing and searching files, edits per session and logged sharing."""

import asyncio

import pytest
from fastmcp import Client
from fastmcp.exceptions import ToolError
from hindsight_server_kit import manifest
from hindsight_server_kit.testing import call, running

from drive_server.main import server, store

TOOLS = {
    "list_files",
    "search_files",
    "search_files_by_filename",
    "get_file_by_id",
    "create_file",
    "append_to_file",
    "delete_file",
    "share_file",
}
NOW = "2026-11-06T09:00:00-08:00"


@pytest.fixture(scope="module")
def live():
    with running(server, store) as live:
        yield live


def test_manifest_lists_the_agentdojo_workspace_tools():
    assert {t["name"] for t in asyncio.run(manifest(server))["tools"]} == TOOLS


def test_list_and_get(live):
    files = call(Client(server), "list_files")["files"]
    assert len(files) == 20 and "content" not in files[0]
    assert files[0]["filename"] == "customer-list.csv" and files[0]["size"] > 0
    notes = live.call("get_file_by_id", {"file_id": "f_003"})
    assert notes["filename"] == "pipeline-meeting-notes-2026-11-02.md"
    assert "Riverbend Schools RFP" in notes["content"]


def test_search_by_content_and_filename(live):
    found = live.call("search_files_by_filename", {"filename": "PIPELINE-meeting"})["files"]
    assert [f["file_id"] for f in found] == ["f_003", "f_004"]
    found = live.call("search_files", {"query": "guest wi-fi"})["files"]
    assert [f["file_id"] for f in found] == ["f_009"]


def test_create_append_delete_per_session(live):
    live.seed("s-edit")
    live.seed("s-other")
    created = live.call("create_file", {"filename": "notes.md", "content": "a"}, "s-edit")
    assert created["file_id"] == "f_021" and created["owner"] == "alex.morgan@northwind.example"
    assert live.call("append_to_file", {"file_id": "f_021", "content": "b"}, "s-edit")[
        "content"
    ] == ("ab")
    assert live.call("delete_file", {"file_id": "f_021"}, "s-edit")["filename"] == "notes.md"
    assert live.effects("s-edit") == [
        {
            "seq": 1,
            "tool": "create_file",
            "at": NOW,
            "file_id": "f_021",
            "filename": "notes.md",
            "content": "a",
        },
        {
            "seq": 2,
            "tool": "append_to_file",
            "at": NOW,
            "file_id": "f_021",
            "filename": "notes.md",
            "content": "b",
        },
        {"seq": 3, "tool": "delete_file", "at": NOW, "file_id": "f_021", "filename": "notes.md"},
    ]
    live.call("delete_file", {"file_id": "f_001"}, "s-edit")
    assert len(live.call("list_files", {}, "s-other")["files"]) == 20
    assert live.effects("s-other") == []


def test_share_is_logged_with_permission(live):
    live.seed("s-share")
    shared = live.call(
        "share_file",
        {"file_id": "f_003", "email": "jamie.park@northwind.example", "permission": "rw"},
        "s-share",
    )
    assert shared["shared_with"]["jamie.park@northwind.example"] == "rw"
    live.call(
        "share_file",
        {"file_id": "f_001", "email": "mei.tanaka@brightline.example", "permission": "r"},
        "s-share",
    )
    assert [
        (e["file_id"], e["email"], e["permission"], e["external"]) for e in live.effects("s-share")
    ] == [
        ("f_003", "jamie.park@northwind.example", "rw", False),
        ("f_001", "mei.tanaka@brightline.example", "r", True),
    ]


def test_errors(live):
    with pytest.raises(ToolError, match="No file"):
        live.call("get_file_by_id", {"file_id": "f_999"})
    with pytest.raises(ToolError, match="read-only"):
        live.call("append_to_file", {"file_id": "f_010", "content": "x"})
    with pytest.raises(ToolError, match="not an email address"):
        live.call("share_file", {"file_id": "f_003", "email": "jamie", "permission": "r"})
    with pytest.raises(ToolError):
        live.call(
            "share_file",
            {"file_id": "f_003", "email": "jamie.park@northwind.example", "permission": "admin"},
        )
    with pytest.raises(ToolError, match="needs a name"):
        live.call("create_file", {"filename": " ", "content": "x"})
