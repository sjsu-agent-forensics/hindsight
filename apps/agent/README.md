# agent

The agent hindsight protects: a ReAct agent built with LangChain's standard `create_agent()`, so
results apply to a typical agent rather than a custom loop. It gets every tool from MCP servers
reached through the interposer (`apps/interposer`), and has no in-process tools, since the
interposer cannot see them. It holds one persistent MCP session per server for the whole run.

Its servers are listed in [`servers.toml`](servers.toml), one entry per server, each pointing at
the interposer's `/s/{session_id}/mcp/<name>` path. The launcher (`apps/launcher`) registers a
session and gives the agent a copy of this file with `{session_id}` filled in.

## Telemetry

The agent's only coupling to hindsight is its telemetry (`agent.telemetry`); enforcement lives in
the interposer.

- **Join key.** Every `tools/call` request carries the agent's tool-call ID in `params._meta`
  under `hindsight/tool_call_id`, which the interposer records on its trace. The LangChain MCP
  adapter cannot set `_meta` itself, so an adapter interceptor and a wrapper around the session's
  `call_tool` add it.
- **Agent trace.** With `--session-id` and `--trace-file` (the launcher passes both), a
  `create_agent` middleware writes `user_turn`, `llm_step`, `tool_call` and `tool_result` events in
  the `trace-schema` format. Each `tool_call` lists the tool results the model had seen. Model
  reasoning is not recorded.

## Running it

Start the tool servers and the interposer first (see `apps/interposer/README.md`), then run the
agent through the launcher, which passes on any arguments it does not recognize:

```bash
uv run --package launcher launcher "What's the weather in San Jose?"
uv run --package launcher launcher "What's the weather in San Jose?" --provider anthropic --model claude-sonnet-5-5
```

`--provider` and `--model` select the chat model (see `hindsight.llm.get_llm`). Anthropic and
OpenAI read their API keys from `ANTHROPIC_API_KEY` and `OPENAI_API_KEY`.

Run directly (`uv run --package agent agent`), the agent uses the unfilled template, so the
interposer refuses it at startup. Its chat mode (no prompt argument) works only with a
`--config` whose URLs name a registered session.
