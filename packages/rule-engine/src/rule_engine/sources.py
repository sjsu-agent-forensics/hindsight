"""Where rules are read from: a ``RuleSource``, by default a directory of JSON files.

The interposer reads its rules once per session, at registration, so each session runs
under a fixed rule set. Anything that can return the current rules can stand in for the
directory (a document store, for example) without changing the engine or enforcement.
"""

import json
import logging
from pathlib import Path
from typing import Protocol

from rule_engine import Rule, RuleError, parse_rule

logger = logging.getLogger(__name__)


class RuleSource(Protocol):
    """Gives the rules currently in force."""

    def load_rules(self) -> list[Rule]:
        """Return every rule in force, sorted by ID.

        Raises:
            RuleError: A stored rule is malformed. Callers must not skip it: a
                skipped deny rule is a silent hole in enforcement.
        """


class DirectoryRuleSource:
    """Rules stored one per JSON file (``*.json``) in a directory.

    One rule per file lets the analysis service add a rule by writing a file and retire
    one by deleting it. A missing directory means no rules, and is logged.
    """

    def __init__(self, path: Path) -> None:
        self.path = path

    def load_rules(self) -> list[Rule]:
        """Read and validate every rule file in the directory.

        Raises:
            RuleError: A file is not valid JSON or not a valid rule, or two files share
                a rule ID. The message names the file.
        """
        if not self.path.is_dir():
            logger.warning(
                "Rule directory %s does not exist; no rules apply", self.path
            )
            return []
        rules: dict[str, Rule] = {}
        for file in sorted(self.path.glob("*.json")):
            try:
                rule = parse_rule(json.loads(file.read_text(encoding="utf-8")))
            except (json.JSONDecodeError, RuleError) as error:
                raise RuleError(f"{file}: {error}") from error
            if rule.id in rules:
                raise RuleError(f"{file}: rule ID {rule.id!r} is used by another file")
            rules[rule.id] = rule
        return [rules[rule_id] for rule_id in sorted(rules)]
