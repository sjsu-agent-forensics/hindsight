"""Shared plumbing for the internal MCP tool servers.

Every internal server builds a `SessionStore` at import time and reads its state through
`store.get()` inside each tool, then calls `serve()` from `main()`. The kit owns which session a
request belongs to, the control endpoint that seeds and discards sessions, the per-session effect
log, the manifest export and the shared people directory; each server owns only its tools and its
fixture loader.
"""

from hindsight_server_kit.control import control_app
from hindsight_server_kit.fixtures import apply_overlay, find_item, load_json_fixture, next_id
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
from hindsight_server_kit.workspace import (
    INTERNAL_DOMAIN,
    Person,
    is_external,
    lookup_person,
    require_address,
    search_people,
)

__all__ = [
    "CONTROL_PORT_OFFSET",
    "DEFAULT_SEED",
    "DEFAULT_TIME",
    "INTERNAL_DOMAIN",
    "MCP_PATH",
    "Person",
    "SESSION_HEADER",
    "Session",
    "SessionStore",
    "apply_overlay",
    "control_app",
    "find_item",
    "is_external",
    "load_json_fixture",
    "lookup_person",
    "manifest",
    "next_id",
    "read_json",
    "require_address",
    "search_people",
    "serve",
]
