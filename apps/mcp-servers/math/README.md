# math-server

MCP tool server exposing `add` and `multiply` over streamable HTTP at
`http://127.0.0.1:8000/mcp`. It is the tool server for the `mcp-enabled-agent` demo.

Not a workspace member -- it has its own environment (see "The MCP server exception" in the root
`README.md`). From the repo root:

```bash
uv sync --project apps/mcp-servers/math                  # once
uv run --project apps/mcp-servers/math math-server       # Ctrl-C to quit
```
