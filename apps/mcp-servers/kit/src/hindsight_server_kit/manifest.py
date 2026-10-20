"""Export a server's `tools/list` with a hash of each descriptor, for the interposer's pin check."""

import hashlib
import json
from typing import Any

from fastmcp import FastMCP


async def manifest(server: FastMCP) -> dict[str, Any]:
    """Return every tool's MCP descriptor and the SHA-256 of its canonical JSON form.

    Canonical means keys sorted, no whitespace, None fields dropped, so the hash changes only when
    a tool's name, description or schema does. Registry labels (sources, sinks, reads, writes) are
    not here yet; they arrive with each server's `registry.toml`.
    """
    tools = []
    for tool in await server.list_tools():
        descriptor = tool.to_mcp_tool().model_dump(mode="json", by_alias=True, exclude_none=True)
        canonical = json.dumps(descriptor, sort_keys=True, separators=(",", ":"))
        tools.append(
            {
                "name": descriptor["name"],
                "descriptor_sha256": hashlib.sha256(canonical.encode("utf-8")).hexdigest(),
                "descriptor": descriptor,
            }
        )
    return {"server": server.name, "tools": sorted(tools, key=lambda t: t["name"])}
