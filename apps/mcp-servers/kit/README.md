# hindsight-server-kit

Library shared by the internal MCP tool servers (`records`, `email`, `web`, `files`, `clock`,
`messaging` and `calendar`). It is not a server and runs nothing on its own. It owns:

- **Session routing.** `store.get()` returns the state of the session named by the
  `Hindsight-Session` request header. A header naming a session that was never seeded is an MCP
  error, not a silent fallback. A request with no header gets one default state, loaded on first
  use from `HINDSIGHT_FIXTURE` (else the server's benign fixture), so `fastmcp call` and quick
  manual runs work with nothing else running. Reset the default state by restarting the server.
- **The control endpoint**, on the MCP port plus 100:

  | request                                 | does                                     |
  | --------------------------------------- | ---------------------------------------- |
  | `POST /control/sessions/{sid}`          | seed `sid`, or reset it if it exists     |
  | `GET /control/sessions/{sid}/effects`   | that session's effect log                |
  | `DELETE /control/sessions/{sid}`        | discard it                               |
  | `GET /control/sessions`, `/control/health` | list sessions, liveness               |

  The `POST` body is optional JSON: `fixture` (path to this server's fixture file), or `scenario`
  (an id, resolved to `$HINDSIGHT_SCENARIOS/<scenario>/<server>.json`, default `./scenarios`; a
  missing file leaves this server on its benign fixture), `time` (ISO 8601 with a UTC offset) and
  `seed` (integer). The orchestrator can send the same body to every server.
- **The effect log**, an append-only list per session of what each call changed, written with
  `session.record_effect(tool, **details)`.
- **Determinism**: each session has a fixed scenario time (`session.now`, default
  `2026-11-06T09:00:00-08:00`) and its own seeded `session.random`, so a reseed gives identical
  responses.
- **Manifest export**: `<name>-server --manifest` prints `tools/list` with a SHA-256 of each
  canonical descriptor, for the interposer's pin check.

## Using it in a server

```python
from fastmcp import FastMCP
from hindsight_server_kit import SessionStore, serve

server = FastMCP("Records Server")
store = SessionStore("records", load_fixture=load_records)  # module level, so `fastmcp call` finds it


@server.tool()
def get_customer(id: str) -> dict:
    """..."""
    return store.get().data.get_customer(id)  # this session's data


def main() -> None:
    serve(server, store, port=8011)  # MCP on 8011, control on 8111
```

`load_fixture(path)` gets the session's fixture file, or `None` for the benign default, and returns
whatever the tools read from `session.data`. Servers whose data is JSON can use
`load_json_fixture(path, benign)`, which also understands overlays: a fixture with
`"base": "benign"` starts from the benign fixture, appends the items under `add`, and replaces
fields on the items named under `patch` (found by their `*_id` field anywhere in the data), so an
overlay lists only what it changes:

```json
{"base": "benign", "patch": {"w_001": {"text": "updated page text"}}}
```

`next_id(items, "email_id", "e_")` gives the id for a new item, based only on what is already in
the session, so a rerun assigns the same ids.

Sync tools run on worker threads; take `session.lock` around anything that reads and then writes.

Each server lists the kit as a path dependency in its own `pyproject.toml`:

```toml
dependencies = ["fastmcp>=4.0.8", "hindsight-server-kit"]

[tool.uv.sources]
hindsight-server-kit = { path = "../kit", editable = true }
```

The kit depends only on `fastmcp`. It must never depend on `hindsight-core`, or the `mcp<2` cap
comes back (see "The MCP server exception" in the root `README.md`).

## Tests

`hindsight_server_kit.testing.running(server, store)` serves a server on free local ports for a
test and gives back a client that can send the session header; the in-memory fastmcp client cannot.

```bash
uv run --project apps/mcp-servers/kit pytest apps/mcp-servers/kit
```
