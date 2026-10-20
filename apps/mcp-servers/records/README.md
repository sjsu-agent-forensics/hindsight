# records-server

Internal MCP tool server over a fake customer database, the confidential source in each scenario.
Customers (`c_001`, `c_002`, ...) and their orders are generated from a fixed seed, so every run
sees the same people; all of it is synthetic (`example.*` emails, `555-01xx` phone numbers).

| tool                          | returns                                              |
| ----------------------------- | ---------------------------------------------------- |
| `find_customer(query)`        | up to 20 matches on name, email or id                |
| `get_customer(id)`            | the full record, including the free-text `notes`     |
| `list_orders(customer_id)`    | that customer's orders                               |
| `update_customer(id, fields)` | the updated record; logged in the effect log         |

`notes` is where untrusted content comes from, and `update_customer.fields` is the sensitive
argument. The tools are fixed lookups, not raw SQL.

Built on `hindsight-server-kit` (see `apps/mcp-servers/kit/README.md`): each session gets its own
in-memory copy of the database, picked by the `Hindsight-Session` header, and a request with no
header uses one default copy.

## Running it

```bash
uv sync --project apps/mcp-servers/records                    # once
uv run --project apps/mcp-servers/records records-server      # Ctrl-C to quit
```

MCP is at `http://127.0.0.1:8011/api/mcp` and the control endpoint at
`http://127.0.0.1:8111/control`. Without a running server, call a tool through the module:

```bash
uv run --project apps/mcp-servers/records fastmcp call apps/mcp-servers/records/src/records_server/main.py get_customer '{"id": "c_001"}'
```

## Fixtures

A fixture is JSON, all keys optional:

```json
{
  "seed": 295,
  "customers": 50,
  "overlays": { "c_005": { "notes": "text planted in this customer's notes" } }
}
```

`database` may name a SQLite file instead of generating one (relative to the fixture file); it is
copied into memory, so runs never change it. Write one with:

```bash
uv run --project apps/mcp-servers/records records-generate records.db --seed 295 --customers 50
```

## Tests

```bash
uv run --project apps/mcp-servers/records pytest apps/mcp-servers/records
```
