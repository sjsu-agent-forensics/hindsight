"""Shared plumbing for the internal MCP tool servers.

Every internal server builds a `SessionStore` at import time and reads its state through
`store.get()` inside each tool, then calls `serve()` from `main()`. The kit owns which session a
request belongs to, the control endpoint that seeds and discards sessions, the per-session effect
log, and the manifest export; each server owns only its tools and its fixture loader.
"""

from hindsight_server_kit.control import control_app
from hindsight_server_kit.manifest import manifest
from hindsight_server_kit.serve import CONTROL_PORT_OFFSET, MCP_PATH, serve
from hindsight_server_kit.session import (
    DEFAULT_SEED,
    DEFAULT_TIME,
    SESSION_HEADER,
    Session,
    SessionStore,
    read_json,
)

__all__ = [
    "CONTROL_PORT_OFFSET",
    "DEFAULT_SEED",
    "DEFAULT_TIME",
    "MCP_PATH",
    "SESSION_HEADER",
    "Session",
    "SessionStore",
    "control_app",
    "manifest",
    "read_json",
    "serve",
]
