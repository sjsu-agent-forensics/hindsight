# Hindsight — Public MCP Bridge Framework

## Design Specification

**Version:** 0.1  
**Author:** Hindsight Team (individual author and email not specified)  
**Date:** 2026-10-28

## Version History

| Version | Changes |
| --- | --- |
| 0.2 | Configured framework for the Personal Workspace domain (Google Drive, Google Calendar, Gmail) with simulated workspace mock fixtures and stdio servers. |
| 0.1 | Initial design specification for the public MCP bridge framework and its current implementation. |

## Introduction

This document describes the framework that connects selected third-party MCP tools to hindsight
without exposing those tools directly to the agent. The framework can host a stdio MCP process or
proxy an HTTP MCP endpoint, publish the upstream tools through a loopback Streamable HTTP endpoint,
and provide local lifecycle and session-control operations.

The framework is configured for the **Personal Workspace** domain, providing integrations for
Google Drive, Google Calendar, and Gmail alongside an offline reference mock simulating workspace
files, events, and communications. Credentials and local OAuth keys are managed via environment
variables.

## References

- Repository deployment and environment overview: [README.md](../../README.md)
- Bridge setup, configuration, and commands: [public-bridge README](../../apps/mcp-servers/public-bridge/README.md)
- Public server declarations: [public_servers.toml](../../public_servers.toml)
- Launcher for configured bridges: [launch_public_servers.py](../../scripts/launch_public_servers.py)
- Requirements referenced by the deployment design: E-10 (resettable experiment state), E-13
  (reloadable file-based experiment configuration), N-01 (trace storage), N-06 (local-only,
  synthetic-data tool access), and N-09 (offline forensic analysis after a session).

## Requirements

| ID | Requirement | Current design response |
| --- | --- | --- |
| PB-01 | The agent reaches public tools only through the MCP interposer. | Each bridge exposes an MCP endpoint for the interposer. Agent client configuration must continue to list only the interposer's session URL. |
| PB-02 | All bridge network listeners bind to `127.0.0.1`. | Configuration rejects any bridge host other than `127.0.0.1`; MCP and control listeners bind to the configured loopback host. |
| PB-03 | Connect to MCP servers using stdio or HTTP upstream transport. | TOML configuration supports `transport = "stdio"` with a command and arguments, or `transport = "http"` with a target URL. |
| PB-04 | Permit only configured tools when an allowlist is provided. | `allowed_tools` filters tool discovery and rejects calls outside that list. |
| PB-05 | Provide a deterministic session setup/reset and cleanup interface for bridge-managed state. | The local control API seeds or resets a session sandbox, records bridge-observed effects, and discards its state. Session IDs are validated before being used as sandbox directory names. |
| PB-06 | Keep test and development runs independent of external tools or network access. | An offline stdio reference MCP server supports local bridge and lifecycle verification. |
| PB-07 | Make tool descriptors inspectable and fingerprintable. | The manifest command exports filtered descriptors and canonical SHA-256 digests. |
| PB-08 | Support portable, file-based server configuration and secret injection. | TOML supports environment variable placeholders for commands, arguments, headers, and environment values. The checked-in file contains no chosen third-party integration. |
| PB-09 | Start and stop configured bridge processes as a group. | The launcher checks for occupied loopback ports, starts each configured bridge, waits for both listeners, relays logs, and terminates child processes when stopped. |

The framework's sandbox and effect log are session-scoped. This does not automatically make an
upstream tool's internal state session-scoped. A selected stateful server must be configured so
that it uses the corresponding session sandbox, or otherwise provide equivalent reset isolation,
before it can satisfy E-10 for its own state. External side effects and remote services also require
an evaluation-specific synthetic-data and permissions plan.

The bridge configuration is file-based, but experiment configurations covering task, model,
benchmark suite, permissions, and policy version are outside this change. This framework alone
does not claim that E-13 is fully implemented.

## Functional Overview

At startup, `public-bridge` loads a named server entry from TOML and validates its name, transport,
loopback host, port allocation, and supported sandbox mode. For stdio, it launches the configured
MCP process and performs an initialize handshake. For HTTP, it connects to the configured MCP
target. The bridge discovers upstream tools and exposes them over Streamable HTTP at
`http://127.0.0.1:<port>/api/mcp`.

The bridge filters tool discovery and tool calls through the configured allowlist. A call is
forwarded upstream, its session ID is read from the `Hindsight-Session` request header when
provided, and an effect record is appended to that bridge's in-memory session log. The stdio client
handles JSON-RPC request IDs, responses, initialization, liveness pings, stderr relay, and process
shutdown.

A separate control listener on `127.0.0.1:<control_port>` supports health checks, session listing,
seeding/reset, effect retrieval, and discard. By default the control port is the MCP port plus 100.
The orchestrator uses control operations around an experiment; the agent does not receive this
endpoint. The launcher can start all configured bridges and stops the group when interrupted or
when a bridge exits.

The manifest mode connects to the upstream server, obtains its available tools, applies the
allowlist, and emits normalized descriptor data and SHA-256 hashes. A second CLI mode emits a
TOML snippet that can be used to configure an interposer server entry.

## Configuration / External Interfaces

### Server configuration

`public_servers.toml` is the portable source of server declarations. Supported fields include:

| Field | Meaning |
| --- | --- |
| `transport` | `stdio` or `http`; defaults to `stdio`. |
| `command`, `args`, `env`, `cwd` | Process launch configuration for stdio servers. |
| `target_url`, `headers` | MCP endpoint and request headers for HTTP upstreams. |
| `port`, `control_port`, `host` | Local MCP and control listeners; host must be `127.0.0.1`. |
| `allowed_tools` | Optional tool-name allowlist. |
| `sandbox_mode`, `fixture_dir` | Sandbox policy label and optional fixture source directory. |
| `startup_timeout` | Upstream startup or HTTP timeout in seconds. |

Use `${ENV_VAR}` or `${ENV_VAR:-default}` placeholders to inject values from environment
variables. Store secrets in the runtime environment rather than committing them to configuration.
The configuration file should use portable paths or environment-specific configuration overlays.

### Network interfaces

| Interface | Address | Caller |
| --- | --- | --- |
| Public MCP bridge | `http://127.0.0.1:<port>/api/mcp` | MCP interposer |
| Bridge control | `http://127.0.0.1:<control_port>/control` | Local orchestrator and operators |
| HTTP upstream, when configured | URL from `target_url` | Bridge process |
| Stdio upstream, when configured | Child process stdin/stdout | Bridge process |

Control routes are `GET /control/health`, `GET /control/sessions`, `POST /control/sessions/{sid}`,
`DELETE /control/sessions/{sid}`, and `GET /control/sessions/{sid}/effects`. Session IDs are
limited to 1–128 letters, digits, underscores, or hyphens.

### Runtime and dependencies

The bridge is an independent Python 3.12 project managed by uv, outside the shared workspace. Its
own environment isolates the newer MCP SDK major version used by MCP servers from the agent's
workspace dependencies. Runtime dependencies are FastMCP, Starlette, Uvicorn, and AnyIO. The
development dependency group includes pytest, pytest-asyncio, and httpx.

The bridge does not add a specific third-party MCP server dependency. The configured server's
runtime (for example, a Python command or another locally installed executable) must be available
on the host.

## Debug

### Logging

- Uvicorn listeners use warning-level logging by default.
- Upstream stdio stderr is relayed with a `[public:<server-name>]` prefix.
- The launcher prefixes combined child output with `[public:<server-name>]` and suppresses uv's
  `UV_NATIVE_TLS` informational lines.
- Upstream tool call exceptions are logged with a stack trace and returned to the MCP caller as an
  error result. Tool output and arguments are untrusted data and are not treated as authorization.
- The `--manifest` CLI option prints the discovered tool descriptors and their hashes as JSON.

### Counters

No metrics or persistent counters are currently exported. The control endpoint exposes ephemeral
session counts through `GET /control/sessions`, and `GET /control/sessions/{sid}/effects` returns
the in-memory list of observed calls for a session. These records are diagnostic metadata, not the
append-only raw agent/proxy trace store described by N-01.

## Implementation

The implementation is isolated under `apps/mcp-servers/public-bridge/`:

| Subtask | Module(s) | Responsibility |
| --- | --- | --- |
| Parse declarative configuration | `config.py` | Typed stdio/HTTP configurations, environment expansion, host and port validation. |
| Supervise stdio MCP processes | `stdio_process.py` | Launch, handshake, JSON-RPC, stderr relay, liveness, and shutdown. |
| Proxy MCP tools | `bridge.py` | Discover and filter tools, dispatch calls, record effects, expose Streamable HTTP. |
| Manage session sandbox state | `sandbox.py` | Create, seed, reset, inspect, and discard bridge-managed session directories and effects. |
| Expose local control routes | `control.py` | Health, session lifecycle, and effect inspection API. |
| Host services and teardown | `serve.py` | Run MCP and control listeners and clean up resources on shutdown. |
| Export descriptors | `manifest.py` | Canonical descriptor hashes and interposer config snippets. |
| CLI and offline reference server | `main.py`, `mock_server.py` | Run a configured bridge, export manifest/config, and provide an offline MCP fixture. |
| Start configured bridges together | `scripts/launch_public_servers.py` | Port checks, startup readiness, log relay, and group shutdown. |

The implementation remains separate from the shared uv workspace because the server-side MCP
SDK major version is incompatible with the agent's MCP client dependency. Apps communicate through
MCP/HTTP and do not import each other.

### Startup and shutdown order

1. Install the bridge project into its own environment with uv.
2. Start configured upstream servers and bridge MCP/control listeners.
3. Wait for bridge listeners to become ready.
4. Start the orchestrated session; seed/reset each selected tool's session state through its
   control interface.
5. Configure the agent with the interposer's session URL; the interposer forwards approved calls
   to the local bridge.
6. After the session ends, persist traces through the relevant trace components, retrieve any
   required bridge effects, discard session sandboxes, then stop bridge processes.
7. Run forensic analysis as a separate post-session worker, outside the agent's execution path.

Steps involving orchestrator, interposer, trace persistence, and forensic-worker integration are
deployment integration points; they are not implemented by this bridge package itself.

## Testing

### General Approach

Exercise the framework entirely on loopback with the reference stdio mock so tests need no public
service credentials, package downloads at test time, or external network. Cover valid and invalid
configuration, process lifecycle, MCP tool discovery/calls, allowlist behavior, the control API,
session reset behavior, and stable descriptor hashing. Component/system verification should also
confirm that the agent lists only the interposer endpoint and that selected stateful upstream tools
use synthetic per-session state.

### Unit Tests

The following test coverage is present in `apps/mcp-servers/public-bridge/tests/`:

- `test_config.py`: valid TOML, environment expansion, loopback-only validation, port collision,
  and missing configuration file.
- `test_stdio_process.py`: stdio initialize/list/call/ping/shutdown lifecycle with the mock server.
- `test_bridge.py`: Streamable HTTP initialize, tool listing and invocation, and effect recording.
- `test_control.py`: health, session listing, seed, effect retrieval, and discard routes.
- `test_sandbox.py`: fixture copy, reset to initial fixture state, effect recording, and discard.
- `test_manifest.py`: canonical SHA-256 determinism, manifest construction, and interposer snippet.

These tests are present in the repository; execution status for this specification revision is not
recorded. Run them with:

```bash
uv run --project apps/mcp-servers/public-bridge pytest apps/mcp-servers/public-bridge/tests
```

## Appendix

### Example offline configuration

```toml
[public_servers.mock]
transport = "stdio"
command = "python"
args = ["-m", "public_bridge.mock_server"]
port = 8021
control_port = 8121
host = "127.0.0.1"
sandbox_mode = "ephemeral_dir"
startup_timeout = 15.0
```

### Current scope limits

- Configured servers target Google Workspace tools (`@anthropic/mcp-server-gdrive`, `@cocal/google-calendar-mcp`, `@gongrzhe/server-gmail-autoauth-mcp`). Live execution requires Google OAuth 2.0 credentials (`GOOGLE_OAUTH_CREDENTIALS`).
- Session isolation applies directly to bridge-managed directories and effect logs. Generic
  third-party state is not automatically moved into those directories or reset by the bridge.
- Control routes are loopback-only but do not implement authentication; they are intended for
  local orchestrator/operator use, not agent access.
- The bridge does not replace the N-01 append-only trace store or implement N-09 analysis.
- The framework does not itself enforce outbound network policy for upstream tools; deployment
  must configure selected tools and host networking to meet the evaluation policy.
