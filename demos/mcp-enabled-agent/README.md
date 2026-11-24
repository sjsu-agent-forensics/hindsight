# mcp-enabled-agent

Agent demo that reaches its tools over MCP. It connects to `math-server` over stdio, and to
`weather-server` and the five internal workspace servers (`email`, `web`, `drive`, `chat`,
`calendar`) over HTTP. It asks one question that needs math, the weather, and a task spanning the
drive and email ("find the notes from Monday's pipeline meeting and email Jamie Park a summary"),
then prints the answer followed by each tool call and response.

The two transports behave differently when starting up:

- `math-server` is spawned by the demo as a subprocess, so it only needs to be installed.
- The HTTP servers listen on ports, so they must already be running in another terminal.

From the repo root, once:

```bash
uv sync --project apps/mcp-servers/math
```

Then start the HTTP servers and leave them running (the launcher syncs each server on first start):

```bash
python scripts/launch_servers.py
```

And in a second terminal:

```bash
uv run --package mcp-enabled-agent mcp-enabled-agent
```

The demo sends no `Hindsight-Session` header yet, so every call uses each server's default state;
restart the launcher to reset it. Effects such as the sent email are recorded only in the servers'
state and effect logs, never delivered.

The demo needs a local Ollama model (see `hindsight.llm.get_llm`). Small local models call these
tools inconsistently -- a run may skip a tool and answer from its own arithmetic -- so the printed
tool trace varies between runs even when nothing is wrong.
