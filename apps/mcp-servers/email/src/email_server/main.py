"""MCP tool server for the user's mailbox: reading, searching and sending email.

Incoming emails are untrusted content (subjects, bodies, sender names). Nothing is actually
sent: a sent email is stored in the session's "sent" folder and logged in the effect log, which
is how the evaluator sees what the agent sent.
"""

from datetime import datetime
from pathlib import Path
from typing import Any

from fastmcp import FastMCP
from fastmcp.exceptions import ToolError
from hindsight_server_kit import SessionStore, load_json_fixture, next_id, serve

PORT = 8012
BENIGN = Path(__file__).parent / "fixtures" / "benign.json"
FOLDERS = ("inbox", "sent")


def load_mailbox(path: Path | None) -> dict[str, Any]:
    """Load a session's mailbox: the benign fixture, or a fixture file / overlay on it."""
    return load_json_fixture(path, BENIGN)


server = FastMCP("Email Server")
store = SessionStore("email", load_mailbox)


# Tool names and docstrings below are published to clients as the MCP tool name and
# description, so renaming a function or editing its docstring changes the tool descriptor.
@server.tool()
def list_emails(folder: str = "inbox", limit: int = 20) -> dict:
    """List emails in a folder ("inbox" or "sent"), newest first, without their bodies."""
    if folder not in FOLDERS:
        raise ToolError(f"Unknown folder {folder!r}; folders are {', '.join(FOLDERS)}.")
    session = store.get()
    with session.lock:
        emails = [e for e in session.data["emails"] if e["folder"] == folder]
        emails = _newest_first(emails)[: max(limit, 0)]
        return {"folder": folder, "emails": [_summary(e) for e in emails]}


@server.tool()
def read_email(email_id: str) -> dict:
    """Read one email in full by its email_id (e.g. "e_001"), and mark it read."""
    session = store.get()
    with session.lock:
        email = _email(session.data, email_id)
        email["read"] = True
        return dict(email)


@server.tool()
def search_emails(query: str) -> dict:
    """Search all folders for emails whose subject, body or sender contains the query
    (case-insensitive). Returns up to 20 matches, newest first, without their bodies."""
    needle = query.lower()
    session = store.get()
    with session.lock:
        matches = [
            e
            for e in session.data["emails"]
            if needle
            in " ".join([e["subject"], e["body"], e["from"]["name"], e["from"]["email"]]).lower()
        ]
        return {"query": query, "emails": [_summary(e) for e in _newest_first(matches)[:20]]}


@server.tool()
def send_email(to: list[str], subject: str, body: str, cc: list[str] | None = None) -> dict:
    """Send an email from the user's address to one or more recipients."""
    recipients = list(to) + list(cc or [])
    if not to:
        raise ToolError("An email needs at least one recipient in 'to'.")
    bad = [r for r in recipients if "@" not in r]
    if bad:
        raise ToolError(f"Not an email address: {', '.join(bad)}.")
    session = store.get()
    with session.lock:
        emails = session.data["emails"]
        email = {
            "email_id": next_id(emails, "email_id", "e_"),
            "folder": "sent",
            "from": dict(session.data["owner"]),
            "to": list(to),
            "cc": list(cc or []),
            "subject": subject,
            "body": body,
            "date": session.now.isoformat(),
            "read": True,
        }
        emails.append(email)
        session.record_effect(
            "send_email",
            email_id=email["email_id"],
            to=email["to"],
            cc=email["cc"],
            subject=subject,
            body=body,
        )
        return {"sent": True, "email_id": email["email_id"], "to": email["to"], "cc": email["cc"]}


def _email(data: dict[str, Any], email_id: str) -> dict[str, Any]:
    for email in data["emails"]:
        if email["email_id"] == email_id:
            return email
    raise ToolError(f"No email with id {email_id!r}.")


def _summary(email: dict[str, Any]) -> dict[str, Any]:
    return {k: email[k] for k in ("email_id", "from", "to", "subject", "date", "read")}


def _newest_first(emails: list[dict[str, Any]]) -> list[dict[str, Any]]:
    # Ties keep fixture order, so the listing is the same on every run.
    return sorted(emails, key=lambda e: datetime.fromisoformat(e["date"]), reverse=True)


def main() -> None:
    """Serve the tools over HTTP, with the kit's control endpoint on PORT + 100."""
    serve(server, store, port=PORT)
