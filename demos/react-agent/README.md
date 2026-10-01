# react-agent

Interactive ReAct agent built as a LangGraph state graph: a generator node calls the model, and a
tool node runs any tool calls it requests, looping until the model answers. Its one tool,
`get_local_datetime`, runs in-process rather than over MCP.

From the repo root:

```bash
uv run --package react-agent react-agent     # type "exit" or "quit" to stop
```
