# agent

The agent hindsight protects: a ReAct agent built with LangChain's standard `create_agent()`, so
results apply to a typical agent rather than a custom loop. It gets every tool from MCP servers
reached through the interposer (`apps/interposer`), and has no in-process tools, since the
interposer cannot see them. It holds one persistent MCP session per server for the whole run.

Its servers are listed in [`servers.toml`](servers.toml), one entry per server, each pointing at
the interposer's `/mcp/<name>` path.

## Running it

Start the tool servers and the interposer first (see `apps/interposer/README.md`), then:

```bash
uv run --package agent agent                                    # chat; "exit" or "quit" to stop
uv run --package agent agent "What's the weather in San Jose?"  # answer one prompt and exit
uv run --package agent agent --provider anthropic --model claude-sonnet-5-5
```

`--provider` and `--model` select the chat model (see `hindsight.llm.get_llm`). Anthropic and
OpenAI read their API keys from `ANTHROPIC_API_KEY` and `OPENAI_API_KEY`.
