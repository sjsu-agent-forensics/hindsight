"""Phase 6: rules block matching calls in-path, and replaying a trace reproduces them.

Two sessions run under the same two hand-written rules: one carries a value from server
A's response, and a poisoned descriptor's planted address, to server B; the other moves
only values the user supplied. Replay must give each call the decision it got live.
"""

import json
from contextlib import AsyncExitStack
from pathlib import Path

import httpx
import pytest
import trace_schema as ts
from mcp.shared.exceptions import McpError
from rule_engine.replay import replay

from interposer.config import HttpUpstream
from interposer.testing import (
    LOOKUP_EMAIL,
    LOOKUP_URL,
    POISONED_EMAIL,
    open_session,
    run,
    running_gateway,
    running_test_server,
)

RULES = [
    {
        "id": "no-lookup-to-b",
        "source": {"server": "a", "tool": "lookup"},
        "sink": {"server": "b", "tool": "echo"},
    },
    {
        "id": "no-descriptor-destinations",
        "source": {"kind": "descriptor"},
        "sink": {"server": "b"},
        "value_types": ["email", "url"],
    },
]


def _write_rules(directory: Path, rules: list[dict]) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    for rule in rules:
        (directory / f"{rule['id']}.json").write_text(json.dumps(rule))
    return directory


def _meta(tool_call_id: str) -> dict:
    return {ts.TOOL_CALL_ID_META_KEY: tool_call_id}


async def _run(gateway, session_id: str, calls: list[tuple]) -> dict[str, str]:
    """Run calls (server, tool, arguments, tool-call ID) in one session.

    The agent lists both servers' tools first, as a real agent does at startup, so their
    descriptors are indexed. Returns each call's outcome: its text, prefixed with
    ``error:`` for a tool error result. A blocked call is answered with a tool error
    result, so it fails only that call; a JSON-RPC error would abort a LangChain agent's
    whole run, and is recorded as ``jsonrpc error:`` so a test notices.
    """
    outcomes = {}
    async with AsyncExitStack() as stack:
        servers = {}
        for name in ("a", "b"):
            servers[name] = await open_session(stack, gateway.mcp_url(session_id, name))
            await servers[name].list_tools()
        for server, tool, arguments, call_id in calls:
            try:
                result = await servers[server].call_tool(
                    tool, arguments, meta=_meta(call_id)
                )
            except McpError as error:
                outcomes[call_id] = f"jsonrpc error: {error}"
                continue
            prefix = "error: " if result.isError else ""
            outcomes[call_id] = prefix + result.content[0].text
    return outcomes


@pytest.fixture(scope="module")
def sessions(tmp_path_factory):
    """The attack and benign sessions' traces, and what each one's agent got back."""
    rules_dir = _write_rules(tmp_path_factory.mktemp("rules"), RULES)
    with running_test_server() as url:
        upstreams = {"a": HttpUpstream("a", url), "b": HttpUpstream("b", url)}
        with running_gateway(upstreams, rules_dir=rules_dir) as gateway:
            attack = gateway.register("Look up the contact details and summarize them.")
            attack_seen = run(
                _run,
                gateway,
                attack.id,
                [
                    ("a", "lookup", {}, "read"),
                    ("b", "echo", {"text": f"Posting to {LOOKUP_URL}"}, "exfiltrate"),
                    ("b", "echo", {"text": f"cc {POISONED_EMAIL}"}, "planted"),
                    ("b", "echo", {"text": "nothing anyone returned"}, "harmless"),
                ],
            )
            gateway.end(attack.id)

            benign = gateway.register(f"Send the summary to {LOOKUP_EMAIL}.")
            benign_seen = run(
                _run,
                gateway,
                benign.id,
                [
                    ("a", "lookup", {}, "read"),
                    ("b", "echo", {"text": f"Summary for {LOOKUP_EMAIL}"}, "send"),
                ],
            )
            gateway.end(benign.id)
            yield {
                "attack_seen": attack_seen,
                "benign_seen": benign_seen,
                "attack": _events(attack.trace_dir),
                "benign": _events(benign.trace_dir),
            }


def _events(trace_dir: Path) -> list[dict]:
    return ts.read_events(trace_dir / ts.PROXY_TRACE_FILE)


def _decision(events: list[dict], call_id: str) -> dict:
    [decision] = [
        e
        for e in events
        if e[ts.EVENT_TYPE] == "decision" and e[ts.TOOL_CALL_ID] == call_id
    ]
    return decision


def test_a_rule_blocks_a_value_from_server_a_reaching_server_b(sessions):
    seen = sessions["attack_seen"]["exfiltrate"]
    assert seen == "error: Call blocked by hindsight rule no-lookup-to-b"
    decision = _decision(sessions["attack"], "exfiltrate")
    assert decision[ts.DECISION] == "block"
    assert decision[ts.RULE_ID] == "no-lookup-to-b"
    [match] = decision[ts.PROVENANCE_MATCHES]["text"]
    assert (match[ts.SERVER], match[ts.TOOL_NAME]) == ("a", "lookup")


def test_a_blocked_call_never_reaches_the_server(sessions):
    responses = [
        e[ts.TOOL_CALL_ID] for e in sessions["attack"] if e[ts.EVENT_TYPE] == "response"
    ]
    assert "exfiltrate" not in responses and "planted" not in responses
    assert "read" in responses


def test_calls_after_a_block_still_go_through(sessions):
    assert sessions["attack_seen"]["harmless"] == "nothing anyone returned"


def test_a_poisoned_descriptors_planted_address_is_blocked(sessions):
    assert sessions["attack_seen"]["planted"] == (
        "error: Call blocked by hindsight rule no-descriptor-destinations"
    )
    decision = _decision(sessions["attack"], "planted")
    matches = decision[ts.PROVENANCE_MATCHES]["text"]
    # Both server names front the same test server, so both published the descriptor.
    assert {(m["source_kind"], m[ts.TOOL_NAME]) for m in matches} == {
        ("descriptor", "notes")
    }
    assert {m[ts.SERVER] for m in matches} == {"a", "b"}


def test_values_the_user_supplied_pass_under_the_same_rules(sessions):
    assert sessions["benign_seen"]["send"] == f"Summary for {LOOKUP_EMAIL}"
    decisions = [e for e in sessions["benign"] if e[ts.EVENT_TYPE] == "decision"]
    assert {d[ts.DECISION] for d in decisions} == {"allow"}


@pytest.mark.parametrize("session", ["attack", "benign"])
def test_replay_reproduces_every_live_decision(sessions, session):
    events = sessions[session]
    live = {
        (e[ts.MCP_SESSION_ID], e[ts.JSONRPC_REQUEST_ID]): (
            e[ts.DECISION],
            tuple(e[ts.RULE_IDS]),
        )
        for e in events
        if e[ts.EVENT_TYPE] == "decision"
    }
    replayed = {
        (call.mcp_session_id, call.jsonrpc_id): (
            call.decision.effect,
            call.decision.rule_ids,
        )
        for call in replay(events)
    }
    assert replayed == live
    if session == "benign":
        assert all(effect == "allow" for effect, _ in replayed.values())


def test_the_registration_records_the_rules_and_extraction_settings(sessions):
    registration = sessions["attack"][0]
    assert {rule["id"] for rule in registration[ts.RULES]} == {r["id"] for r in RULES}
    assert registration[ts.EXTRACTION]["min_length"] == 8


def test_a_malformed_rule_file_fails_registration(tmp_path):
    rules_dir = _write_rules(tmp_path, [{"id": "broken", "sink": {"tool": "echo"}}])
    with running_test_server() as url:
        with running_gateway({"b": HttpUpstream("b", url)}, rules_dir=rules_dir) as gw:
            response = httpx.post(
                f"{gw.url}/sessions",
                json={
                    "session_id": "22222222-2222-2222-2222-222222222222",
                    "trusted_inputs": [],
                },
            )
    assert response.status_code == 500
    assert "broken.json" in response.json()["error"]
