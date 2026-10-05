# weather-server

MCP tool server exposing one tool, `weather_san_jose`, which returns a fixed reading
(`temperature_celsius`, `condition`). A second tool server for the `mcp-enabled-agent` demo,
alongside `math-server`.

Unlike `math-server`, this one serves over streamable HTTP rather than stdio, so it listens on a
port and must be started before the demo rather than being spawned by it.

Not a workspace member -- it has its own environment (see "The MCP server exception" in the root
`README.md`). From the repo root:

```bash
uv sync --project apps/mcp-servers/weather              # once
uv run --project apps/mcp-servers/weather weather-server   # Ctrl-C to quit
```

It serves at `http://127.0.0.1:8001/api/mcp`, which is the URL `mcp-enabled-agent` connects to.
The host, port and path are set explicitly in `main()` rather than left at fastmcp's defaults, so
that it does not collide with other servers.

Note that it binds `0.0.0.0`, not `127.0.0.1`, so the tools are reachable from other machines on
the network. Change the `host` argument in `main()` if you want it local-only.
