"""Tool discovery, canonical schema fingerprinting, and manifest export for public tools.

Allows inspectable verification of third-party public MCP tools, generating SHA-256 digests
of tool descriptors for interposer pinning and configuration generation.
"""

from collections.abc import Sequence
import hashlib
import json
from typing import Any

from public_bridge.config import PublicServerConfig


def canonical_descriptor_hash(descriptor: dict[str, Any]) -> str:
    """Compute deterministic SHA-256 hex digest of a canonicalized tool descriptor."""
    canonical = json.dumps(descriptor, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def build_manifest(
    config: PublicServerConfig, raw_tools: Sequence[dict[str, Any]]
) -> dict[str, Any]:
    """Build a manifest record containing tools and their SHA-256 descriptor digests.

    Args:
        config: Configuration of the public MCP server.
        raw_tools: Raw tool list returned from tools/list.

    Returns:
        Structured manifest dictionary.
    """
    allowed = set(config.allowed_tools) if config.allowed_tools else None
    tools_output = []

    for tool in raw_tools:
        name = tool.get("name", "")
        if allowed is not None and name not in allowed:
            continue
        clean_tool = {
            "name": name,
            "description": tool.get("description", ""),
            "inputSchema": tool.get("inputSchema", {}),
        }
        digest = canonical_descriptor_hash(clean_tool)
        tools_output.append({**clean_tool, "sha256": digest})

    return {
        "server": config.name,
        "transport": config.transport,
        "mcp_url": f"http://{config.host}:{config.port}/api/mcp",
        "control_url": f"http://{config.host}:{config.control_port}/control",
        "tools_count": len(tools_output),
        "tools": tools_output,
    }


def toml_interposer_snippet(config: PublicServerConfig) -> str:
    """Generate TOML configuration snippet for hindsight interposer's servers.toml."""
    return (
        f"# Public MCP Server: {config.name}\n"
        f"[servers.{config.name}]\n"
        f'url = "http://{config.host}:{config.port}/api/mcp"\n'
    )
