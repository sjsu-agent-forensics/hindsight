# faults-server

Test MCP server over stdio for the interposer's Phase 4 tests
(`apps/interposer/tests/test_processes.py`), not for the agent. Its two tools let a test make a
call hang and find the server's process:

| Tool | Does |
| --- | --- |
| `pid` | returns the server's process ID, so a test can kill it mid-session |
| `slow` | sleeps for the given number of seconds before answering, blocking the server |

Not a workspace member -- it has its own environment (see "The MCP server exception" in the root
`README.md`). Install it once, from the repo root:

```bash
uv sync --project apps/mcp-servers/faults
```
