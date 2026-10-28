"""Public MCP tools bridge framework for hindsight.

Connects third-party public MCP tools (stdio and HTTP) to hindsight over loopback Streamable
HTTP and provides resettable session sandboxing and control endpoints (E-10, N-06).
"""

from public_bridge.bridge import PublicBridgeServer
from public_bridge.config import (
    HttpPublicServerConfig,
    PublicServerConfig,
    StdioPublicServerConfig,
    load_config,
)
from public_bridge.control import control_app
from public_bridge.manifest import (
    build_manifest,
    canonical_descriptor_hash,
    toml_interposer_snippet,
)
from public_bridge.sandbox import SessionSandbox, SessionSandboxManager
from public_bridge.serve import serve, serve_bridge_async
from public_bridge.stdio_process import StdioProcessClient

__all__ = [
    "HttpPublicServerConfig",
    "PublicBridgeServer",
    "PublicServerConfig",
    "SessionSandbox",
    "SessionSandboxManager",
    "StdioProcessClient",
    "StdioPublicServerConfig",
    "build_manifest",
    "canonical_descriptor_hash",
    "control_app",
    "load_config",
    "serve",
    "serve_bridge_async",
    "toml_interposer_snippet",
]
