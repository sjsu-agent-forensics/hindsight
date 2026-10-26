"""Tests for rule parsing, evaluation and the directory rule source."""

import json

import pytest
from provenance import IndexEntry

from rule_engine import ALLOW, Decision, RuleError, evaluate, parse_rule
from rule_engine.sources import DirectoryRuleSource

WEB_URL = IndexEntry("https://exfil.example/c", "tool", "web", "fetch_page", "4", "c1")
POISONED = IndexEntry("ops@evil.example", "descriptor", "weather", "forecast", "1")
USERS = IndexEntry("bob@corp.example", "trusted")
NOTES = IndexEntry("quarterly plan notes", "tool", "files", "read_file", "5", "c2")


def _rule(**fields) -> dict:
    return {"id": "r1", "sink": {"server": "email"}, **fields}


def test_a_minimal_rule_names_only_a_sink_server():
    rule = parse_rule(_rule())
    assert (rule.source.kind, rule.source.server, rule.sink.tool) == (
        "tool",
        None,
        None,
    )


@pytest.mark.parametrize(
    "data, message",
    [
        ({"sink": {"server": "email"}}, "'id'"),
        ({"id": "r1"}, "must name at least a 'server'"),
        ({"id": "r1", "sink": {"tool": "send_email"}}, "must name at least a 'server'"),
        (_rule(effect="allow"), "'effect' must be 'deny'"),
        (_rule(sinc={"server": "email"}), "unknown keys"),
        (_rule(source={"kind": "trusted"}), "'tool' or 'descriptor'"),
        (_rule(value_types=["ip"]), "'value_types'"),
    ],
)
def test_malformed_rules_are_refused(data, message):
    with pytest.raises(RuleError, match=message):
        parse_rule(data)


def test_a_rule_round_trips_through_to_dict():
    data = {
        "id": "r1",
        "effect": "deny",
        "source": {"kind": "tool", "server": "web"},
        "sink": {"server": "email", "tool": "send_email", "param": "to"},
        "value_types": ["email", "url"],
    }
    assert parse_rule(data).to_dict() == data


def _evaluate(rule: dict, matches: dict, server="email", tool="send_email") -> Decision:
    return evaluate([parse_rule(rule)], server, tool, matches)


def test_a_tool_value_reaching_the_sink_is_blocked():
    rule = _rule(source={"server": "web"})
    assert _evaluate(rule, {"body": [WEB_URL]}) == Decision("block", ("r1",))


def test_missing_fields_match_anything_but_named_fields_must_match():
    assert _evaluate(_rule(), {"body": [WEB_URL]}).effect == "block"
    assert _evaluate(_rule(source={"server": "files"}), {"body": [WEB_URL]}) == ALLOW
    assert _evaluate(_rule(source={"tool": "fetch_page"}), {"b": [WEB_URL]}).effect == (
        "block"
    )


def test_the_sink_server_tool_and_param_limit_which_calls_and_arguments():
    rule = _rule(sink={"server": "email", "tool": "send_email", "param": "to"})
    assert _evaluate(rule, {"to": [WEB_URL]}).effect == "block"
    assert _evaluate(rule, {"body": [WEB_URL]}) == ALLOW
    assert _evaluate(rule, {"to": [WEB_URL]}, tool="draft_email") == ALLOW
    assert _evaluate(rule, {"to": [WEB_URL]}, server="chat") == ALLOW


def test_values_the_user_supplied_never_fire_a_rule():
    assert _evaluate(_rule(), {"to": [USERS]}) == ALLOW


def test_descriptor_values_fire_only_rules_naming_the_descriptor_source():
    assert _evaluate(_rule(), {"to": [POISONED]}) == ALLOW
    rule = _rule(source={"kind": "descriptor"})
    assert _evaluate(rule, {"to": [POISONED]}).effect == "block"
    assert _evaluate(rule, {"to": [WEB_URL]}) == ALLOW


def test_value_types_target_destinations_without_blocking_content():
    rule = _rule(value_types=["url", "email"])
    assert _evaluate(rule, {"body": [WEB_URL]}).effect == "block"
    assert _evaluate(rule, {"body": [NOTES]}) == ALLOW


def test_every_firing_rule_is_named_in_id_order():
    rules = [parse_rule({**_rule(), "id": rule_id}) for rule_id in ("zeta", "alpha")]
    assert evaluate(rules, "email", "send_email", {"b": [WEB_URL]}).rule_ids == (
        "alpha",
        "zeta",
    )


def test_the_directory_source_reads_every_rule_file_in_id_order(tmp_path):
    for name, rule_id in [("b.json", "beta"), ("a.json", "alpha")]:
        (tmp_path / name).write_text(json.dumps({**_rule(), "id": rule_id}))
    (tmp_path / "README.md").write_text("not a rule")
    assert [r.id for r in DirectoryRuleSource(tmp_path).load_rules()] == [
        "alpha",
        "beta",
    ]


def test_a_missing_rule_directory_means_no_rules(tmp_path):
    assert DirectoryRuleSource(tmp_path / "absent").load_rules() == []


@pytest.mark.parametrize(
    "contents, message",
    [("{not json", "bad.json"), (json.dumps({"id": "r1"}), "bad.json")],
)
def test_a_malformed_rule_file_fails_loudly_naming_the_file(
    tmp_path, contents, message
):
    (tmp_path / "bad.json").write_text(contents)
    with pytest.raises(RuleError, match=message):
        DirectoryRuleSource(tmp_path).load_rules()


def test_two_files_with_one_rule_id_are_refused(tmp_path):
    for name in ("a.json", "b.json"):
        (tmp_path / name).write_text(json.dumps(_rule()))
    with pytest.raises(RuleError, match="used by another file"):
        DirectoryRuleSource(tmp_path).load_rules()
