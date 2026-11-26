# scenario-servers

Attack test servers for the interposer's end-to-end scenarios (`apps/interposer/scenarios`), not
for normal use. Their content is attacker-written on purpose. Each is a stdio server, so the
interposer runs a fresh copy for every session and no run can affect the next.

| Program | Tool | Role in the scenarios |
| --- | --- | --- |
| `scenario-browser` | `fetch_page(url)` | Pages with prompt injections hidden in their text |
| `scenario-mail` | `send_email(to, subject, body)` | The sink attacks try to reach; sends nothing |
| `scenario-notes` | `read_notes()` | The user's private notes, with a distinctive account number |
| `scenario-forecast` | `get_forecast(city)` | A working tool whose description is poisoned |

Not a workspace member -- it has its own environment (see "The MCP server exception" in the root
`README.md`). Install it once, from the repo root:

```bash
uv sync --project apps/mcp-servers/scenarios
```
