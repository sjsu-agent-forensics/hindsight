"""MCP tool server for Northwind's team chat, with tools named after AgentDojo's Slack suite.

Messages are untrusted content. The channel `ext-ergo-supplies` is shared with people at the
supplier Ergo Supplies, so a post there leaves the company, and so does a direct message to one of
them. Every effect entry carries an `external` flag for exactly that, so the effect log shows what
left Northwind without any lookup. Nothing is delivered anywhere outside the session.
"""

from pathlib import Path
from typing import Any

from fastmcp import FastMCP
from fastmcp.exceptions import ToolError
from hindsight_server_kit import (
    Session,
    SessionStore,
    is_external,
    load_json_fixture,
    next_id,
    require_address,
    serve,
)

PORT = 8016
BENIGN = Path(__file__).parent / "fixtures" / "benign.json"


def _load_chat(path: Path | None) -> dict[str, Any]:
    """Load a session's chat workspace: the benign fixture, or a fixture file / overlay on it."""
    return load_json_fixture(path, BENIGN)


server = FastMCP("Chat Server")
store = SessionStore("chat", _load_chat)


# Tool names and docstrings below are published to clients as the MCP tool name and
# description, so renaming a function or editing its docstring changes the tool descriptor.
@server.tool()
def get_channels() -> dict[str, Any]:
    """Return the names of the channels the user belongs to."""
    session = store.get()
    with session.lock:
        me = session.data["me"]
        names = [c["channel"] for c in session.data["channels"] if me in c["members"]]
    return {"channels": names}


@server.tool()
def read_channel_messages(channel: str) -> dict[str, Any]:
    """Return the messages in a channel, oldest first."""
    session = store.get()
    with session.lock:
        found = _channel(session.data, channel)
        return {"channel": found["channel"], "messages": [dict(m) for m in found["messages"]]}


@server.tool()
def read_inbox(user: str) -> dict[str, Any]:
    """Return the direct messages sent to a user, oldest first."""
    session = store.get()
    with session.lock:
        handle = _user(session.data, user)["user"]
        messages = [dict(m) for m in session.data["dms"] if m["recipient"] == handle]
    return {"user": handle, "messages": messages}


@server.tool()
def get_users_in_channel(channel: str) -> dict[str, Any]:
    """Return the members of a channel."""
    session = store.get()
    with session.lock:
        found = _channel(session.data, channel)
        return {"channel": found["channel"], "users": list(found["members"])}


@server.tool()
def send_channel_message(channel: str, body: str) -> dict[str, Any]:
    """Post a message to a channel as the user. Returns the posted message."""
    session = store.get()
    with session.lock:
        found = _channel(session.data, channel)
        message = _message(session, body)
        found["messages"].append(message)
        session.record_effect(
            "send_channel_message",
            message_id=message["message_id"],
            channel=found["channel"],
            body=body,
            external=found["external"],
        )
        return {"channel": found["channel"], **message}


@server.tool()
def send_direct_message(recipient: str, body: str) -> dict[str, Any]:
    """Send a direct message from the user to another user. Returns the sent message."""
    session = store.get()
    with session.lock:
        to = _user(session.data, recipient)
        message = {**_message(session, body), "recipient": to["user"]}
        session.data["dms"].append(message)
        session.record_effect(
            "send_direct_message",
            message_id=message["message_id"],
            recipient=to["user"],
            body=body,
            external=to["external"],
        )
        return dict(message)


@server.tool()
def add_user_to_channel(user: str, channel: str) -> dict[str, Any]:
    """Add a user to a channel. Returns the channel's updated member list."""
    session = store.get()
    with session.lock:
        member = _user(session.data, user)
        found = _channel(session.data, channel)
        if member["user"] in found["members"]:
            raise ToolError(f"{member['user']!r} is already in #{found['channel']}.")
        found["members"].append(member["user"])
        session.record_effect(
            "add_user_to_channel",
            user=member["user"],
            channel=found["channel"],
            external=member["external"] or found["external"],
        )
        return {"channel": found["channel"], "users": list(found["members"])}


@server.tool()
def invite_user_to_slack(user: str, user_email: str) -> dict[str, Any]:
    """Invite a new user to the chat workspace by handle and email address.

    Returns the new member.
    """
    handle = user.strip().lower()
    if not handle:
        raise ToolError("A new user needs a handle.")
    address = require_address(user_email)
    session = store.get()
    with session.lock:
        if any(u["user"] == handle for u in session.data["users"]):
            raise ToolError(f"{handle!r} is already in the workspace.")
        member = {
            "user": handle,
            "name": handle,
            "email": address,
            "external": is_external(address),
        }
        session.data["users"].append(member)
        session.record_effect(
            "invite_user_to_slack", user=handle, email=address, external=member["external"]
        )
        return dict(member)


@server.tool()
def remove_user_from_slack(user: str) -> dict[str, Any]:
    """Remove a user from the chat workspace and from every channel."""
    session = store.get()
    with session.lock:
        member = _user(session.data, user)
        if member["user"] == session.data["me"]:
            raise ToolError("The user cannot remove themselves from the workspace.")
        session.data["users"].remove(member)
        channels = [c for c in session.data["channels"] if member["user"] in c["members"]]
        for channel in channels:
            channel["members"].remove(member["user"])
        session.record_effect(
            "remove_user_from_slack",
            user=member["user"],
            channels=[c["channel"] for c in channels],
            external=member["external"],
        )
        return {"removed": True, "user": member["user"]}


def _channel(data: dict[str, Any], name: str) -> dict[str, Any]:
    wanted = name.strip().lstrip("#").lower()
    for channel in data["channels"]:
        if channel["channel"] == wanted:
            return channel
    names = ", ".join(c["channel"] for c in data["channels"])
    raise ToolError(f"No channel {name!r}; channels are {names}.")


def _user(data: dict[str, Any], handle: str) -> dict[str, Any]:
    wanted = handle.strip().lstrip("@").lower()
    for user in data["users"]:
        if user["user"] == wanted:
            return user
    raise ToolError(f"No user {handle!r} in the workspace; get_users_in_channel lists handles.")


def _message(session: Session, body: str) -> dict[str, Any]:
    # Message ids are unique across channels and direct messages.
    every = [m for c in session.data["channels"] for m in c["messages"]] + session.data["dms"]
    return {
        "message_id": next_id(every, "message_id", "m_"),
        "sender": session.data["me"],
        "body": body,
        "timestamp": session.now.isoformat(),
    }


def main() -> None:
    """Serve the tools over HTTP, with the kit's control endpoint on PORT + 100."""
    serve(server, store, port=PORT)
