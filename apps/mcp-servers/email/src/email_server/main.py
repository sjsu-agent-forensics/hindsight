"""MCP tool server for Alex Morgan's mailbox, with tools named after AgentDojo's Workspace suite.

Received emails are untrusted content: their senders include customers and the supplier Ergo
Supplies, and their subjects and bodies are returned to the agent unchanged. Nothing is ever
delivered. A sent email is stored in the session's "sent" folder and logged in the effect log,
which is how the evaluator sees what the agent sent, and to whom.
"""

from datetime import datetime
from pathlib import Path
from typing import Any

from fastmcp import FastMCP
from fastmcp.exceptions import ToolError
from hindsight_server_kit import (
    SessionStore,
    is_external,
    load_json_fixture,
    next_id,
    require_address,
    search_people,
    serve,
)

PORT = 8012
BENIGN = Path(__file__).parent / "fixtures" / "benign.json"


def _load_mailbox(path: Path | None) -> dict[str, Any]:
    """Load a session's mailbox: the benign fixture, or a fixture file / overlay on it."""
    return load_json_fixture(path, BENIGN)


server = FastMCP("Email Server")
store = SessionStore("email", _load_mailbox)


# Tool names and docstrings below are published to clients as the MCP tool name and
# description, so renaming a function or editing its docstring changes the tool descriptor.
@server.tool()
def send_email(
    recipients: list[str],
    subject: str,
    body: str,
    attachments: list[str] | None = None,
    cc: list[str] | None = None,
    bcc: list[str] | None = None,
) -> dict[str, Any]:
    """Send an email from the user's address.

    `recipients`, `cc` and `bcc` are email addresses; `attachments` are cloud drive file ids
    (e.g. "f_001"). Returns the sent email.
    """
    if not recipients:
        raise ToolError("An email needs at least one address in 'recipients'.")
    to = [require_address(a) for a in recipients]
    cc = [require_address(a) for a in cc or []]
    bcc = [require_address(a) for a in bcc or []]
    session = store.get()
    with session.lock:
        emails = session.data["emails"]
        email = {
            "email_id": next_id(emails, "email_id", "e_"),
            "folder": "sent",
            "sender": session.data["owner"],
            "recipients": to,
            "cc": cc,
            "bcc": bcc,
            "subject": subject,
            "body": body,
            "attachments": list(attachments or []),
            "timestamp": session.now.isoformat(),
            "read": True,
        }
        emails.append(email)
        session.record_effect(
            "send_email",
            email_id=email["email_id"],
            recipients=to,
            cc=cc,
            bcc=bcc,
            subject=subject,
            body=body,
            attachments=email["attachments"],
            external=any(is_external(a) for a in to + cc + bcc),
        )
        return dict(email)


@server.tool()
def delete_email(email_id: str) -> dict[str, Any]:
    """Delete an email by its email_id (e.g. "e_001"), moving it to the trash."""
    session = store.get()
    with session.lock:
        email = _email(session.data, email_id)
        if email["folder"] == "trash":
            raise ToolError(f"Email {email_id!r} is already in the trash.")
        previous, email["folder"] = email["folder"], "trash"
        session.record_effect(
            "delete_email", email_id=email_id, folder=previous, subject=email["subject"]
        )
        return {"deleted": True, "email_id": email_id, "folder": "trash"}


@server.tool()
def get_unread_emails() -> dict[str, Any]:
    """Return all unread received emails, newest first, and mark them as read."""
    session = store.get()
    with session.lock:
        unread = [e for e in _folder(session.data, "inbox") if not e["read"]]
        for email in unread:
            email["read"] = True
        return {"emails": [dict(e) for e in unread]}


@server.tool()
def get_received_emails() -> dict[str, Any]:
    """Return all received emails, newest first."""
    return _list_folder("inbox")


@server.tool()
def get_sent_emails() -> dict[str, Any]:
    """Return all sent emails, newest first."""
    return _list_folder("sent")


@server.tool()
def get_draft_emails() -> dict[str, Any]:
    """Return all draft emails, newest first."""
    return _list_folder("drafts")


@server.tool()
def search_emails(query: str, sender: str | None = None) -> dict[str, Any]:
    """Search emails whose subject or body contains the query, ignoring case.

    If `sender` is given, only emails from that address are returned. Emails in the trash are not
    searched. Results are newest first.
    """
    needle = query.strip().lower()
    if not needle:
        raise ToolError("The search query is empty.")
    session = store.get()
    with session.lock:
        matches = [
            dict(e)
            for e in _newest_first(session.data["emails"])
            if e["folder"] != "trash"
            and needle in f"{e['subject']}\n{e['body']}".lower()
            and (sender is None or e["sender"].lower() == sender.strip().lower())
        ]
    return {"query": query, "emails": matches}


@server.tool()
def search_contacts_by_name(query: str) -> dict[str, Any]:
    """Find contacts whose name contains the query, ignoring case."""
    return {"query": query, "contacts": search_people(query, "name")}


@server.tool()
def search_contacts_by_email(query: str) -> dict[str, Any]:
    """Find contacts whose email address contains the query, ignoring case."""
    return {"query": query, "contacts": search_people(query, "email")}


def _list_folder(folder: str) -> dict[str, Any]:
    session = store.get()
    with session.lock:
        return {"emails": [dict(e) for e in _folder(session.data, folder)]}


def _folder(data: dict[str, Any], folder: str) -> list[dict[str, Any]]:
    return _newest_first([e for e in data["emails"] if e["folder"] == folder])


def _email(data: dict[str, Any], email_id: str) -> dict[str, Any]:
    for email in data["emails"]:
        if email["email_id"] == email_id:
            return email
    raise ToolError(f"No email with id {email_id!r}.")


def _newest_first(emails: list[dict[str, Any]]) -> list[dict[str, Any]]:
    # Ties keep fixture order, so a listing is the same on every run.
    return sorted(emails, key=lambda e: datetime.fromisoformat(e["timestamp"]), reverse=True)


def main() -> None:
    """Serve the tools over HTTP, with the kit's control endpoint on PORT + 100."""
    serve(server, store, port=PORT)
