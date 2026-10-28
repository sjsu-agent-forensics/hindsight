"""Unit tests for tool descriptor fingerprinting and manifest generation."""

from public_bridge.config import StdioPublicServerConfig
from public_bridge.manifest import (
    build_manifest,
    canonical_descriptor_hash,
    toml_interposer_snippet,
)


def test_canonical_descriptor_hash() -> None:
    """Ensure descriptor hash is deterministic regardless of key order."""
    desc1 = {"name": "test", "description": "desc", "inputSchema": {"type": "object"}}
    desc2 = {"inputSchema": {"type": "object"}, "name": "test", "description": "desc"}
    assert canonical_descriptor_hash(desc1) == canonical_descriptor_hash(desc2)


def test_build_manifest() -> None:
    """Test building manifest structure with SHA-256 digests."""
    cfg = StdioPublicServerConfig(
        name="test_srv",
        command="python",
        port=8021,
        control_port=8121,
    )
    raw_tools = [
        {"name": "tool_a", "description": "A", "inputSchema": {"type": "object"}},
        {"name": "tool_b", "description": "B", "inputSchema": {"type": "object"}},
    ]
    manifest = build_manifest(cfg, raw_tools)
    assert manifest["server"] == "test_srv"
    assert manifest["tools_count"] == 2
    assert "sha256" in manifest["tools"][0]
    assert manifest["mcp_url"] == "http://127.0.0.1:8021/api/mcp"
    assert manifest["control_url"] == "http://127.0.0.1:8121/control"


def test_toml_interposer_snippet() -> None:
    """Verify generated TOML snippet format."""
    cfg = StdioPublicServerConfig(
        name="my_tool",
        command="python",
        port=8025,
    )
    snippet = toml_interposer_snippet(cfg)
    assert "[servers.my_tool]" in snippet
    assert 'url = "http://127.0.0.1:8025/api/mcp"' in snippet
