"""Mock stdio MCP tool server for public tool testing and CI.

Speaks JSON-RPC 2.0 over stdin/stdout without external dependencies, providing reference
public tools (fetch, filesystem operations, and query execution) for local tests.
"""

import json
import os
from pathlib import Path
import sys
from typing import Any


def _tools_list() -> list[dict[str, Any]]:
    """Return the list of tool descriptors exposed by this mock server."""
    return [
        {
            "name": "fetch",
            "description": "Fetch content from a simulated public URL.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "url": {
                        "type": "string",
                        "description": "URL to fetch content from.",
                    }
                },
                "required": ["url"],
            },
        },
        {
            "name": "read_file",
            "description": "Read content from a file in the sandbox directory.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Relative path to the file.",
                    }
                },
                "required": ["path"],
            },
        },
        {
            "name": "write_file",
            "description": "Write content to a file in the sandbox directory.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Relative path to the file.",
                    },
                    "content": {
                        "type": "string",
                        "description": "Text content to write.",
                    },
                },
                "required": ["path", "content"],
            },
        },
        {
            "name": "execute_query",
            "description": "Execute a simulated SQL query.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "sql": {
                        "type": "string",
                        "description": "SQL statement to run.",
                    }
                },
                "required": ["sql"],
            },
        },
    ]


def _call_tool(name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    """Execute a tool call against this mock server's local state.

    Args:
        name: Tool name to execute.
        arguments: Keyword arguments passed to the tool.

    Returns:
        MCP content dictionary with text payload.

    Raises:
        ValueError: Unknown tool name.
    """
    if name == "fetch":
        url = str(arguments.get("url", ""))
        return {
            "content": [
                {
                    "type": "text",
                    "text": json.dumps(
                        {
                            "status": 200,
                            "url": url,
                            "body": f"Simulated content fetched from {url}",
                        }
                    ),
                }
            ]
        }
    if name == "read_file":
        target = Path(arguments.get("path", "")).resolve()
        if not target.exists():
            return {
                "isError": True,
                "content": [{"type": "text", "text": f"File not found: {target.name}"}],
            }
        return {
            "content": [{"type": "text", "text": target.read_text(encoding="utf-8")}]
        }
    if name == "write_file":
        target = Path(arguments.get("path", "")).resolve()
        target.parent.mkdir(parents=True, exist_ok=True)
        content = str(arguments.get("content", ""))
        target.write_text(content, encoding="utf-8")
        return {
            "content": [
                {
                    "type": "text",
                    "text": json.dumps({"written": str(target), "bytes": len(content)}),
                }
            ]
        }
    if name == "execute_query":
        sql = str(arguments.get("sql", ""))
        return {
            "content": [
                {
                    "type": "text",
                    "text": json.dumps(
                        {
                            "query": sql,
                            "rows": [{"id": 1, "value": "mock_result"}],
                            "rowcount": 1,
                        }
                    ),
                }
            ]
        }
    raise ValueError(f"Unknown tool: {name}")


def _handle_request(request: dict[str, Any]) -> dict[str, Any] | None:
    """Handle a single incoming JSON-RPC request and return the response."""
    method = request.get("method")
    req_id = request.get("id")

    # Notifications carry no id and expect no response.
    if req_id is None:
        return None

    if method == "initialize":
        return {
            "jsonrpc": "2.0",
            "id": req_id,
            "result": {
                "protocolVersion": "2024-11-05",
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "mock-public-server", "version": "0.1.0"},
            },
        }

    if method == "ping":
        return {"jsonrpc": "2.0", "id": req_id, "result": {}}

    if method == "tools/list":
        return {
            "jsonrpc": "2.0",
            "id": req_id,
            "result": {"tools": _tools_list()},
        }

    if method == "tools/call":
        params = request.get("params", {})
        tool_name = params.get("name", "")
        tool_args = params.get("arguments", {})
        try:
            res = _call_tool(tool_name, tool_args)
            return {"jsonrpc": "2.0", "id": req_id, "result": res}
        except Exception as exc:
            return {
                "jsonrpc": "2.0",
                "id": req_id,
                "error": {"code": -32603, "message": str(exc)},
            }

    return {
        "jsonrpc": "2.0",
        "id": req_id,
        "error": {"code": -32601, "message": f"Method {method} not found"},
    }


def main() -> None:
    """Run the mock stdio server event loop reading lines from stdin."""
    # Ensure stdout is unbuffered for stdio communication.
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
        except json.JSONDecodeError:
            continue
        resp = _handle_request(req)
        if resp is not None:
            sys.stdout.write(json.dumps(resp) + "\n")
            sys.stdout.flush()


if __name__ == "__main__":
    main()
