# email-server

Internal MCP tool server for the user's mailbox (Alex Morgan, `alex.morgan@northwind.example`).

| tool                                 | returns                                                  |
| ------------------------------------ | -------------------------------------------------------- |
| `list_emails(folder, limit)`         | `inbox` or `sent`, newest first, without bodies          |
| `read_email(email_id)`               | the full email; marks it read                            |
| `search_emails(query)`               | up to 20 matches on subject, body or sender              |
| `send_email(to, subject, body, cc)`  | the new `email_id`; logged in the effect log             |

Subjects, bodies and sender names are untrusted content; `send_email.to` and `send_email.body`
are the sensitive arguments. Nothing is really sent: a sent email goes into the session's `sent`
folder and the effect log.

The benign mailbox is `src/email_server/fixtures/benign.json`. Its people match `records`
(Samuel Eriksen is `c_001`, and so on). A fixture can replace it or overlay it with
`"base": "benign"` plus `add` / `patch` (see `apps/mcp-servers/kit/README.md`).

```bash
uv sync --project apps/mcp-servers/email                  # once
uv run --project apps/mcp-servers/email email-server      # MCP on 8012, control on 8112
uv run --project apps/mcp-servers/email pytest apps/mcp-servers/email
```
