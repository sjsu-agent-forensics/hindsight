# math-server

MCP tool server exposing `add` and `multiply` over stdio. Whoever uses it spawns it as a subprocess
-- you do not normally run it by hand: the `mcp-enabled-agent` demo directly, and the interposer
(`apps/interposer`) once per session for the agent in `apps/agent`.

Not a workspace member -- it has its own environment (see "The MCP server exception" in the root
`README.md`). Install it once, from the repo root, so the demo and the interposer can launch it:

```bash
uv sync --project apps/mcp-servers/math
```

To run it standalone for debugging, it speaks JSON-RPC on stdin/stdout:

```bash
uv run --project apps/mcp-servers/math math-server       # Ctrl-D to quit
```
