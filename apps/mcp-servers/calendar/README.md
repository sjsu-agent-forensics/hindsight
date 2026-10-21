# calendar-server

Internal MCP tool server for the user's calendar: another source of untrusted text (event titles
and notes) and another sink (inviting someone shares an event's notes with them).

| tool                                                          | returns                                        |
| ------------------------------------------------------------- | ---------------------------------------------- |
| `list_events(start, end)`                                     | events overlapping the range, earliest first   |
| `create_event(title, start, end, participants, location, notes)` | the new event; logged in the effect log     |

`create_event.participants` is the sensitive argument. Times are ISO 8601 with a UTC offset. With
no range, `list_events` covers today through the next 7 days, where "today" is the session's
scenario time, so a rerun lists the same events.

Note that the module is `calendar_server`, not `calendar`, which would shadow Python's own
`calendar` module.

The benign calendar is `src/calendar_server/fixtures/benign.json`.

```bash
uv sync --project apps/mcp-servers/calendar                     # once
uv run --project apps/mcp-servers/calendar calendar-server      # MCP on 8017, control on 8117
uv run --project apps/mcp-servers/calendar pytest apps/mcp-servers/calendar
```
