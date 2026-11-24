"""MCP tool server for Alex Morgan's calendar, with tools named after AgentDojo's Workspace suite.

Event titles, descriptions and locations are untrusted content: events from customers and the
supplier carry their text unchanged. Inviting someone shares an event's title and description with
them, so every change, including added participants, goes into the effect log; nothing is sent.
"Today" is the session's scenario time, not the wall clock, so results match on a rerun.
"""

from datetime import date, datetime
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
    serve,
)

PORT = 8017
BENIGN = Path(__file__).parent / "fixtures" / "benign.json"


def _load_calendar(path: Path | None) -> dict[str, Any]:
    """Load a session's calendar: the benign fixture, or a fixture file / overlay on it."""
    return load_json_fixture(path, BENIGN)


server = FastMCP("Calendar Server")
store = SessionStore("calendar", _load_calendar)


# Tool names and docstrings below are published to clients as the MCP tool name and
# description, so renaming a function or editing its docstring changes the tool descriptor.
@server.tool()
def get_current_day() -> dict[str, Any]:
    """Return today's date (YYYY-MM-DD) and the current time."""
    now = store.get().now
    return {"date": now.date().isoformat(), "now": now.isoformat()}


@server.tool()
def search_calendar_events(query: str, date: str | None = None) -> dict[str, Any]:
    """Search events whose title or description contains the query, ignoring case.

    If `date` (YYYY-MM-DD) is given, only events starting on that day are returned. Results are in
    start time order.
    """
    needle = query.strip().lower()
    if not needle:
        raise ToolError("The search query is empty.")
    day = _parse_day(date) if date else None
    session = store.get()
    with session.lock:
        matches = [
            dict(e)
            for e in _by_start(session.data["events"])
            if needle in f"{e['title']}\n{e['description']}".lower()
            and (day is None or _start(e).date() == day)
        ]
    return {"query": query, "events": matches}


@server.tool()
def get_day_calendar_events(day: str) -> dict[str, Any]:
    """Return the events starting on a day (YYYY-MM-DD), in start time order."""
    wanted = _parse_day(day)
    session = store.get()
    with session.lock:
        events = [dict(e) for e in _by_start(session.data["events"]) if _start(e).date() == wanted]
    return {"day": wanted.isoformat(), "events": events}


@server.tool()
def create_calendar_event(
    title: str,
    start_time: str,
    end_time: str,
    description: str = "",
    participants: list[str] | None = None,
    location: str | None = None,
) -> dict[str, Any]:
    """Create an event and invite its participants (email addresses).

    Times are ISO 8601 with a UTC offset, e.g. "2026-11-09T14:00:00-08:00". The user is always a
    participant. Returns the new event.
    """
    start, end = _parse_range(start_time, end_time)
    invited = [require_address(a) for a in participants or []]
    session = store.get()
    with session.lock:
        events = session.data["events"]
        owner = session.data["owner"]
        event = {
            "event_id": next_id(events, "event_id", "ev_"),
            "title": title,
            "description": description,
            "start_time": start.isoformat(),
            "end_time": end.isoformat(),
            "location": location or "",
            "participants": _merge([owner], invited),
            "status": "confirmed",
        }
        events.append(event)
        session.record_effect(
            "create_calendar_event",
            event_id=event["event_id"],
            title=title,
            description=description,
            start_time=event["start_time"],
            end_time=event["end_time"],
            location=event["location"],
            participants=event["participants"],
            external=any(is_external(a) for a in event["participants"]),
        )
        return dict(event)


@server.tool()
def cancel_calendar_event(event_id: str) -> dict[str, Any]:
    """Cancel an event by its event_id (e.g. "ev_001"); participants are told it is canceled."""
    session = store.get()
    with session.lock:
        event = _live_event(session.data, event_id)
        event["status"] = "canceled"
        session.record_effect(
            "cancel_calendar_event",
            event_id=event_id,
            title=event["title"],
            participants=list(event["participants"]),
        )
        return {"canceled": True, "event_id": event_id, "title": event["title"]}


@server.tool()
def reschedule_calendar_event(
    event_id: str, new_start_time: str, new_end_time: str | None = None
) -> dict[str, Any]:
    """Move an event to a new start time (ISO 8601 with a UTC offset).

    Without `new_end_time` the event keeps its length. Returns the updated event.
    """
    session = store.get()
    with session.lock:
        event = _live_event(session.data, event_id)
        if new_end_time is None:
            length = _end(event) - _start(event)
            start = _parse_time(new_start_time, "new_start_time")
            end = start + length
        else:
            start, end = _parse_range(new_start_time, new_end_time)
        event["start_time"], event["end_time"] = start.isoformat(), end.isoformat()
        session.record_effect(
            "reschedule_calendar_event",
            event_id=event_id,
            start_time=event["start_time"],
            end_time=event["end_time"],
        )
        return dict(event)


@server.tool()
def add_calendar_event_participants(event_id: str, participants: list[str]) -> dict[str, Any]:
    """Invite more people (email addresses) to an event. Returns the updated event."""
    if not participants:
        raise ToolError("Name at least one participant to add.")
    invited = [require_address(a) for a in participants]
    session = store.get()
    with session.lock:
        event = _live_event(session.data, event_id)
        current = {a.lower() for a in event["participants"]}
        added = [a for a in _merge([], invited) if a.lower() not in current]
        event["participants"] = _merge(event["participants"], added)
        session.record_effect(
            "add_calendar_event_participants",
            event_id=event_id,
            title=event["title"],
            added=added,
            external=any(is_external(a) for a in added),
        )
        return dict(event)


def _live_event(data: dict[str, Any], event_id: str) -> dict[str, Any]:
    for event in data["events"]:
        if event["event_id"] == event_id:
            if event["status"] == "canceled":
                raise ToolError(f"Event {event_id!r} is canceled.")
            return event
    raise ToolError(f"No event with id {event_id!r}.")


def _parse_day(value: str) -> date:
    try:
        return date.fromisoformat(value.strip())
    except ValueError:
        raise ToolError(f"{value!r} is not a date; use YYYY-MM-DD, e.g. 2026-11-06.") from None


def _parse_time(value: str, name: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.strip())
    except ValueError:
        raise ToolError(f"{name} {value!r} is not an ISO 8601 time.") from None
    if parsed.tzinfo is None:
        raise ToolError(f"{name} {value!r} needs a UTC offset, e.g. 2026-11-09T14:00:00-08:00.")
    return parsed


def _parse_range(start_time: str, end_time: str) -> tuple[datetime, datetime]:
    start = _parse_time(start_time, "start_time")
    end = _parse_time(end_time, "end_time")
    if end <= start:
        raise ToolError(f"The event ends ({end_time}) before it starts ({start_time}).")
    return start, end


def _merge(addresses: list[str], more: list[str]) -> list[str]:
    # Keep the first spelling of each address, compared without case.
    merged: list[str] = []
    for address in addresses + more:
        if address.lower() not in (a.lower() for a in merged):
            merged.append(address)
    return merged


def _start(event: dict[str, Any]) -> datetime:
    return datetime.fromisoformat(event["start_time"])


def _end(event: dict[str, Any]) -> datetime:
    return datetime.fromisoformat(event["end_time"])


def _by_start(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(events, key=_start)


def main() -> None:
    """Serve the tools over HTTP, with the kit's control endpoint on PORT + 100."""
    serve(server, store, port=PORT)
