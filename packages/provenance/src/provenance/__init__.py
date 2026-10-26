"""Value-matching provenance: where each value in a tool call's arguments came from.

The interposer indexes distinctive values from the session's trusted inputs (the user's
prompt) and from every tool response, each with its source. Before a call is forwarded,
``ProvenanceIndex.match`` reports which indexed values its arguments contain. No model is
involved: extraction and matching are deterministic string operations, so the same
inputs always give the same matches, live in the proxy and when replaying a trace.

Values pass through the model on their way from a response to a later call. The index
sees only values the model copies; a value it rewrites or encodes is not matched.

What is indexed is set by ``ExtractionConfig`` (tuning items in the design):

- ``url``: ``http``, ``https``, ``ftp`` and ``file`` URLs
- ``email``: email addresses
- ``path``: absolute POSIX, Windows and home-relative file paths
- ``string``: any whole string of at least ``min_length`` characters

Values are normalized by lowercasing and collapsing whitespace, nothing more. A string
that holds JSON (as MCP text content often does) is parsed and its values indexed
instead of the raw text.

Content indexed here comes from the user and from upstream servers; it is treated as
data to compare, never interpreted.
"""

import json
import re
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any, Literal

ValueType = Literal["url", "email", "path", "string"]
SourceKind = Literal["trusted", "tool"]

_URL = re.compile(r"\b(?:https?|ftp|file)://[^\s\"'<>(){}\[\]]+", re.IGNORECASE)
_EMAIL = re.compile(r"\b[\w.+-]+@[\w-]+(?:\.[\w-]+)+\b")
_PATH = re.compile(
    # POSIX with at least two segments, not part of a URL or a longer word.
    r"(?<![\w:/.])/(?:[\w.-]+/)+[\w.-]+"
    # Windows drive paths, with either separator.
    r"|\b[A-Za-z]:[\\/](?:[\w.-]+[\\/])*[\w.-]+"
    # Home-relative paths.
    r"|(?<![\w/])~/(?:[\w.-]+/)*[\w.-]+"
)
# Punctuation that ends a sentence rather than a URL or path.
_TRAILING = ".,;:!?"
_WHITESPACE = re.compile(r"\s+")


@dataclass(frozen=True)
class ExtractionConfig:
    """Which values to index and match.

    Args:
        min_length: The shortest whole string indexed as a ``string`` value, after
            normalization. Shorter strings are too common to say where they came from.
        value_types: The kinds of value to extract.
    """

    min_length: int = 8
    value_types: frozenset[ValueType] = frozenset({"url", "email", "path", "string"})


@dataclass(frozen=True)
class IndexEntry:
    """One indexed value and where it came from.

    For a ``tool`` value, ``server``, ``tool``, ``jsonrpc_id`` and ``tool_call_id`` name
    the response it was found in. A ``trusted`` value came from the session's trusted
    inputs and has none of them.
    """

    value: str
    source_kind: SourceKind
    server: str | None = None
    tool: str | None = None
    jsonrpc_id: str | None = None
    tool_call_id: str | None = None


def normalize(text: str) -> str:
    """Lowercase ``text`` and collapse each run of whitespace to one space."""
    return _WHITESPACE.sub(" ", text).strip().lower()


def extract_values(data: Any, config: ExtractionConfig) -> set[str]:
    """Return the normalized values to index from any JSON-shaped ``data``.

    Dict keys are not values; only what they map to is searched. A string holding a JSON
    object or array is parsed and searched instead of indexed whole.
    """
    values: set[str] = set()
    for text in _strings(data, parse_json=True):
        values |= _values_in(text, config)
    return values


class ProvenanceIndex:
    """One session's index of values and their sources.

    Created when the session is registered and discarded when it ends. Not thread-safe:
    the gateway updates and reads it from one event loop.
    """

    def __init__(self, config: ExtractionConfig) -> None:
        self.config = config
        self._entries: dict[str, list[IndexEntry]] = {}
        # Each trusted input's whole normalized text, to recognize a tool value the user
        # also wrote even when extraction would not have picked it out of the prompt.
        self._trusted_texts: list[str] = []

    def add_trusted(self, content: str) -> None:
        """Index a trusted input, such as the user's prompt."""
        self._trusted_texts.append(normalize(content))
        for value in extract_values(content, self.config):
            self._add(IndexEntry(value, "trusted"))

    def add_tool_result(
        self,
        result: Any,
        server: str,
        tool: str | None,
        jsonrpc_id: str,
        tool_call_id: str | None,
    ) -> None:
        """Index the values in one tool call's result, with the call as their source."""
        for value in extract_values(result, self.config):
            self._add(IndexEntry(value, "tool", server, tool, jsonrpc_id, tool_call_id))

    def match(self, arguments: dict[str, Any]) -> dict[str, list[IndexEntry]]:
        """Return, per argument, the indexed values it contains and their sources.

        An argument matches an indexed value when the value appears anywhere in the
        argument's normalized text, so a URL inside a message body matches. Arguments
        with no match are left out.

        Trusted inputs win: a value the user supplied is attributed to the user even if
        a tool also returned it, which resolves values that are shared by chance.
        """
        matches: dict[str, list[IndexEntry]] = {}
        for name, argument in arguments.items():
            texts = [normalize(text) for text in _strings(argument, parse_json=False)]
            found = [
                entry
                for value, entries in self._entries.items()
                if any(value in text for text in texts)
                for entry in self._attribute(value, entries)
            ]
            if found:
                matches[name] = found
        return matches

    def _attribute(self, value: str, entries: list[IndexEntry]) -> list[IndexEntry]:
        trusted = [entry for entry in entries if entry.source_kind == "trusted"]
        if trusted:
            return trusted
        if any(value in text for text in self._trusted_texts):
            return [IndexEntry(value, "trusted")]
        return entries

    def _add(self, entry: IndexEntry) -> None:
        entries = self._entries.setdefault(entry.value, [])
        if entry not in entries:
            entries.append(entry)


def _values_in(text: str, config: ExtractionConfig) -> set[str]:
    """Return the normalized values of the configured types found in one string."""
    values: set[str] = set()
    if "url" in config.value_types:
        values |= {normalize(url.rstrip(_TRAILING)) for url in _URL.findall(text)}
    if "email" in config.value_types:
        values |= {normalize(email) for email in _EMAIL.findall(text)}
    if "path" in config.value_types:
        values |= {normalize(path.rstrip(_TRAILING)) for path in _PATH.findall(text)}
    whole = normalize(text)
    if "string" in config.value_types and len(whole) >= config.min_length:
        values.add(whole)
    return values


def _strings(data: Any, parse_json: bool) -> Iterator[str]:
    """Yield every string inside JSON-shaped ``data``, values only.

    With ``parse_json``, a string that holds a JSON object or array yields the strings
    inside it instead of itself.
    """
    if isinstance(data, str):
        parsed = _parse_json_container(data) if parse_json else None
        if parsed is None:
            yield data
        else:
            yield from _strings(parsed, parse_json)
    elif isinstance(data, dict):
        for value in data.values():
            yield from _strings(value, parse_json)
    elif isinstance(data, list):
        for item in data:
            yield from _strings(item, parse_json)


def _parse_json_container(text: str) -> dict | list | None:
    """Return ``text`` parsed if it is a JSON object or array, else None."""
    stripped = text.strip()
    if not stripped or stripped[0] not in "[{":
        return None
    try:
        parsed = json.loads(stripped)
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, dict | list) else None
