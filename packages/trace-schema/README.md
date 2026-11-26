# trace-schema

The trace event format shared by the interposer and the agent, imported as `trace_schema`. It has
no dependencies, so any app can use it.

Each hindsight session gets a directory holding two JSON Lines files: `proxy.jsonl`, written by the
interposer, and `agent.jsonl`, written by the agent. Each line is one flat JSON event. Field names
follow the OpenTelemetry GenAI and MCP semantic conventions where one exists (`gen_ai.*`, `mcp.*`,
`jsonrpc.*`, `error.type`); hindsight's own fields are under `hindsight.*`.

| Module contents | Purpose |
| --- | --- |
| field name constants (`SESSION_ID`, `TOOL_CALL_ID`, ...) | the only place field names are spelled |
| `ProxyEventType`, `AgentEventType` | the event types each producer writes |
| `TOOL_CALL_ID_META_KEY` | the `params._meta` key that joins agent and proxy events |
| `make_event()`, `dumps()`, `read_events()` | build, serialize and read events |
| `trace_schema.validate` | check that a session's two traces are complete and join exactly |

## Tests

```bash
uv run --package trace-schema pytest packages/trace-schema
```
