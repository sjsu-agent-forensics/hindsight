"""MCP tool server for Alex Morgan's cloud drive, with tools named after AgentDojo's Workspace
suite.

The drive holds the workspace's most private data: the customer list, budgets, salary bands and
contracts. File content is untrusted, because files can be written or shared by others, including
people outside Northwind. Files are entries in the session's data and never touch the disk. Every
change is logged, and `share_file` records whether the file left the company.
"""

from pathlib import Path
from typing import Any, Literal

from fastmcp import FastMCP
from fastmcp.exceptions import ToolError
from hindsight_server_kit import (
    SessionStore,
    is_external,
    load_json_fixture,
    next_id,
    require_address,
    serve,
)

PORT = 8014
BENIGN = Path(__file__).parent / "fixtures" / "benign.json"


def _load_drive(path: Path | None) -> dict[str, Any]:
    """Load a session's drive: the benign fixture, or a fixture file / overlay on it."""
    return load_json_fixture(path, BENIGN)


server = FastMCP("Drive Server")
store = SessionStore("drive", _load_drive)


# Tool names and docstrings below are published to clients as the MCP tool name and
# description, so renaming a function or editing its docstring changes the tool descriptor.
@server.tool()
def list_files() -> dict[str, Any]:
    """List every file the user owns or that is shared with them, without file content."""
    session = store.get()
    with session.lock:
        return {"files": [_summary(f) for f in session.data["files"]]}


@server.tool()
def search_files(query: str) -> dict[str, Any]:
    """Return the files whose content contains the query, ignoring case."""
    needle = _needle(query)
    session = store.get()
    with session.lock:
        matches = [dict(f) for f in session.data["files"] if needle in f["content"].lower()]
    return {"query": query, "files": matches}


@server.tool()
def search_files_by_filename(filename: str) -> dict[str, Any]:
    """Return the files whose name contains the given text, ignoring case."""
    needle = _needle(filename)
    session = store.get()
    with session.lock:
        matches = [dict(f) for f in session.data["files"] if needle in f["filename"].lower()]
    return {"query": filename, "files": matches}


@server.tool()
def get_file_by_id(file_id: str) -> dict[str, Any]:
    """Return a file by its file_id (e.g. "f_001"), with its content and sharing list."""
    session = store.get()
    with session.lock:
        return dict(_file(session.data, file_id))


@server.tool()
def create_file(filename: str, content: str) -> dict[str, Any]:
    """Create a new file owned by the user.

    Returns the new file.
    """
    if not filename.strip():
        raise ToolError("A file needs a name.")
    session = store.get()
    with session.lock:
        files = session.data["files"]
        file = {
            "file_id": next_id(files, "file_id", "f_"),
            "filename": filename.strip(),
            "content": content,
            "owner": session.data["owner"],
            "shared_with": {},
            "last_modified": session.now.isoformat(),
        }
        files.append(file)
        session.record_effect(
            "create_file", file_id=file["file_id"], filename=file["filename"], content=content
        )
        return dict(file)


@server.tool()
def append_to_file(file_id: str, content: str) -> dict[str, Any]:
    """Append text to the end of a file the user can edit.

    Returns the updated file.
    """
    session = store.get()
    with session.lock:
        file = _writable(session.data, file_id)
        file["content"] += content
        file["last_modified"] = session.now.isoformat()
        session.record_effect(
            "append_to_file", file_id=file_id, filename=file["filename"], content=content
        )
        return dict(file)


@server.tool()
def delete_file(file_id: str) -> dict[str, Any]:
    """Delete a file the user can edit.

    Returns the deleted file.
    """
    session = store.get()
    with session.lock:
        file = _writable(session.data, file_id)
        session.data["files"].remove(file)
        session.record_effect("delete_file", file_id=file_id, filename=file["filename"])
        return dict(file)


@server.tool()
def share_file(file_id: str, email: str, permission: Literal["r", "rw"]) -> dict[str, Any]:
    """Share a file with someone by email address, with read ("r") or read-write ("rw") access.

    Returns the file with its updated sharing list.
    """
    address = require_address(email)
    session = store.get()
    with session.lock:
        file = _writable(session.data, file_id)
        file["shared_with"][address] = permission
        session.record_effect(
            "share_file",
            file_id=file_id,
            filename=file["filename"],
            email=address,
            permission=permission,
            external=is_external(address),
        )
        return dict(file)


def _file(data: dict[str, Any], file_id: str) -> dict[str, Any]:
    for file in data["files"]:
        if file["file_id"] == file_id:
            return file
    raise ToolError(f"No file with id {file_id!r}.")


def _writable(data: dict[str, Any], file_id: str) -> dict[str, Any]:
    # The user can change a file they own or one shared with them read-write.
    file = _file(data, file_id)
    owner = data["owner"]
    if file["owner"] != owner and file["shared_with"].get(owner) != "rw":
        raise ToolError(f"File {file_id!r} is shared with the user read-only; it cannot change.")
    return file


def _summary(file: dict[str, Any]) -> dict[str, Any]:
    summary = {k: v for k, v in file.items() if k != "content"}
    summary["size"] = len(file["content"])
    return summary


def _needle(query: str) -> str:
    needle = query.strip().lower()
    if not needle:
        raise ToolError("The search query is empty.")
    return needle


def main() -> None:
    """Serve the tools over HTTP, with the kit's control endpoint on PORT + 100."""
    serve(server, store, port=PORT)
