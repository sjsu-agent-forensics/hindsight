# files-server

Internal MCP tool server for the user's documents: a second internal source of confidential data,
and a way to chain through shared state (write a file in one step, read it back in another).

| tool                          | returns                                               |
| ----------------------------- | ----------------------------------------------------- |
| `list_files(prefix)`          | files under the prefix, sorted by path, no contents   |
| `read_file(path)`             | the file, including its contents                      |
| `write_file(path, content)`   | the file's summary; logged in the effect log          |
| `share_file(path, recipient)` | the file's summary; logged in the effect log          |

File contents are untrusted content; `share_file.recipient` and `write_file.path` are the
sensitive arguments. The plan's registry sketch calls the share argument `with`, but that is a
Python keyword, so it is `recipient` here. Paths are keys into the session's file list, never
real paths on disk, and `..` is rejected.

The benign files are `src/files_server/fixtures/benign.json`.

```bash
uv sync --project apps/mcp-servers/files                  # once
uv run --project apps/mcp-servers/files files-server      # MCP on 8014, control on 8114
uv run --project apps/mcp-servers/files pytest apps/mcp-servers/files
```
