"""MCP tool server for team chat: channels and direct messages.

Messages are untrusted content. Posting and direct messages go into the effect log, marked
`external` when the channel is shared with, or the user belongs to, another organisation.
"""

from pathlib import Path
from typing import Any

from fastmcp import FastMCP
from fastmcp.exceptions import ToolError
from hindsight_server_kit import Session, SessionStore, load_json_fixture, next_id, serve

PORT = 8016
BENIGN = Path(__file__).parent / "fixtures" / "benign.json"


def load_workspace(path: Path | None) -> dict[str, Any]:
    """Load a session's chat workspace: the benign fixture, or a fixture file / overlay on it."""
    return load_json_fixture(path, BENIGN)


server = FastMCP("Messaging Server")
store = SessionStore("messaging", load_workspace)


# Tool names and docstrings below are published to clients as the MCP tool name and
# description, so renaming a function or editing its docstring changes the tool descriptor.
@server.tool()
def read_channel(channel: str, limit: int = 20) -> dict:
    """Read the most recent messages in a channel (e.g. "general"), oldest first."""
    session = store.get()
    with session.lock:
        data = session.data
        found = _channel(data, channel)
        names = {u["user"]: u["name"] for u in data["users"]}
        messages = found["messages"][-limit:] if limit > 0 else []
        return {
            "channel": found["channel"],
            "external": found["external"],
            "messages": [{**m, "name": names.get(m["user"], m["user"])} for m in messages],
        }


@server.tool()
def post_message(channel: str, text: str) -> dict:
    """Post a message to a channel as the user."""
    session = store.get()
    with session.lock:
        data = session.data
        found = _channel(data, channel)
        message = _new_message(session, user=data["me"], text=text)
        found["messages"].append(message)
        session.record_effect(
            "post_message",
            message_id=message["message_id"],
            channel=found["channel"],
            external=found["external"],
            text=text,
        )
        return {"posted": True, "channel": found["channel"], "message_id": message["message_id"]}


@server.tool()
def send_dm(user: str, text: str) -> dict:
    """Send a direct message to a user by their handle (e.g. "jamie.park")."""
    session = store.get()
    with session.lock:
        data = session.data
        recipient = next((u for u in data["users"] if u["user"] == user), None)
        if recipient is None:
            handles = ", ".join(u["user"] for u in data["users"])
            raise ToolError(f"No user {user!r}; users are {handles}.")
        message = {**_new_message(session, user=data["me"], text=text), "to": user}
        data["dms"].append(message)
        session.record_effect(
            "send_dm",
            message_id=message["message_id"],
            user=user,
            external=recipient["external"],
            text=text,
        )
        return {"sent": True, "user": user, "message_id": message["message_id"]}


def _channel(data: dict[str, Any], name: str) -> dict[str, Any]:
    name = name.lstrip("#")
    for channel in data["channels"]:
        if channel["channel"] == name:
            return channel
    names = ", ".join(c["channel"] for c in data["channels"])
    raise ToolError(f"No channel {name!r}; channels are {names}.")


def _new_message(session: Session, user: str, text: str) -> dict[str, Any]:
    # Ids run across channels and DMs alike, so every message in the session has its own.
    data = session.data
    every = [m for c in data["channels"] for m in c["messages"]] + data["dms"]
    return {
        "message_id": next_id(every, "message_id", "m_"),
        "user": user,
        "text": text,
        "ts": session.now.isoformat(),
    }


def main() -> None:
    """Serve the tools over HTTP, with the kit's control endpoint on PORT + 100."""
    serve(server, store, port=PORT)
