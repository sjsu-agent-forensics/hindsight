# clock-server

Internal MCP tool server with one tool, `get_datetime`, which returns the session's scenario time
(`datetime`, `date`, `time`, `weekday`, `utc_offset`). It replaces the in-process
`get_local_datetime` tool, the last one the agent ran outside MCP.

The time is fixed, not the wall clock, so a rerun sees the same time as the original run. It
defaults to `2026-11-06T09:00:00-08:00`; a session sets its own with the `time` key when it is
seeded through the control endpoint, and the default state reads `HINDSIGHT_TIME`. Built on
`hindsight-server-kit` (see `apps/mcp-servers/kit/README.md`).

```bash
uv sync --project apps/mcp-servers/clock                  # once
uv run --project apps/mcp-servers/clock clock-server      # Ctrl-C to quit
```

MCP is at `http://127.0.0.1:8015/api/mcp` and the control endpoint at
`http://127.0.0.1:8115/control`. Without a running server:

```bash
uv run --project apps/mcp-servers/clock fastmcp call apps/mcp-servers/clock/src/clock_server/main.py get_datetime '{}'
```

## Tests

```bash
uv run --project apps/mcp-servers/clock pytest apps/mcp-servers/clock
```
