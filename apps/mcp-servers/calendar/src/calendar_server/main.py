"""MCP tool server for the user's calendar: another source of untrusted text, and another sink.

Event titles and notes are untrusted content, and inviting someone to an event shares its title
and notes with them, so `create_event` goes into the effect log with its participants. "Today"
is the session's scenario time, not the wall clock, so listings match on a rerun.
"""

from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from fastmcp import FastMCP
from fastmcp.exceptions import ToolError
from hindsight_server_kit import SessionStore, load_json_fixture, next_id, serve

PORT = 8017
BENIGN = Path(__file__).parent / "fixtures" / "benign.json"
DEFAULT_WINDOW = timedelta(days=7)


def load_calendar(path: Path | None) -> dict[str, Any]:
    """Load a session's calendar: the benign fixture, or a fixture file / overlay on it."""
    return load_json_fixture(path, BENIGN)


server = FastMCP("Calendar Server")
store = SessionStore("calendar", load_calendar)


# Tool names and docstrings below are published to clients as the MCP tool name and
# description, so renaming a function or editing its docstring changes the tool descriptor.
@server.tool()
def list_events(start: str | None = None, end: str | None = None) -> dict:
    """List events overlapping a time range, earliest first.

    start and end are ISO 8601 date-times (e.g. "2026-11-06T00:00:00-08:00"). By default the
    range is today through the next 7 days.
    """
    session = store.get()
    today = session.now.replace(hour=0, minute=0, second=0, microsecond=0)
    lo = _parse(start, "start") if start else today
    hi = _parse(end, "end") if end else lo + DEFAULT_WINDOW
    with session.lock:
        events = [
            dict(e)
            for e in session.data["events"]
            if _parse(e["start"], "start") < hi and _parse(e["end"], "end") > lo
        ]
    events.sort(key=lambda e: _parse(e["start"], "start"))
    return {"start": lo.isoformat(), "end": hi.isoformat(), "events": events}


@server.tool()
def create_event(
    title: str,
    start: str,
    end: str,
    participants: list[str],
    location: str = "",
    notes: str = "",
) -> dict:
    """Create an event and invite its participants by email address.

    start and end are ISO 8601 date-times with a UTC offset (e.g. "2026-11-06T14:00:00-08:00").
    """
    if _parse(end, "end") <= _parse(start, "start"):
        raise ToolError("An event must end after it starts.")
    bad = [p for p in participants if "@" not in p]
    if bad:
        raise ToolError(f"Not an email address: {', '.join(bad)}.")
    session = store.get()
    with session.lock:
        events = session.data["events"]
        owner = session.data["owner"]
        event = {
            "event_id": next_id(events, "event_id", "ev_"),
            "title": title,
            "start": start,
            "end": end,
            "participants": [owner] + [p for p in participants if p != owner],
            "location": location,
            "notes": notes,
        }
        events.append(event)
        session.record_effect("create_event", **event)
        return dict(event)


def _parse(value: str, name: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        raise ToolError(f"{name} {value!r} is not an ISO 8601 date-time.") from None
    if parsed.tzinfo is None:
        raise ToolError(f"{name} {value!r} needs a UTC offset, e.g. -08:00.")
    return parsed


def main() -> None:
    """Serve the tools over HTTP, with the kit's control endpoint on PORT + 100."""
    serve(server, store, port=PORT)
