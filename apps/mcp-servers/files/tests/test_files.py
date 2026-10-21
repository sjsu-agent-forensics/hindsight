"""Tests for files-server: listing, reading, writing, sharing, and per-session isolation."""

import pytest
from fastmcp import Client
from fastmcp.exceptions import ToolError
from hindsight_server_kit.testing import call, running

from files_server.main import server, store


@pytest.fixture(scope="module")
def live():
    with running(server, store) as live:
        yield live


def test_list_and_read():
    listing = call(Client(server), "list_files")["files"]
    assert [f["path"] for f in listing] == sorted(f["path"] for f in listing)
    assert "content" not in listing[0]
    finance = call(Client(server), "list_files", {"prefix": "Finance/"})["files"]
    assert [f["file_id"] for f in finance] == ["f_002"]
    file = call(Client(server), "read_file", {"path": "/Finance\\overdue-invoices.txt"})
    assert "3275882316" in file["content"]


def test_errors(live):
    with pytest.raises(ToolError, match="No file"):
        live.call("read_file", {"path": "Nope/missing.txt"})
    with pytest.raises(ToolError, match="Invalid path"):
        live.call("write_file", {"path": "../escape.txt", "content": "x"})
    with pytest.raises(ToolError, match="Not an email address"):
        live.call("share_file", {"path": "Sales/renewals-q4.txt", "recipient": "jamie"})


def test_write_then_read_back_stays_in_its_session(live):
    live.seed("s-write")
    live.seed("s-other")
    summary = live.call(
        "write_file", {"path": "Notes/todo.txt", "content": "call c_003"}, "s-write"
    )
    assert summary["file_id"] == "f_005" and summary["size"] == len("call c_003")
    assert live.call("read_file", {"path": "Notes/todo.txt"}, "s-write")["content"] == "call c_003"
    with pytest.raises(ToolError):
        live.call("read_file", {"path": "Notes/todo.txt"}, "s-other")
    live.call("write_file", {"path": "Notes/todo.txt", "content": "done"}, "s-write")
    assert [(e["file_id"], e["created"], e["content"]) for e in live.effects("s-write")] == [
        ("f_005", True, "call c_003"),
        ("f_005", False, "done"),
    ]


def test_share_is_logged(live):
    live.seed("s-share")
    shared = live.call(
        "share_file",
        {"path": "Finance/overdue-invoices.txt", "recipient": "dana@ergosupplies.example"},
        "s-share",
    )
    assert shared["shared_with"] == ["morgan.lee@northwind.example", "dana@ergosupplies.example"]
    assert live.effects("s-share") == [
        {
            "seq": 1,
            "tool": "share_file",
            "at": "2026-11-06T09:00:00-08:00",
            "file_id": "f_002",
            "path": "Finance/overdue-invoices.txt",
            "recipient": "dana@ergosupplies.example",
        }
    ]
