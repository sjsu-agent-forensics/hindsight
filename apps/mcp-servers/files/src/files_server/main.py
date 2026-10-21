"""MCP tool server for the user's documents: a second internal source, and a chain through state.

File contents are untrusted content. `write_file` and `share_file` change state, so both go into
the effect log; a file written in one call and read back in another is the shared-state chain the
registry's `reads`/`writes` labels exist to catch. Paths are plain keys into the session's file
list, never real paths on disk, so nothing here can touch the host's filesystem.
"""

from pathlib import Path
from typing import Any

from fastmcp import FastMCP
from fastmcp.exceptions import ToolError
from hindsight_server_kit import SessionStore, load_json_fixture, next_id, serve

PORT = 8014
BENIGN = Path(__file__).parent / "fixtures" / "benign.json"


def load_files(path: Path | None) -> dict[str, Any]:
    """Load a session's files: the benign fixture, or a fixture file / overlay on it."""
    return load_json_fixture(path, BENIGN)


server = FastMCP("Files Server")
store = SessionStore("files", load_files)


# Tool names and docstrings below are published to clients as the MCP tool name and
# description, so renaming a function or editing its docstring changes the tool descriptor.
@server.tool()
def list_files(prefix: str = "") -> dict:
    """List files whose path starts with the prefix (all files by default), sorted by path."""
    session = store.get()
    with session.lock:
        files = sorted(
            (f for f in session.data["files"] if f["path"].startswith(prefix)),
            key=lambda f: f["path"],
        )
        return {"prefix": prefix, "files": [_summary(f) for f in files]}


@server.tool()
def read_file(path: str) -> dict:
    """Read a file's contents by path (e.g. "Sales/renewals-q4.txt")."""
    session = store.get()
    with session.lock:
        return dict(_file(session.data, _clean(path)))


@server.tool()
def write_file(path: str, content: str) -> dict:
    """Create a file, or replace the contents of an existing one, at the given path."""
    path = _clean(path)
    session = store.get()
    with session.lock:
        files = session.data["files"]
        existing = next((f for f in files if f["path"] == path), None)
        if existing is None:
            existing = {"file_id": next_id(files, "file_id", "f_"), "path": path, "shared_with": []}
            files.append(existing)
        created = "content" not in existing
        existing["content"] = content
        existing["modified"] = session.now.isoformat()
        session.record_effect(
            "write_file", file_id=existing["file_id"], path=path, created=created, content=content
        )
        return _summary(existing)


@server.tool()
def share_file(path: str, recipient: str) -> dict:
    """Share a file with another person by email address."""
    if "@" not in recipient:
        raise ToolError(f"Not an email address: {recipient}.")
    session = store.get()
    with session.lock:
        file = _file(session.data, _clean(path))
        if recipient not in file["shared_with"]:
            file["shared_with"].append(recipient)
        session.record_effect(
            "share_file", file_id=file["file_id"], path=file["path"], recipient=recipient
        )
        return _summary(file)


def _clean(path: str) -> str:
    # One spelling per file, so "/Sales/x.txt", "Sales\\x.txt" and "Sales/x.txt" are the same key.
    cleaned = "/".join(part for part in path.replace("\\", "/").split("/") if part not in ("", "."))
    if not cleaned or ".." in cleaned.split("/"):
        raise ToolError(f"Invalid path {path!r}.")
    return cleaned


def _file(data: dict[str, Any], path: str) -> dict[str, Any]:
    for file in data["files"]:
        if file["path"] == path:
            return file
    raise ToolError(f"No file at {path!r}. Use list_files to see what exists.")


def _summary(file: dict[str, Any]) -> dict[str, Any]:
    return {
        "file_id": file["file_id"],
        "path": file["path"],
        "size": len(file["content"]),
        "shared_with": list(file["shared_with"]),
        "modified": file["modified"],
    }


def main() -> None:
    """Serve the tools over HTTP, with the kit's control endpoint on PORT + 100."""
    serve(server, store, port=PORT)
