"""The rule engine: whether a tool call may go ahead, from where its values came.

``evaluate`` is a pure, deterministic function of the rules, the call's server and tool,
and the provenance index's matches for the call's arguments. The interposer runs it on
every call before forwarding it; ``rule_engine.replay`` runs it over a recorded trace, so
the analysis service can check a candidate rule against recorded sessions and get the
decisions the live proxy would have made. It calls no model.

A rule, as stored in a JSON file (see ``rule_engine.sources``)::

    {
      "id": "no-web-destinations-to-email",
      "effect": "deny",
      "source": {"kind": "tool", "server": "web", "tool": "fetch_page"},
      "sink": {"server": "email", "tool": "send_email", "param": "to"},
      "value_types": ["url", "email"]
    }

It fires when an argument of a call to the sink contains an indexed value whose source
matches. Any field may be left out to match anything, except the sink ``server``, so no
rule can block every call everywhere:

- ``source.kind``: ``tool`` (a tool result, the default) or ``descriptor`` (a tool's
  description or schema). Values the user supplied never fire a rule.
- ``source.server``, ``source.tool``: where the value came from.
- ``sink.tool``, ``sink.param``: the call, and which of its arguments.
- ``value_types``: only values of these kinds (``url``, ``email``, ``path``,
  ``string``), so a rule can target destinations without blocking content.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Literal, get_args

from provenance import IndexEntry, ValueType, value_kinds

SourceKind = Literal["tool", "descriptor"]
Effect = Literal["allow", "block"]

_RULE_KEYS = {"id", "effect", "source", "sink", "value_types"}
_SOURCE_KEYS = {"kind", "server", "tool"}
_SINK_KEYS = {"server", "tool", "param"}


class RuleError(ValueError):
    """A rule is malformed."""


@dataclass(frozen=True)
class SourceMatch:
    """Which indexed values a rule acts on, by where they came from."""

    kind: SourceKind = "tool"
    server: str | None = None
    tool: str | None = None


@dataclass(frozen=True)
class SinkMatch:
    """Which calls, and which of their arguments, a rule guards."""

    server: str
    tool: str | None = None
    param: str | None = None


@dataclass(frozen=True)
class Rule:
    """A deny rule: values from ``source`` must not reach ``sink``."""

    id: str
    source: SourceMatch
    sink: SinkMatch
    value_types: frozenset[ValueType] | None = None

    def to_dict(self) -> dict[str, Any]:
        """Return the rule as JSON-ready data, in the same shape it is read from."""
        data: dict[str, Any] = {
            "id": self.id,
            "effect": "deny",
            "source": _without_none(vars(self.source)),
            "sink": _without_none(vars(self.sink)),
        }
        if self.value_types is not None:
            data["value_types"] = sorted(self.value_types)
        return data


@dataclass(frozen=True)
class Decision:
    """Whether a call may go ahead, and which rules (sorted by ID) blocked it."""

    effect: Effect
    rule_ids: tuple[str, ...] = ()


ALLOW = Decision("allow")


def parse_rule(data: object) -> Rule:
    """Validate rule data, as read from a rule file, and return the rule.

    Unknown keys are refused rather than ignored: a misspelled key would otherwise widen
    the rule silently.

    Raises:
        RuleError: The data is not a valid rule.
    """
    if not isinstance(data, dict):
        raise RuleError("a rule must be a JSON object")
    _check_keys(data, _RULE_KEYS, "rule")
    rule_id = data.get("id")
    if not isinstance(rule_id, str) or not rule_id:
        raise RuleError("'id' must be a non-empty string")
    if data.get("effect", "deny") != "deny":
        raise RuleError(f"rule {rule_id!r}: 'effect' must be 'deny'")

    source = data.get("source", {})
    sink = data.get("sink")
    if not isinstance(source, dict):
        raise RuleError(f"rule {rule_id!r}: 'source' must be an object")
    if not isinstance(sink, dict) or not isinstance(sink.get("server"), str):
        raise RuleError(f"rule {rule_id!r}: 'sink' must name at least a 'server'")
    _check_keys(source, _SOURCE_KEYS, f"rule {rule_id!r} source")
    _check_keys(sink, _SINK_KEYS, f"rule {rule_id!r} sink")
    kind = source.get("kind", "tool")
    if kind not in get_args(SourceKind):
        raise RuleError(
            f"rule {rule_id!r}: source 'kind' must be 'tool' or 'descriptor'"
        )

    value_types = data.get("value_types")
    if value_types is not None:
        known = set(get_args(ValueType))
        if not isinstance(value_types, list) or not set(value_types) <= known:
            raise RuleError(
                f"rule {rule_id!r}: 'value_types' must be a list of {sorted(known)}"
            )
        value_types = frozenset(value_types)

    return Rule(
        id=rule_id,
        source=SourceMatch(kind, source.get("server"), source.get("tool")),
        sink=SinkMatch(sink["server"], sink.get("tool"), sink.get("param")),
        value_types=value_types,
    )


def evaluate(
    rules: Sequence[Rule],
    server: str,
    tool: str | None,
    matches: Mapping[str, Sequence[IndexEntry]],
) -> Decision:
    """Decide whether a call to ``tool`` on ``server`` may go ahead.

    ``matches`` is the provenance index's answer for the call's arguments: per argument,
    the indexed values it contains and their sources. The call is blocked if any rule
    fires, and the decision names every rule that did.
    """
    fired = sorted(rule.id for rule in rules if _fires(rule, server, tool, matches))
    return Decision("block", tuple(fired)) if fired else ALLOW


def _fires(
    rule: Rule,
    server: str,
    tool: str | None,
    matches: Mapping[str, Sequence[IndexEntry]],
) -> bool:
    sink = rule.sink
    if sink.server != server or (sink.tool is not None and sink.tool != tool):
        return False
    arguments = [sink.param] if sink.param is not None else list(matches)
    return any(
        _source_matches(rule, entry)
        for argument in arguments
        for entry in matches.get(argument, ())
    )


def _source_matches(rule: Rule, entry: IndexEntry) -> bool:
    source = rule.source
    if entry.source_kind != source.kind:
        return False
    if source.server is not None and entry.server != source.server:
        return False
    if source.tool is not None and entry.tool != source.tool:
        return False
    return rule.value_types is None or bool(value_kinds(entry.value) & rule.value_types)


def _check_keys(data: dict, allowed: set[str], what: str) -> None:
    unknown = set(data) - allowed
    if unknown:
        raise RuleError(f"{what}: unknown keys {sorted(unknown)}")


def _without_none(data: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in data.items() if value is not None}
