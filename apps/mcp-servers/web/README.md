# web-server

Internal MCP tool server for web pages, served from a local corpus so that runs never touch the
internet: live pages change between runs, and real requests would leave traces outside the test.

| tool              | returns                                                              |
| ----------------- | -------------------------------------------------------------------- |
| `fetch_page(url)` | the page's `page_id`, title and text, or `status: 404`; always logged |
| `search(query)`   | up to 10 results with URL, title and snippet                         |

Page text is untrusted content. `fetch_page.url` is the sensitive argument, since a URL can carry
data in its query string, so every fetch goes into the effect log with its full URL, even
for pages that don't exist. A known page fetched with an extra query string still resolves.

The benign corpus is `src/web_server/fixtures/benign.json`.

```bash
uv sync --project apps/mcp-servers/web                # once
uv run --project apps/mcp-servers/web web-server      # MCP on 8013, control on 8113
uv run --project apps/mcp-servers/web pytest apps/mcp-servers/web
```
