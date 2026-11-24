"""Log of traffic the gateway refused.

Refused requests (no session, an unknown or ended session, an unknown server or MCP
session) are written here, one JSON object per line, and never to a session's trace:
they belong to no registered session, so they must not enter analysis. The request
content is recorded as received and is untrusted.
"""

import json
from datetime import datetime, timezone
from pathlib import Path

import anyio
from starlette.requests import Request
from starlette.responses import JSONResponse
from mcp.types import INVALID_REQUEST

# Enough to keep a JSON-RPC request whole, without letting one refused request fill the
# disk.
_MAX_BODY_BYTES = 64 * 1024


class RefusedLog:
    """Append-only JSON Lines file of refused requests."""

    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self._path = path
        self._lock = anyio.Lock()

    async def refuse(
        self, request: Request, status: int, reason: str, detail: str
    ) -> JSONResponse:
        """Record a refused request and return the HTTP error to send for it.

        ``reason`` is a short machine-readable code; ``detail`` is the message the
        client also receives. The body is read, so the request must not be served
        afterwards.
        """
        body = await request.body() if request.method == "POST" else b""
        entry = {
            "time": datetime.now(timezone.utc).isoformat(),
            "reason": reason,
            "detail": detail,
            "status": status,
            "method": request.method,
            "path": request.url.path,
            "mcp_session_id": request.headers.get("mcp-session-id"),
            "client": request.client.host if request.client else None,
            "body": body[:_MAX_BODY_BYTES].decode("utf-8", errors="replace"),
            "body_truncated": len(body) > _MAX_BODY_BYTES,
        }
        line = json.dumps(entry, ensure_ascii=False) + "\n"
        async with self._lock:
            await anyio.to_thread.run_sync(self._append, line)
        return JSONResponse(
            {
                "jsonrpc": "2.0",
                "id": None,
                "error": {"code": INVALID_REQUEST, "message": detail},
            },
            status_code=status,
        )

    def _append(self, line: str) -> None:
        with self._path.open("a", encoding="utf-8") as file:
            file.write(line)
