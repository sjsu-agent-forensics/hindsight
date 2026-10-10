# interposer

The hindsight proxy: one gateway process between the agent and every MCP server it uses. This is
Phase 1 of the implementation plan, a passthrough. Each upstream server is served at its own path,
`/mcp/<name>`, and every JSON-RPC message is forwarded both ways unchanged. Each agent session gets
its own persistent upstream session.

Upstream servers are listed in [`servers.toml`](servers.toml). Only remote HTTP servers are served
for now; stdio servers are refused at startup until per-session process management is added.

## Running it

Start the upstream servers first (see the root `README.md`), then the gateway:

```bash
uv run --package interposer interposer            # http://127.0.0.1:8000/mcp/<name>
uv run --package interposer interposer --help     # --config, --host, --port
```

The agent (`apps/agent`) then connects to `http://127.0.0.1:8000/mcp/weather` instead of the
weather server.

## Phase 1 checks

Both scripts take the server's direct URL and its URL through the gateway, and need both running.

```bash
# tools/list through the gateway must match tools/list direct
uv run --package interposer python apps/interposer/scripts/check_descriptors.py \
    http://127.0.0.1:8001/api/mcp http://127.0.0.1:8000/mcp/weather

# median per-call latency over persistent sessions, direct vs through the gateway
uv run --package interposer python apps/interposer/scripts/bench_latency.py \
    http://127.0.0.1:8001/api/mcp http://127.0.0.1:8000/mcp/weather weather_san_jose
```
