# interposer

The hindsight proxy: one gateway process between the agent and every MCP server it uses. It
relays every JSON-RPC message both ways unchanged, and admits only traffic that belongs to a
registered hindsight session.

Upstream servers are listed in [`servers.toml`](servers.toml). Only remote HTTP servers are served
for now; stdio servers are refused at startup until per-session process management is added.

## Sessions

Each agent run is one hindsight session, registered by the launcher (`apps/launcher`) before the
agent starts and ended by it afterwards. The gateway serves:

| Route                                 | Used by  | Purpose                                                    |
| ------------------------------------- | -------- | ---------------------------------------------------------- |
| `POST /sessions`                      | launcher | register a session: `{"session_id", "trusted_inputs"}`     |
| `DELETE /sessions/<session-id>`       | launcher | end it; answers with the MCP sessions opened under it      |
| `/s/<session-id>/mcp/<server>`        | agent    | one server's MCP endpoint within the session               |

`trusted_inputs` is a list of `{"kind", "content"}`, where `kind` is `user_prompt`,
`system_prompt`, `attached_file` or `turn`. The launcher sends only the user's prompt for now.

The agent opens one MCP session per server path, each relayed to its own upstream session, so one
hindsight session maps to several `Mcp-Session-Id` values; the gateway records them. Ending a
session closes all of them, upstream included, before the end call returns. A session nobody ends
is ended after `--idle-timeout` seconds without a request (default 1800).

Requests with no session prefix, or for an unknown or ended session, are refused with an HTTP error
(so an agent pointed at a bad session fails at startup) and written to `logs/refused.jsonl`, one
JSON object per line. Refused traffic is never part of any session.

If an upstream server fails or closes mid-session, the agent's MCP session stays open but every
call to that server returns an "unavailable" error, rather than hanging.

## Running it

Start the upstream servers first (see the root `README.md`), then the gateway:

```bash
uv run --package interposer interposer            # http://127.0.0.1:8000
uv run --package interposer interposer --help     # --config, --log-dir, --idle-timeout, --host, --port
```

Then run the agent through the launcher (see `apps/launcher/README.md`).

## Checks

Each script needs the upstream server running.

```bash
# Phase 1: tools/list through the gateway must match tools/list direct
uv run --package interposer python apps/interposer/scripts/check_descriptors.py \
    http://127.0.0.1:8001/api/mcp http://127.0.0.1:8000 weather

# Phase 1: median per-call latency over persistent sessions, direct vs through the gateway
uv run --package interposer python apps/interposer/scripts/bench_latency.py \
    http://127.0.0.1:8001/api/mcp http://127.0.0.1:8000 weather weather_san_jose

# Phase 2: refusals, the session-to-MCP-session mapping, idle timeout, a dead upstream.
# Starts its own gateway in-process, so it does not need one running.
uv run --package interposer python apps/interposer/scripts/check_sessions.py \
    http://127.0.0.1:8001/api/mcp weather_san_jose
```

The first two register a session of their own for the check, so they also need the gateway running.
