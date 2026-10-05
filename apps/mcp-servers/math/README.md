# math-server

MCP tool server exposing `add` and `multiply` over stdio. It is the tool server for the
`mcp-enabled-agent` demo, which spawns it as a subprocess -- you do not normally run it by hand.

Not a workspace member -- it has its own environment (see "The MCP server exception" in the root
`README.md`). Install it once, from the repo root, so the demo can launch it:

```bash
uv sync --project apps/mcp-servers/math
```

To run it standalone for debugging, it speaks JSON-RPC on stdin/stdout:

```bash
uv run --project apps/mcp-servers/math math-server       # Ctrl-D to quit
```
