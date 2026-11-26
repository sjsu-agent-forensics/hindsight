# rule-engine

Decides whether a tool call may go ahead, from where the values in its arguments came. Imported as
`rule_engine`; used live by the interposer and, through replay, by the analysis service, so both
get the same decisions from the same inputs. It calls no model and does no I/O except reading rule
files.

| Module | Contents |
| --- | --- |
| `rule_engine` | `Rule`, `parse_rule()`, `evaluate()` (rules + call + provenance matches → `Decision`) |
| `rule_engine.sources` | `RuleSource`, and `DirectoryRuleSource`: one rule per JSON file in a directory |
| `rule_engine.replay` | `replay()`: rebuild a session's provenance index from its proxy trace and evaluate each call |

A rule blocks a call to its sink when one of the call's arguments contains an indexed value from its
source:

```json
{
  "id": "no-web-destinations-to-email",
  "effect": "deny",
  "source": {"kind": "tool", "server": "web", "tool": "fetch_page"},
  "sink": {"server": "email", "tool": "send_email", "param": "to"},
  "value_types": ["url", "email"]
}
```

Every field may be left out to match anything, except the sink `server`. `source.kind` is `tool`
(a tool result, the default) or `descriptor` (a tool's description). `value_types` limits the rule
to values of those kinds. Values from the user's trusted inputs never fire a rule. Unknown keys are
refused, since a misspelled key would otherwise widen a rule silently.

## Tests

```bash
uv run --package rule-engine pytest packages/rule-engine
```
