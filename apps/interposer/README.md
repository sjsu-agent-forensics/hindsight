# interposer

The hindsight proxy: one gateway process between the agent and every MCP server it uses. It
relays every JSON-RPC message both ways unchanged, and admits only traffic that belongs to a
registered hindsight session.

Upstream servers are listed in [`servers.toml`](servers.toml), in one of three forms:

| Keys                 | Server                                                                         |
| -------------------- | ------------------------------------------------------------------------------ |
| `url`                | a remote server, reached over streamable HTTP                                  |
| `project`, `program` | a uv project's console script, found in that project's `.venv` on any OS      |
| `command`, `args`    | any command, for example `docker run -i --rm <image>` for a containerized server |

The last two are stdio servers. The agent cannot tell them from HTTP ones: every server is served
to it over HTTP at the same kind of path.

## Stdio servers

Each session gets its own child process for every stdio server, started when the session is
registered (registration fails with HTTP 502 if one cannot start) and stopped when it ends. No
server state carries between runs. The child is stopped with its whole process tree: the MCP SDK
uses a Windows job object, or a process group on POSIX.

A child serves one MCP session. A second MCP session to the same stdio server within one hindsight
session is refused with HTTP 409 and logged. A child is never restarted: if it dies, the gateway
records a `crash` event and every later call to it fails with an "unavailable" error for the rest
of the session.

A FastMCP server can take several seconds to answer its first request (4–6 s measured on
Windows), all of it the server's own startup. Starting children at registration lets that overlap
with the agent's startup.

## Sessions

Each agent run is one hindsight session, registered by the launcher (`apps/launcher`) before the
agent starts and ended by it afterwards. The gateway serves:

| Route                                 | Used by  | Purpose                                                    |
| ------------------------------------- | -------- | ---------------------------------------------------------- |
| `POST /sessions`                      | launcher | register a session; answers with its trace directory       |
| `DELETE /sessions/<session-id>`       | launcher | end it; answers with the MCP sessions opened under it      |
| `/s/<session-id>/mcp/<server>`        | agent    | one server's MCP endpoint within the session               |

The registration payload is `{"session_id", "trusted_inputs"}`, where `trusted_inputs` is a list
of `{"kind", "content"}` and `kind` is `user_prompt`, `system_prompt`, `attached_file` or `turn`.
The launcher sends only the user's prompt for now.

The agent opens one MCP session per server path, each relayed to its own upstream session, so one
hindsight session maps to several `Mcp-Session-Id` values; the gateway records them. Ending a
session closes all of them, upstream included, before the end call returns. A session nobody ends
is ended after `--idle-timeout` seconds without a request (default 1800).

Requests with no session prefix, or for an unknown or ended session, are refused with an HTTP error
(so an agent pointed at a bad session fails at startup) and written to `logs/refused.jsonl`, one
JSON object per line. Refused traffic is never part of any session.

Every upstream call has a timeout (`--upstream-timeout`, default 30 s). On expiry the agent gets an
MCP error, the gateway records a `timeout` event, and a late answer from the server is dropped. If
an upstream server fails or closes mid-session, the agent's MCP session stays open but every call
to that server returns an "unavailable" error, rather than hanging.

## Proxy trace

Each session's trace is written to `<trace-dir>/<session-id>/proxy.jsonl` (default `traces/` at the
repo root), next to the agent's `agent.jsonl`. The event format is the `trace-schema` package's.
Events, numbered in order by `hindsight.seq`:

| Event          | Recorded when                                                                |
| -------------- | ---------------------------------------------------------------------------- |
| `registration` | the session is registered, with its trusted inputs                           |
| `descriptors`  | a `tools/list` response passes through, as the server sent it                |
| `call`         | a `tools/call` request passes through, as the agent sent it                  |
| `decision`     | right after each call; always `allow` until rules are enforced               |
| `response`     | the call's response passes through, matched to it by JSON-RPC request ID     |
| `timeout`      | a request got no answer within the upstream timeout; distinct from a block   |
| `crash`        | the upstream failed or closed while the agent was connected, once per MCP session |

Every event for a call carries the agent's tool-call ID (`gen_ai.tool.call.id`), copied from the
request's `params._meta`, which is how the agent trace joins this one. Events are written by a
background task, off the request path; ending a session returns only after its events are on
disk, so the trace is complete when the launcher's end call returns.

## Running it

Set up each stdio server's environment once (`uv sync --project apps/mcp-servers/math`) and start
the HTTP servers (see the root `README.md`), then the gateway:

```bash
uv run --package interposer interposer            # http://127.0.0.1:8000
uv run --package interposer interposer --help     # --config, --trace-dir, --upstream-timeout, ...
```

Then run the agent through the launcher (see `apps/launcher/README.md`).

## Tests

The tests run the gateway, and a small HTTP MCP server for it, on free local ports, so they need
nothing else running. From the repo root:

```bash
uv run --package interposer pytest apps/interposer                # everything (about 40 s)
uv run --package interposer pytest apps/interposer -m "not slow"  # skip the process tests
```

| File                  | Covers                                                                  |
| --------------------- | ----------------------------------------------------------------------- |
| `test_passthrough.py` | Phase 1: `tools/list` through the gateway matches the server; calls relay |
| `test_sessions.py`    | Phase 2: refusals, the session-to-MCP-session mapping, idle timeout, a dead server |
| `test_tracing.py`     | Phase 3: the proxy trace, with two calls in flight at once              |
| `test_processes.py`   | Phase 4 (`slow`): per-session children, timeouts and crashes            |

`test_processes.py` uses the faults test server and skips unless it is set up:
`uv sync --project apps/mcp-servers/faults`. The helpers the tests share are in
`interposer.testing`. The trace join itself is tested in `packages/trace-schema`.

Two scripts stay scripts, since they need things a test cannot provide:

```bash
# median per-call latency, direct vs through a running gateway (needs weather-server running)
uv run --package interposer python apps/interposer/scripts/bench_latency.py     http://127.0.0.1:8001/api/mcp http://127.0.0.1:8000 weather weather_san_jose

# after a launcher run, check that its two traces are complete and join exactly
uv run --package interposer python apps/interposer/scripts/check_traces.py traces/<session-id>
```

A real agent run depends on a model, so its traces are checked after the fact rather than in a
test; the launcher prints the trace directory when it ends.
