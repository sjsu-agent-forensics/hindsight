"""Unit tests for public MCP server configuration parsing and validation."""

import os
from pathlib import Path
import pytest

from public_bridge.config import StdioPublicServerConfig, load_config


def test_load_config_valid(tmp_path: Path) -> None:
    """Test parsing a valid public servers TOML configuration."""
    cfg_file = tmp_path / "public_servers.toml"
    cfg_file.write_text("""
[public_servers.test_stdio]
transport = "stdio"
command = "python"
args = ["-m", "test_module"]
port = 8031
control_port = 8131
host = "127.0.0.1"
sandbox_mode = "ephemeral_dir"
""")
    servers = load_config(cfg_file)
    assert "test_stdio" in servers
    s = servers["test_stdio"]
    assert isinstance(s, StdioPublicServerConfig)
    assert s.command == "python"
    assert s.args == ["-m", "test_module"]
    assert s.port == 8031
    assert s.control_port == 8131
    assert s.host == "127.0.0.1"
    assert s.sandbox_mode == "ephemeral_dir"


def test_env_var_expansion(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Test expanding environment variables in server config fields."""
    monkeypatch.setenv("TEST_TOKEN", "secret123")
    cfg_file = tmp_path / "public_servers.toml"
    cfg_file.write_text("""
[public_servers.github_test]
command = "npx"
args = ["-y", "@mcp/github"]
env = { GITHUB_TOKEN = "${TEST_TOKEN}" }
port = 8035
""")
    servers = load_config(cfg_file)
    s = servers["github_test"]
    assert isinstance(s, StdioPublicServerConfig)
    assert s.env["GITHUB_TOKEN"] == "secret123"


def test_reject_non_loopback(tmp_path: Path) -> None:
    """Reject configurations with non-loopback host (N-06)."""
    cfg_file = tmp_path / "public_servers.toml"
    cfg_file.write_text("""
[public_servers.bad_host]
command = "python"
port = 8031
host = "0.0.0.0"
""")
    with pytest.raises(ValueError, match="must be '127.0.0.1'"):
        load_config(cfg_file)


def test_reject_port_collision(tmp_path: Path) -> None:
    """Reject configuration where multiple servers claim the same port."""
    cfg_file = tmp_path / "public_servers.toml"
    cfg_file.write_text("""
[public_servers.server1]
command = "python"
port = 8040
control_port = 8140

[public_servers.server2]
command = "python"
port = 8040
control_port = 8141
""")
    with pytest.raises(ValueError, match="Port 8040 collision"):
        load_config(cfg_file)


def test_missing_config_file() -> None:
    """Raise FileNotFoundError when config file is missing."""
    with pytest.raises(FileNotFoundError):
        load_config(Path("/nonexistent/public_servers.toml"))
