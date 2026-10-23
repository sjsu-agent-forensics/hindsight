"""Check that tools/list through the interposer matches tools/list sent directly.

Phase 1 exit check. Opens one MCP session direct and one through the interposer, in a
session registered for the check, sends the same initialize and tools/list messages at
the transport level, and compares the two raw tools/list results. They are compared as
received, not parsed into the SDK's Tool model, which would drop fields it does not
know. Key order counts; JSON whitespace does not, since each side serializes its own
messages.

Exits 0 when the results match and 1 with a diff when they do not.
"""

import argparse
import asyncio
import difflib
import json
import sys
from typing import Any

from _gateway_session import gateway_session
from anyio.streams.memory import MemoryObjectReceiveStream, MemoryObjectSendStream
from mcp.client.streamable_http import streamable_http_client
from mcp.shared.message import SessionMessage
from mcp.types import (
    LATEST_PROTOCOL_VERSION,
    JSONRPCError,
    JSONRPCMessage,
    JSONRPCNotification,
    JSONRPCRequest,
    JSONRPCResponse,
)


async def _list_tools_raw(url: str) -> dict[str, Any]:
    async with streamable_http_client(url) as (read, write, _):
        await _request(
            read,
            write,
            1,
            "initialize",
            {
                "protocolVersion": LATEST_PROTOCOL_VERSION,
                "capabilities": {},
                "clientInfo": {"name": "check-descriptors", "version": "0.1.0"},
            },
        )
        initialized = JSONRPCNotification(
            jsonrpc="2.0", method="notifications/initialized"
        )
        await write.send(SessionMessage(message=JSONRPCMessage(initialized)))
        return await _request(read, write, 2, "tools/list", {})


async def _request(
    read: MemoryObjectReceiveStream[SessionMessage | Exception],
    write: MemoryObjectSendStream[SessionMessage],
    request_id: int,
    method: str,
    params: dict[str, Any],
) -> dict[str, Any]:
    request = JSONRPCRequest(jsonrpc="2.0", id=request_id, method=method, params=params)
    await write.send(SessionMessage(message=JSONRPCMessage(request)))
    async for item in read:
        if isinstance(item, Exception):
            raise item
        reply = item.message.root
        if isinstance(reply, JSONRPCResponse) and reply.id == request_id:
            return reply.result
        if isinstance(reply, JSONRPCError) and reply.id == request_id:
            raise RuntimeError(f"{method} failed: {reply.error.message}")
    raise RuntimeError(f"Session closed before {method} was answered")


async def _run(direct_url: str, gateway_url: str, server: str) -> int:
    direct = json.dumps(await _list_tools_raw(direct_url), indent=2, ensure_ascii=False)
    async with gateway_session(gateway_url, server) as proxy_url:
        proxied = json.dumps(
            await _list_tools_raw(proxy_url), indent=2, ensure_ascii=False
        )
    if direct == proxied:
        count = len(json.loads(direct).get("tools", []))
        print(f"OK: tools/list matches ({count} tools)")
        return 0
    print("MISMATCH: tools/list through the interposer differs from direct")
    sys.stdout.writelines(
        difflib.unified_diff(
            direct.splitlines(keepends=True),
            proxied.splitlines(keepends=True),
            fromfile="direct",
            tofile="interposer",
        )
    )
    return 1


def main() -> None:
    """Compare tools/list from a server's direct URL and through the interposer."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("direct_url", help="the upstream server's own URL")
    parser.add_argument("gateway_url", help="the interposer's base URL")
    parser.add_argument("server", help="the server's name in the interposer config")
    args = parser.parse_args()
    sys.exit(asyncio.run(_run(args.direct_url, args.gateway_url, args.server)))


if __name__ == "__main__":
    main()
