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

Every upstream call has a timeout (`--upstream-timeout`, default 30 s). On expiry the gateway
records a `timeout` event and a late answer from the server is dropped. If an upstream server fails
or closes mid-session, the agent's MCP session stays open but every call to that server fails as
"unavailable", rather than hanging.

When the gateway itself fails a tool call (blocked, timed out or unavailable), it answers with a
tool error result (`isError: true`) explaining why, the way the MCP spec has tool execution errors
reported. The model sees a failed call and the agent carries on; a JSON-RPC error would abort a
LangChain agent's whole run. Other requests (`initialize`, `tools/list`) get JSON-RPC errors.

## Provenance index

Each session keeps an in-memory index of distinctive values and where each came from, created at
registration and discarded when the session ends. It holds the session's trusted inputs (the
user's prompt), every value in every tool result, each with its server, tool, JSON-RPC request
ID and tool-call ID, and the URLs, emails and paths in tool descriptors, as `descriptor` values
attributed to the tool that published them. A response is indexed before it is forwarded, so any call the agent makes
after reading it is matched against it. One index serves all the session's servers, so a value
server A returned is recognized in a call to server B.

Before each call is forwarded, the index reports which indexed values its arguments contain. A
value the user also supplied is attributed to the user. The matches are recorded on the call's
`decision` event (`hindsight.provenance.matches`).

What is indexed is set by `--value-types` (`url`, `email`, `path`, `string`; all by default) and
`--min-value-length` (the shortest whole string indexed, default 8). Values are lowercased with
whitespace collapsed. The index itself is the `provenance` package, so the rule engine's replay can
rebuild it from a trace with identical results. It only matches values the model copies verbatim.

## Rules and enforcement

Before each tool call is forwarded, the session's rules are evaluated on the index's matches. A
call that any rule blocks is answered with a tool error result naming the rule, and never reaches
the server; the `decision` event records `block` and the rules that fired.

Rules are JSON files, one per rule, in `rules/` at the repo root (`--rules-dir` to change it), read
when each session is registered: a session runs under a fixed rule set, and a new rule applies from
the next session. A malformed rule file makes registration fail (HTTP 500, naming the file) rather
than being skipped. The rule format and the engine are the `rule-engine` package; its `replay()`
rebuilds a session's index from its proxy trace and reproduces the live decisions, which is why the
registration event records the session's rules and extraction settings.

## Proxy trace

Each session's trace is written to `<trace-dir>/<session-id>/proxy.jsonl` (default `traces/` at the
repo root), next to the agent's `agent.jsonl`. The event format is the `trace-schema` package's.
Events, numbered in order by `hindsight.seq`:

| Event          | Recorded when                                                                |
| -------------- | ---------------------------------------------------------------------------- |
| `registration` | the session is registered, with its trusted inputs                           |
| `descriptors`  | a `tools/list` response passes through, as the server sent it                |
| `call`         | a `tools/call` request passes through, as the agent sent it                  |
| `decision`     | right after each call: `allow` or `block`, the rules that fired, and the provenance matches |
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
uv run --package interposer interposer --help     # --config, --rules-dir, --upstream-timeout, ...
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
| `test_provenance.py`  | Phase 5: values traced across servers, trusted inputs winning, indexed in time |
| `test_enforcement.py` | Phase 6: rules blocking in-path, descriptor rules, replay matching live decisions |

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
