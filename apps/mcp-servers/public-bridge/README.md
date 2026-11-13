# public-bridge

Hosting, bridging, and lifecycle management framework connecting third-party public MCP tools
to hindsight. Configured for the **personal workspace** domain: Google Drive, Google Calendar,
and Gmail.

In hindsight, the agent reaches only the MCP interposer, and every network listener is strictly
bound to the loopback address (`127.0.0.1`). Third-party public MCP tools typically run over
stdio and lack hindsight's session reset endpoints. This package bridges them into Streamable
HTTP MCP endpoints and provides a control protocol (`/control/sessions/{sid}`) for deterministic
session setup and cleanup. A bridge-managed sandbox and effect log are resettable; a third-party
server's own state is resettable only when that server is configured to use the session sandbox.

## Configured Servers

| Server | Package | Port | Tools |
| --- | --- | --- | --- |
| `mock` | built-in mock | 8021 | `gdrive_search`, `gdrive_read_file`, `gdrive_list_files`, `gcalendar_list_events`, `gcalendar_create_event`, `gmail_search`, `gmail_read`, `gmail_draft` |
| `gdrive` | `@anthropic/mcp-server-gdrive` | 8022 | `gdrive_search`, `gdrive_read_file` + auto-format (Docs→Markdown, Sheets→CSV) |
| `gcalendar` | `@cocal/google-calendar-mcp` | 8023 | `list_calendars`, `list_events`, `create_event`, `update_event`, `delete_event`, `find_free_time` |
| `gmail` | `@gongrzhe/server-gmail-autoauth-mcp` | 8024 | `gmail_search`, `gmail_read`, `gmail_draft`, `gmail_send`, `gmail_list_labels` |

## Architecture

- **Loopback Enforcement (N-06)**: Bridges bind only to `127.0.0.1`.
- **Streamable HTTP Bridge**: Exposes tools over HTTP at `http://127.0.0.1:<port>/api/mcp` so the
  interposer can front them seamlessly.
- **Session Sandboxing & Reseedability (E-10, N-06)**: Manages an isolated directory and effect
  log per hindsight session ID, reset by `POST /control/sessions/{sid}`. Stateful upstream tools
  must be configured to use that directory for their own state to be isolated and reseedable.
- **Manifest & Integrity Check**: Computes canonical SHA-256 digests of tool descriptors for the
  interposer's descriptor check.
- **Offline Reference Mock**: A built-in mock server implementing personal workspace tool schemas
  (Drive, Calendar, Gmail) for CI and testing without external network access, npm, or Google
  credentials.

## Authentication (Google OAuth 2.0)

All live Google Workspace servers require OAuth credentials:

1. **Create a Google Cloud project** at <https://console.cloud.google.com>
2. **Enable APIs**: Google Drive API, Google Calendar API, Gmail API
3. **Configure OAuth consent screen** (add yourself as a test user)
4. **Create OAuth Client ID** (Desktop application type)
5. **Download credentials** and save as `gcp-oauth.keys.json`
6. **Set environment variable**:
   ```bash
   export GOOGLE_OAUTH_CREDENTIALS=/path/to/gcp-oauth.keys.json
   ```
7. **First-time Gmail auth** (one-time browser flow):
   ```bash
   npx @gongrzhe/server-gmail-autoauth-mcp auth
   ```

## Configuration

Servers are declared in `public_servers.toml`:

```toml
[public_servers.mock]
transport = "stdio"
command = "python"
args = ["-m", "public_bridge.mock_server"]
port = 8021
control_port = 8121
host = "127.0.0.1"
sandbox_mode = "ephemeral_dir"

[public_servers.gdrive]
transport = "stdio"
command = "npx"
args = ["-y", "@anthropic/mcp-server-gdrive"]
env = { GOOGLE_OAUTH_CREDENTIALS = "${GOOGLE_OAUTH_CREDENTIALS:-./gcp-oauth.keys.json}" }
port = 8022
control_port = 8122
host = "127.0.0.1"
sandbox_mode = "stateless"

[public_servers.gcalendar]
transport = "stdio"
command = "npx"
args = ["-y", "@cocal/google-calendar-mcp"]
env = { GOOGLE_OAUTH_CREDENTIALS = "${GOOGLE_OAUTH_CREDENTIALS:-./gcp-oauth.keys.json}" }
port = 8023
control_port = 8123
host = "127.0.0.1"
sandbox_mode = "stateless"

[public_servers.gmail]
transport = "stdio"
command = "npx"
args = ["-y", "@gongrzhe/server-gmail-autoauth-mcp"]
env = { GOOGLE_OAUTH_CREDENTIALS = "${GOOGLE_OAUTH_CREDENTIALS:-./gcp-oauth.keys.json}" }
port = 8024
control_port = 8124
host = "127.0.0.1"
sandbox_mode = "stateless"
```

## Running

Sync the server's environment:

```bash
uv sync --project apps/mcp-servers/public-bridge --python 3.12
```

Run an individual server bridge:

```bash
# Mock server (offline, no credentials needed)
uv run --project apps/mcp-servers/public-bridge public-bridge mock

# Google Drive (requires OAuth credentials)
uv run --project apps/mcp-servers/public-bridge public-bridge gdrive

# Google Calendar
uv run --project apps/mcp-servers/public-bridge public-bridge gcalendar

# Gmail
uv run --project apps/mcp-servers/public-bridge public-bridge gmail
```

Print tools and SHA-256 descriptors manifest:

```bash
uv run --project apps/mcp-servers/public-bridge public-bridge mock --manifest
```

Print configuration snippet for the interposer's `servers.toml`:

```bash
uv run --project apps/mcp-servers/public-bridge public-bridge gdrive --interposer-snippet
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
