# mcp-enabled-agent

Agent demo that reaches its tools over MCP. It connects to the five internal workspace servers
(`email`, `web`, `drive`, `chat`, `calendar`) over HTTP and gives the agent one task that spans the
drive and email ("find the notes from Monday's pipeline meeting and email Jamie Park a summary"),
then prints the answer followed by each tool call and response.

The servers listen on ports, so start them first and leave them running (the launcher syncs each
server on first start):

```bash
python scripts/launch_servers.py
```

Then, in a second terminal:

```bash
uv run --package mcp-enabled-agent mcp-enabled-agent
```

The demo sends no `Hindsight-Session` header yet, so every call uses each server's default state;
restart the launcher to reset it. Effects such as the sent email are recorded only in the servers'
state and effect logs, never delivered.

The demo needs a local Ollama model (see `hindsight.llm.get_llm`). Small local models call these
tools inconsistently -- a run may skip a tool or stop early -- so the printed tool trace varies
between runs even when nothing is wrong.
