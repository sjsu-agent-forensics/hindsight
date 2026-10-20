"""MCP tool server over a fake customer database: the confidential source in each scenario.

Each session gets its own in-memory SQLite copy of the generated customers, so an update in one
run never shows up in another. The tools are fixed lookups rather than raw SQL, which keeps what
the agent can read and change narrow enough for the security rules to check.
"""

import sqlite3
from pathlib import Path
from typing import Any

from fastmcp import FastMCP
from fastmcp.exceptions import ToolError
from hindsight_server_kit import SessionStore, read_json, serve

from records_server.generate import DEFAULT_CUSTOMERS, DEFAULT_SEED, generate

PORT = 8011

# Fields update_customer may change. The id and account number are deliberately absent: they are
# what ties a response span back to one record.
UPDATABLE_FIELDS = {"name", "email", "phone", "street", "city", "state", "zip", "tier", "notes"}


def load_records(path: Path | None) -> sqlite3.Connection:
    """Build one session's database from its fixture, or the benign default for None.

    A fixture is JSON with optional keys: `seed` and `customers` for the generator; `database`, a
    SQLite file (written by `records-generate`) to copy instead of generating; and `overlays`,
    `{customer_id: {field: value}}` applied on top, which is how an attack scenario plants an
    injection in one customer's `notes`. The file named by `database` is copied into memory, so
    nothing a session does writes back to it.
    """
    spec: dict[str, Any] = read_json(path) if path else {}
    if "database" in spec:
        source = Path(spec["database"])
        if not source.is_absolute() and path is not None:
            source = path.parent / source
        db = sqlite3.connect(":memory:", check_same_thread=False)
        db.row_factory = sqlite3.Row
        with sqlite3.connect(source) as src:
            src.backup(db)
        src.close()
    else:
        db = generate(spec.get("seed", DEFAULT_SEED), spec.get("customers", DEFAULT_CUSTOMERS))
    for customer_id, fields in spec.get("overlays", {}).items():
        _update(db, customer_id, fields)
    return db


server = FastMCP("Records Server")
store = SessionStore("records", load_records)


# Tool names and docstrings below are published to clients as the MCP tool name and
# description, so renaming a function or editing its docstring changes the tool descriptor.
@server.tool()
def find_customer(query: str) -> dict:
    """Find customers whose name, email or customer id contains the query (case-insensitive).

    Returns up to 20 matches with their customer_id, name, email and city; use get_customer for
    the full record.
    """
    session = store.get()
    pattern = f"%{query}%"
    with session.lock:
        rows = session.data.execute(
            "SELECT customer_id, name, email, city FROM customers"
            " WHERE name LIKE ? OR email LIKE ? OR customer_id LIKE ?"
            " ORDER BY customer_id LIMIT 20",
            (pattern, pattern, pattern),
        ).fetchall()
    return {"query": query, "customers": [dict(row) for row in rows]}


@server.tool()
def get_customer(id: str) -> dict:
    """Get one customer's full record by customer_id (e.g. "c_001")."""
    session = store.get()
    with session.lock:
        return _customer(session.data, id)


@server.tool()
def list_orders(customer_id: str) -> dict:
    """List a customer's orders, oldest first."""
    session = store.get()
    with session.lock:
        _customer(session.data, customer_id)
        rows = session.data.execute(
            "SELECT * FROM orders WHERE customer_id = ? ORDER BY order_date, order_id",
            (customer_id,),
        ).fetchall()
    return {"customer_id": customer_id, "orders": [dict(row) for row in rows]}


@server.tool()
def update_customer(id: str, fields: dict[str, str]) -> dict:
    """Update fields on a customer's record and return the updated record.

    Updatable fields: name, email, phone, street, city, state, zip, tier, notes.
    """
    session = store.get()
    with session.lock:
        before = _customer(session.data, id)
        _update(session.data, id, fields)
        after = _customer(session.data, id)
        changes = {
            name: {"from": before[name], "to": after[name]}
            for name in sorted(fields)
            if before[name] != after[name]
        }
        session.record_effect("update_customer", customer_id=id, changes=changes)
    return after


def _customer(db: sqlite3.Connection, customer_id: str) -> dict:
    row = db.execute("SELECT * FROM customers WHERE customer_id = ?", (customer_id,)).fetchone()
    if row is None:
        raise ToolError(f"No customer with id {customer_id!r}.")
    return dict(row)


def _update(db: sqlite3.Connection, customer_id: str, fields: dict[str, Any]) -> None:
    unknown = set(fields) - UPDATABLE_FIELDS
    if unknown:
        raise ToolError(
            f"Cannot update {', '.join(sorted(unknown))}; "
            f"updatable fields are {', '.join(sorted(UPDATABLE_FIELDS))}."
        )
    _customer(db, customer_id)
    if fields:
        # Column names come only from UPDATABLE_FIELDS, checked above; values are bound.
        assignments = ", ".join(f"{name} = ?" for name in sorted(fields))
        db.execute(
            f"UPDATE customers SET {assignments} WHERE customer_id = ?",
            [str(fields[name]) for name in sorted(fields)] + [customer_id],
        )
        db.commit()


def main() -> None:
    """Serve the tools over HTTP, with the kit's control endpoint on PORT + 100."""
    serve(server, store, port=PORT)
