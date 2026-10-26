# provenance

The value-matching provenance index, imported as `provenance`. The interposer keeps one index per
session; the rule engine's replay will rebuild one from a recorded trace. It has no dependencies
and does no I/O, so both get identical results from the same inputs.

- **Indexing.** `ProvenanceIndex.add_trusted()` indexes a trusted input (the user's prompt);
  `add_tool_result()` indexes a tool response with its server, tool, JSON-RPC request ID and
  tool-call ID.
- **Matching.** `match(arguments)` returns, per argument, the indexed values it contains and their
  sources. A value the user supplied is attributed to the user even if a tool also returned it.
- **Extraction.** `ExtractionConfig` sets what is indexed: URLs, emails, file paths, and whole
  strings of at least `min_length` (default 8) characters. Values are lowercased with whitespace
  collapsed; JSON inside text is parsed first.

Only values the model copies are matched; a value it rewrites or encodes is not.

## Tests

```bash
uv run --package provenance pytest packages/provenance
```
