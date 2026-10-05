# mcp-enabled-agent

Agent demo that reaches its tools over MCP. It connects to two tool servers at once -- `math-server`
over stdio and `weather-server` over HTTP -- asks one question that needs both, and prints the
answer followed by each tool call and response.

The two transports behave differently when starting up:

- `math-server` is spawned by the demo as a subprocess, so it only needs to be installed.
- `weather-server` listens on a port, so it must already be running in another terminal.

From the repo root, once:

```bash
uv sync --project apps/mcp-servers/math
uv sync --project apps/mcp-servers/weather
```

Then start the weather server and leave it running:

```bash
uv run --project apps/mcp-servers/weather weather-server
```

And in a second terminal:

```bash
uv run --package mcp-enabled-agent mcp-enabled-agent
```

The demo needs a local Ollama model (see `hindsight.llm.get_llm`). Small local models call these
tools inconsistently -- a run may skip a tool and answer from its own arithmetic -- so the printed
tool trace varies between runs even when nothing is wrong.
