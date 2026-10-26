# rules

The interposer's rules: one JSON rule per `*.json` file. Each session runs under the rules here when
it is registered, for its whole life; a rule added later applies from the next session. A malformed
file makes registration fail, naming the file, rather than being skipped.

The analysis service will add validated rules here; people can too, for tests and experiments. The
rule format is described in `packages/rule-engine/README.md`. For example:

```json
{
  "id": "no-web-destinations-to-email",
  "effect": "deny",
  "source": {"kind": "tool", "server": "web"},
  "sink": {"server": "email", "tool": "send_email"},
  "value_types": ["url", "email"]
}
```

With no rule files, every call is allowed. Point the interposer elsewhere with `--rules-dir`.
