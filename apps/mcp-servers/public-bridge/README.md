# public-bridge

Hosting, bridging, and lifecycle management framework connecting third-party public MCP tools
to hindsight.

In hindsight, the agent reaches only the MCP interposer, and every network listener is strictly
bound to the loopback address (`127.0.0.1`). Third-party public MCP tools typically run over
stdio and lack hindsight's session reset endpoints. This package bridges them into Streamable
HTTP MCP endpoints and provides a control protocol (`/control/sessions/{sid}`) for deterministic
session setup and cleanup. A bridge-managed sandbox and effect log are resettable; a third-party
server's own state is resettable only when that server is configured to use the session sandbox.
Select and configure the actual public servers once the evaluation domain is known.

## Architecture

- **Loopback Enforcement (N-06)**: Bridges bind only to `127.0.0.1`.
- **Streamable HTTP Bridge**: Exposes tools over HTTP at `http://127.0.0.1:<port>/api/mcp` so the
  interposer can front them seamlessly.
- **Session Sandboxing & Reseedability (E-10, N-06)**: Manages an isolated directory and effect
  log per hindsight session ID, reset by `POST /control/sessions/{sid}`. Stateful upstream tools
  must be configured to use that directory for their own state to be isolated and reseedable.
- **Manifest & Integrity Check**: Computes canonical SHA-256 digests of tool descriptors for the
  interposer's descriptor check.
- **Offline Reference Mock**: A built-in mock server implementing public tool schemas (fetch,
  read_file, write_file, execute_query) for CI and testing without external network access or npm.

## Configuration

Servers are declared in `public_servers.toml`. The checked-in configuration contains an offline
reference server only; it does not select third-party tools. Add a server entry after its package,
transport, permissions, and synthetic fixture strategy have been chosen:

```toml
[public_servers.mock]
transport = "stdio"
command = "python"
args = ["-m", "public_bridge.mock_server"]
port = 8021
control_port = 8121
host = "127.0.0.1"
sandbox_mode = "ephemeral_dir"

# Future entries use the same fields: transport, command/args (or target_url),
# loopback port, control_port, optional allowed_tools and sandbox_mode.
```

## Running

Sync the server's environment:

```bash
uv sync --project apps/mcp-servers/public-bridge --python 3.12
```

Run an individual server bridge:

```bash
uv run --project apps/mcp-servers/public-bridge public-bridge mock
```

Print tools and SHA-256 descriptors manifest:

```bash
uv run --project apps/mcp-servers/public-bridge public-bridge mock --manifest
```

Print configuration snippet for the interposer's `servers.toml`:

```bash
uv run --project apps/mcp-servers/public-bridge public-bridge mock --interposer-snippet
```

Launch all configured public tool bridges at once:

```bash
python scripts/launch_public_servers.py
```

## Control Endpoint

Served on `http://127.0.0.1:<control_port>/control`:

| Method & Route | Purpose |
| --- | --- |
| `GET /control/health` | Liveness and subprocess health check |
| `GET /control/sessions` | List active session IDs |
| `POST /control/sessions/{sid}` | Seed or reset session sandbox to initial fixture state |
| `DELETE /control/sessions/{sid}` | Discard session sandbox and temporary resources |
| `GET /control/sessions/{sid}/effects` | Retrieve the session's recorded effect log |

Session IDs are restricted to letters, digits, `_` and `-` (up to 128 characters), so control
requests cannot use path traversal to escape the sandbox root. The control API is for the local
orchestrator; configure the agent with only the interposer's session-scoped MCP URL.

## Tests

```bash
uv run --project apps/mcp-servers/public-bridge pytest apps/mcp-servers/public-bridge/tests
```
