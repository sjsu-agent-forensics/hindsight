# messaging-server

Internal MCP tool server for team chat: channels and direct messages.

| tool                            | returns                                                 |
| ------------------------------- | ------------------------------------------------------- |
| `read_channel(channel, limit)`  | the latest messages, oldest first, with sender names    |
| `post_message(channel, text)`   | the new `message_id`; logged in the effect log          |
| `send_dm(user, text)`           | the new `message_id`; logged in the effect log          |

Messages are untrusted content; `post_message.channel` and `send_dm.user` are the sensitive
arguments. The workspace has three internal channels plus `ext-ergo-supplies`, which is shared
with a supplier, and one external user (`dana.ergo`). Effects carry `external: true` when a
message leaves the organisation that way.

The benign workspace is `src/messaging_server/fixtures/benign.json`.

```bash
uv sync --project apps/mcp-servers/messaging                      # once
uv run --project apps/mcp-servers/messaging messaging-server      # MCP on 8016, control on 8116
uv run --project apps/mcp-servers/messaging pytest apps/mcp-servers/messaging
```
