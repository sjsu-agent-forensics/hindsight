"""Mock stdio MCP tool server simulating a personal workspace for testing and CI.

Speaks JSON-RPC 2.0 over stdin/stdout without external dependencies, providing reference
personal workspace tools (Google Drive, Calendar, and Gmail operations) for local tests.
"""

import json
import os
from pathlib import Path
import sys
from typing import Any


# ---------------------------------------------------------------------------
# Simulated workspace data
# ---------------------------------------------------------------------------

_MOCK_DRIVE_FILES: list[dict[str, Any]] = [
    {
        "id": "doc_001",
        "name": "Q4 Planning Notes",
        "mimeType": "application/vnd.google-apps.document",
        "modifiedTime": "2026-11-10T09:15:00Z",
        "owners": [{"displayName": "Alice Smith", "emailAddress": "alice@example.com"}],
    },
    {
        "id": "sheet_002",
        "name": "Budget 2026",
        "mimeType": "application/vnd.google-apps.spreadsheet",
        "modifiedTime": "2026-11-08T14:30:00Z",
        "owners": [{"displayName": "Alice Smith", "emailAddress": "alice@example.com"}],
    },
    {
        "id": "doc_003",
        "name": "Meeting Minutes - Nov 5",
        "mimeType": "application/vnd.google-apps.document",
        "modifiedTime": "2026-11-05T16:45:00Z",
        "owners": [{"displayName": "Bob Lee", "emailAddress": "bob@example.com"}],
    },
    {
        "id": "slide_004",
        "name": "Product Roadmap Deck",
        "mimeType": "application/vnd.google-apps.presentation",
        "modifiedTime": "2026-11-01T11:00:00Z",
        "owners": [{"displayName": "Alice Smith", "emailAddress": "alice@example.com"}],
    },
]

_MOCK_CALENDAR_EVENTS: list[dict[str, Any]] = [
    {
        "id": "evt_001",
        "summary": "Team Standup",
        "start": {"dateTime": "2026-11-12T09:00:00-08:00"},
        "end": {"dateTime": "2026-11-12T09:30:00-08:00"},
        "attendees": [
            {"email": "alice@example.com"},
            {"email": "bob@example.com"},
        ],
    },
    {
        "id": "evt_002",
        "summary": "1:1 with Manager",
        "start": {"dateTime": "2026-11-12T14:00:00-08:00"},
        "end": {"dateTime": "2026-11-12T14:30:00-08:00"},
        "attendees": [{"email": "manager@example.com"}],
    },
    {
        "id": "evt_003",
        "summary": "Project Review",
        "start": {"dateTime": "2026-11-13T10:00:00-08:00"},
        "end": {"dateTime": "2026-11-13T11:00:00-08:00"},
        "attendees": [
            {"email": "alice@example.com"},
            {"email": "charlie@example.com"},
        ],
    },
]

_MOCK_EMAILS: list[dict[str, Any]] = [
    {
        "id": "msg_001",
        "threadId": "thread_001",
        "from": "bob@example.com",
        "to": "alice@example.com",
        "subject": "Re: Q4 Planning",
        "snippet": "Looks good, I've added my comments to the doc...",
        "body": (
            "Hi Alice,\n\nLooks good, I've added my comments to the Q4 Planning "
            "Notes doc. Can we discuss the timeline in tomorrow's standup?\n\nBest,\nBob"
        ),
        "date": "2026-11-11T10:30:00Z",
        "labels": ["INBOX", "UNREAD"],
    },
    {
        "id": "msg_002",
        "threadId": "thread_002",
        "from": "calendar@google.com",
        "to": "alice@example.com",
        "subject": "Invitation: Project Review @ Nov 13 10:00am",
        "snippet": "Charlie has invited you to Project Review...",
        "body": "You have been invited to Project Review on November 13, 2026 at 10:00 AM.",
        "date": "2026-11-10T08:00:00Z",
        "labels": ["INBOX"],
    },
    {
        "id": "msg_003",
        "threadId": "thread_003",
        "from": "alice@example.com",
        "to": "team@example.com",
        "subject": "Budget review — action items",
        "snippet": "Following up on the budget review, here are the action items...",
        "body": (
            "Team,\n\nFollowing up on the budget review, here are the action items:\n"
            "1. Update department forecasts by Nov 15\n"
            "2. Submit travel requests for Q1\n"
            "3. Review vendor contracts\n\nThanks,\nAlice"
        ),
        "date": "2026-11-09T15:00:00Z",
        "labels": ["SENT"],
    },
]


# ---------------------------------------------------------------------------
# Tool descriptors
# ---------------------------------------------------------------------------


def _tools_list() -> list[dict[str, Any]]:
    """Return the list of tool descriptors exposed by this mock server."""
    return [
        # --- Google Drive tools ---
        {
            "name": "gdrive_search",
            "description": "Search for files in Google Drive by name or content query.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Search query (file name, content, or Drive query syntax).",
                    }
                },
                "required": ["query"],
            },
        },
        {
            "name": "gdrive_read_file",
            "description": (
                "Read the content of a Google Drive file by its ID. "
                "Docs are returned as Markdown, Sheets as CSV, Slides as plain text."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "file_id": {
                        "type": "string",
                        "description": "The Google Drive file ID.",
                    }
                },
                "required": ["file_id"],
            },
        },
        {
            "name": "gdrive_list_files",
            "description": "List recent files in Google Drive, optionally filtered by MIME type.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "mime_type": {
                        "type": "string",
                        "description": "Optional MIME type filter (e.g. application/vnd.google-apps.document).",
                    },
                    "max_results": {
                        "type": "integer",
                        "description": "Maximum number of files to return (default 10).",
                    },
                },
            },
        },
        # --- Google Calendar tools ---
        {
            "name": "gcalendar_list_events",
            "description": "List upcoming calendar events, optionally filtered by date range.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "time_min": {
                        "type": "string",
                        "description": "Start of date range (ISO 8601). Defaults to now.",
                    },
                    "time_max": {
                        "type": "string",
                        "description": "End of date range (ISO 8601).",
                    },
                    "max_results": {
                        "type": "integer",
                        "description": "Maximum number of events to return (default 10).",
                    },
                },
            },
        },
        {
            "name": "gcalendar_create_event",
            "description": "Create a new calendar event.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "summary": {
                        "type": "string",
                        "description": "Event title.",
                    },
                    "start": {
                        "type": "string",
                        "description": "Start datetime (ISO 8601).",
                    },
                    "end": {
                        "type": "string",
                        "description": "End datetime (ISO 8601).",
                    },
                    "attendees": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "List of attendee email addresses.",
                    },
                    "description": {
                        "type": "string",
                        "description": "Optional event description.",
                    },
                },
                "required": ["summary", "start", "end"],
            },
        },
        # --- Gmail tools ---
        {
            "name": "gmail_search",
            "description": "Search emails using Gmail query syntax (from:, subject:, has:attachment, etc.).",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Gmail search query string.",
                    },
                    "max_results": {
                        "type": "integer",
                        "description": "Maximum number of messages to return (default 10).",
                    },
                },
                "required": ["query"],
            },
        },
        {
            "name": "gmail_read",
            "description": "Read the full content of an email by its message ID.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "message_id": {
                        "type": "string",
                        "description": "The Gmail message ID.",
                    }
                },
                "required": ["message_id"],
            },
        },
        {
            "name": "gmail_draft",
            "description": "Create an email draft.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "to": {
                        "type": "string",
                        "description": "Recipient email address.",
                    },
                    "subject": {
                        "type": "string",
                        "description": "Email subject line.",
                    },
                    "body": {
                        "type": "string",
                        "description": "Email body text.",
                    },
                },
                "required": ["to", "subject", "body"],
            },
        },
    ]


# ---------------------------------------------------------------------------
# Tool implementations
# ---------------------------------------------------------------------------


def _call_tool(name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    """Execute a tool call against this mock server's simulated workspace data.

    Args:
        name: Tool name to execute.
        arguments: Keyword arguments passed to the tool.

    Returns:
        MCP content dictionary with text payload.

    Raises:
        ValueError: Unknown tool name.
    """
    if name == "gdrive_search":
        return _handle_gdrive_search(arguments)
    if name == "gdrive_read_file":
        return _handle_gdrive_read_file(arguments)
    if name == "gdrive_list_files":
        return _handle_gdrive_list_files(arguments)
    if name == "gcalendar_list_events":
        return _handle_gcalendar_list_events(arguments)
    if name == "gcalendar_create_event":
        return _handle_gcalendar_create_event(arguments)
    if name == "gmail_search":
        return _handle_gmail_search(arguments)
    if name == "gmail_read":
        return _handle_gmail_read(arguments)
    if name == "gmail_draft":
        return _handle_gmail_draft(arguments)
    raise ValueError(f"Unknown tool: {name}")


def _handle_gdrive_search(arguments: dict[str, Any]) -> dict[str, Any]:
    """Search mock Drive files by query."""
    query = str(arguments.get("query", "")).lower()
    matches = [
        f for f in _MOCK_DRIVE_FILES if query in f["name"].lower()
    ]
    return {
        "content": [
            {
                "type": "text",
                "text": json.dumps(
                    {"query": query, "files": matches, "totalFiles": len(matches)}
                ),
            }
        ]
    }


def _handle_gdrive_read_file(arguments: dict[str, Any]) -> dict[str, Any]:
    """Read content of a mock Drive file by ID."""
    file_id = str(arguments.get("file_id", ""))
    file_meta = next((f for f in _MOCK_DRIVE_FILES if f["id"] == file_id), None)
    if file_meta is None:
        return {
            "isError": True,
            "content": [{"type": "text", "text": f"File not found: {file_id}"}],
        }
    # Simulate content based on file type.
    mime = file_meta["mimeType"]
    if "document" in mime:
        content = f"# {file_meta['name']}\n\nSimulated document content for {file_meta['name']}."
    elif "spreadsheet" in mime:
        content = "Category,Amount,Status\nEngineering,150000,Approved\nMarketing,80000,Pending"
    elif "presentation" in mime:
        content = f"Slide 1: {file_meta['name']}\nSlide 2: Overview\nSlide 3: Details"
    else:
        content = f"Binary content of {file_meta['name']}"
    return {"content": [{"type": "text", "text": content}]}


def _handle_gdrive_list_files(arguments: dict[str, Any]) -> dict[str, Any]:
    """List mock Drive files, optionally filtered by MIME type."""
    mime_filter = arguments.get("mime_type")
    max_results = int(arguments.get("max_results", 10))
    files = _MOCK_DRIVE_FILES
    if mime_filter:
        files = [f for f in files if f["mimeType"] == mime_filter]
    files = files[:max_results]
    return {
        "content": [
            {"type": "text", "text": json.dumps({"files": files, "totalFiles": len(files)})}
        ]
    }


def _handle_gcalendar_list_events(arguments: dict[str, Any]) -> dict[str, Any]:
    """List mock calendar events."""
    max_results = int(arguments.get("max_results", 10))
    events = _MOCK_CALENDAR_EVENTS[:max_results]
    return {
        "content": [
            {
                "type": "text",
                "text": json.dumps({"events": events, "totalEvents": len(events)}),
            }
        ]
    }


def _handle_gcalendar_create_event(arguments: dict[str, Any]) -> dict[str, Any]:
    """Simulate creating a calendar event."""
    event = {
        "id": f"evt_{len(_MOCK_CALENDAR_EVENTS) + 1:03d}",
        "summary": arguments.get("summary", ""),
        "start": {"dateTime": arguments.get("start", "")},
        "end": {"dateTime": arguments.get("end", "")},
        "attendees": [
            {"email": e} for e in arguments.get("attendees", [])
        ],
        "status": "confirmed",
    }
    return {
        "content": [
            {
                "type": "text",
                "text": json.dumps({"created": True, "event": event}),
            }
        ]
    }


def _handle_gmail_search(arguments: dict[str, Any]) -> dict[str, Any]:
    """Search mock emails by query."""
    query = str(arguments.get("query", "")).lower()
    max_results = int(arguments.get("max_results", 10))
    matches = []
    for msg in _MOCK_EMAILS:
        if (
            query in msg["subject"].lower()
            or query in msg["snippet"].lower()
            or query in msg["from"].lower()
            or query in msg["to"].lower()
        ):
            matches.append(
                {
                    "id": msg["id"],
                    "threadId": msg["threadId"],
                    "from": msg["from"],
                    "subject": msg["subject"],
                    "snippet": msg["snippet"],
                    "date": msg["date"],
                }
            )
    matches = matches[:max_results]
    return {
        "content": [
            {
                "type": "text",
                "text": json.dumps(
                    {"query": query, "messages": matches, "totalMessages": len(matches)}
                ),
            }
        ]
    }


def _handle_gmail_read(arguments: dict[str, Any]) -> dict[str, Any]:
    """Read a mock email by message ID."""
    message_id = str(arguments.get("message_id", ""))
    msg = next((m for m in _MOCK_EMAILS if m["id"] == message_id), None)
    if msg is None:
        return {
            "isError": True,
            "content": [{"type": "text", "text": f"Message not found: {message_id}"}],
        }
    return {"content": [{"type": "text", "text": json.dumps(msg)}]}


def _handle_gmail_draft(arguments: dict[str, Any]) -> dict[str, Any]:
    """Simulate creating an email draft."""
    draft = {
        "id": f"draft_{len(_MOCK_EMAILS) + 1:03d}",
        "to": arguments.get("to", ""),
        "subject": arguments.get("subject", ""),
        "body": arguments.get("body", ""),
        "status": "draft",
    }
    return {
        "content": [
            {
                "type": "text",
                "text": json.dumps({"created": True, "draft": draft}),
            }
        ]
    }


# ---------------------------------------------------------------------------
# JSON-RPC 2.0 handler
# ---------------------------------------------------------------------------


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
                "serverInfo": {"name": "mock-workspace-server", "version": "0.1.0"},
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
