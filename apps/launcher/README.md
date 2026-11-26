# launcher

Runs the agent (`apps/agent`) once under a hindsight session:

1. Generates a session ID and registers it with the interposer (`POST /sessions`), with the
   user's prompt as the only trusted input. The interposer answers with the session's trace
   directory.
2. Runs the agent on the prompt, with a copy of the agent's `servers.toml` whose `{session_id}`
   placeholders are replaced by the session ID, so every MCP URL is the session's path. The agent
   writes its trace to `agent.jsonl` in the trace directory, beside the interposer's `proxy.jsonl`.
3. Ends the session (`DELETE /sessions/<id>`) however the agent exits, then prints the MCP sessions
   the agent opened and the trace directory. Both traces are complete by then.

## Running it

Start the tool servers and the interposer first (see `apps/interposer/README.md`), then:

```bash
uv run --package launcher launcher "What's the weather in San Jose?"
uv run --package launcher launcher "What's the weather in San Jose?" --provider anthropic
```

Arguments the launcher does not recognize are passed to the agent. `--gateway` sets the
interposer's URL (default `http://127.0.0.1:8000`) and `--agent-config` the template config.

One run answers one prompt. The agent's chat mode is not available under a session yet: each
later turn would have to be registered as a trusted input, which the design defers.
