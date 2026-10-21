"""Fixture files for the JSON-backed servers, and overlays that change a few items of the benign one.

A server's fixture is a JSON object whose lists hold items with a stable id (`email_id`,
`file_id`, ...). A fixture file either replaces the benign fixture outright, or starts from it:

    {
      "base": "benign",
      "add":   {"emails": [{"email_id": "e_900", ...}]},   # appended to that list
      "patch": {"e_003": {"body": "..."}}                   # fields replaced on that item
    }

`patch` finds an item by its id in any list, so an overlay names only the items it changes.
"""

import copy
from pathlib import Path
from typing import Any

from hindsight_server_kit.session import read_json


def load_json_fixture(path: Path | None, benign: Path) -> dict[str, Any]:
    """Return the fixture at `path`, or the server's benign fixture for None."""
    benign_data = read_json(benign)
    if path is None:
        return benign_data
    spec = read_json(path)
    if spec.get("base") != "benign":
        return spec
    return apply_overlay(benign_data, spec)


def apply_overlay(data: dict[str, Any], overlay: dict[str, Any]) -> dict[str, Any]:
    """Return a copy of `data` with the overlay's `add` and `patch` applied."""
    data = copy.deepcopy(data)
    for key, items in overlay.get("add", {}).items():
        if not isinstance(data.get(key), list):
            raise ValueError(f"Overlay adds to {key!r}, which is not a list in the fixture.")
        data[key].extend(copy.deepcopy(items))
    for item_id, fields in overlay.get("patch", {}).items():
        item = find_item(data, item_id)
        if item is None:
            raise ValueError(f"Overlay patches {item_id!r}, which is not in the fixture.")
        item.update(copy.deepcopy(fields))
    return data


def find_item(data: Any, item_id: str) -> dict[str, Any] | None:
    """Find the item whose `*_id` field equals `item_id`, searching nested lists and objects."""
    if isinstance(data, dict):
        if any(k.endswith("_id") and v == item_id for k, v in data.items()):
            return data
        children = data.values()
    elif isinstance(data, list):
        children = data
    else:
        return None
    for child in children:
        found = find_item(child, item_id)
        if found is not None:
            return found
    return None


def next_id(items: list[dict[str, Any]], key: str, prefix: str) -> str:
    """Return the next id after the highest `prefix<number>` in `items`, keeping its width.

    New ids depend only on what is already in the session, so a rerun assigns the same ones.
    """
    numbers = [
        item[key][len(prefix) :]
        for item in items
        if str(item.get(key, "")).startswith(prefix) and item[key][len(prefix) :].isdigit()
    ]
    width = max((len(n) for n in numbers), default=3)
    return f"{prefix}{max((int(n) for n in numbers), default=0) + 1:0{width}d}"
