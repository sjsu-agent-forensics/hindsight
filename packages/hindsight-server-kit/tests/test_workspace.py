"""Tests for the generated Northwind workspace and the shared people directory."""

import copy
import importlib.util
import json
from pathlib import Path
from types import ModuleType

import pytest
from fastmcp.exceptions import ToolError

from hindsight_server_kit import is_external, lookup_person, require_address, search_people

REPO_ROOT = Path(__file__).resolve().parents[3]


def _load_generator() -> ModuleType:
    # scripts/ is not a package; load the generator straight from its file.
    path = REPO_ROOT / "scripts" / "generate_workspace.py"
    spec = importlib.util.spec_from_file_location("generate_workspace", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


generator = _load_generator()


def test_generator_is_deterministic_and_matches_the_committed_fixtures():
    workspace = generator.build_workspace(295)
    assert workspace == generator.build_workspace(295)
    assert workspace != generator.build_workspace(296)
    for name, relative in generator.OUTPUTS.items():
        on_disk = json.loads((REPO_ROOT / relative).read_text(encoding="utf-8"))
        assert on_disk == workspace[name], f"{relative} is stale; run generate_workspace.py"
    counts = {
        "people": len(workspace["directory"]["people"]),
        "emails": len(workspace["email"]["emails"]),
        "events": len(workspace["calendar"]["events"]),
        "files": len(workspace["drive"]["files"]),
        "channels": len(workspace["chat"]["channels"]),
        "messages": sum(len(c["messages"]) for c in workspace["chat"]["channels"])
        + len(workspace["chat"]["dms"]),
        "pages": len(workspace["web"]["pages"]),
    }
    assert counts == {
        "people": 22,
        "emails": 40,
        "events": 25,
        "files": 20,
        "channels": 5,
        "messages": 60,
        "pages": 10,
    }


def test_only_reserved_domains_and_phone_numbers_appear():
    workspace = generator.build_workspace(295)
    assert generator.check_workspace(workspace) == []
    bad = copy.deepcopy(workspace)
    bad["web"]["pages"][0]["text"] = "See https://www.google.com or call 408-555-1234."
    bad["email"]["emails"][0]["cc"] = ["someone@gmail.com"]
    problems = generator.check_workspace(bad)
    assert any("www.google.com" in p for p in problems)
    assert any("gmail.com" in p for p in problems)
    assert any("408-555-1234" in p for p in problems)


def test_every_reference_resolves():
    bad = generator.build_workspace(295)
    bad["chat"]["channels"][0]["members"].append("ghost")
    bad["email"]["emails"][0]["attachments"] = ["f_999"]
    assert generator.check_workspace(bad) == [
        "email attachment: 'f_999' does not exist",
        "chat member: 'ghost' does not exist",
    ]


def test_person_lookup_and_address_checks():
    assert lookup_person("jamie")["email"] == "jamie.park@northwind.example"
    assert lookup_person("Dana.Whitfield@ErgoSupplies.example")["internal"] is False
    assert lookup_person("nobody@example.com") is None
    assert [p["handle"] for p in search_people("lee", "name")] == ["morgan"]
    assert is_external("x@ergosupplies.example") and not is_external("a@northwind.example")
    assert require_address(" a@b.example ") == "a@b.example"
    with pytest.raises(ToolError, match="not an email address"):
        require_address("a@b")
    with pytest.raises(ToolError, match="query is empty"):
        search_people(" ", "email")
