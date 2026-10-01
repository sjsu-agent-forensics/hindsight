# mcp-enabled-agent

Agent demo that reaches its tools over MCP. It connects to the math server, asks one arithmetic
question, and prints the answer followed by each tool call and response.

Start the math server first (see `apps/mcp-servers/math/README.md`), then from the repo root:

```bash
uv run --package mcp-enabled-agent mcp-enabled-agent
```
