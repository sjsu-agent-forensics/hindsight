"""The people directory shared by every workspace server, and checks on addresses.

All five servers describe one fictional company, Northwind, so a name, email address or chat handle
means the same person on every server. The directory is generated together with the servers'
fixtures by `scripts/generate_workspace.py` and is read-only: it is not per session, and no tool
changes it.

Addresses that tools receive come from the agent and are untrusted. They are only checked for shape
and looked up here; an address that is not in the directory is allowed (mail can go to anyone) but
counts as outside Northwind, which is what the effect log's `external` flags record.
"""

import functools
import re
from pathlib import Path
from typing import TypedDict

from fastmcp.exceptions import ToolError

from hindsight_server_kit.session import read_json

DIRECTORY = Path(__file__).parent / "directory.json"
INTERNAL_DOMAIN = "northwind.example"

_ADDRESS = re.compile(r"[^@\s]+@[^@\s]+\.[^@\s]+")


class Person(TypedDict):
    """One entry of the people directory."""

    handle: str
    name: str
    email: str
    phone: str
    role: str
    internal: bool


@functools.cache
def _people() -> tuple[Person, ...]:
    """Return everyone in the directory, in generator order; read once per process."""
    return tuple(read_json(DIRECTORY)["people"])


def lookup_person(handle_or_email: str) -> Person | None:
    """Return the person with this chat handle or email address, ignoring case, or None."""
    key = handle_or_email.strip().lower()
    for person in _people():
        if key in (person["handle"], person["email"].lower()):
            return person
    return None


def search_people(query: str, field: str) -> list[Person]:
    """Return the people whose `field` ("name" or "email") contains `query`, ignoring case.

    Raises:
        ToolError: The query is empty, which would match everyone.
    """
    needle = query.strip().lower()
    if not needle:
        raise ToolError("The search query is empty.")
    return [p for p in _people() if needle in p[field].lower()]


def require_address(address: str) -> str:
    """Return `address` trimmed, or raise a ToolError if it is not an email address."""
    address = address.strip()
    if not _ADDRESS.fullmatch(address):
        raise ToolError(f"{address!r} is not an email address.")
    return address


def is_external(address: str) -> bool:
    """Return whether an email address is outside Northwind's own domain."""
    return not address.strip().lower().endswith("@" + INTERNAL_DOMAIN)
