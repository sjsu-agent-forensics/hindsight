"""Seeded generator for the fake customer database.

Every value comes from `random.Random(seed)` and the fixed word lists below, with no third-party
faker, so the same seed gives the same people on every machine and every library version (N-07).
The people are synthetic (N-06): emails use the reserved example.* domains and phone numbers the
fictional 555-01xx range.
"""

import argparse
import sqlite3
from datetime import date, timedelta
from pathlib import Path
from random import Random

DEFAULT_SEED = 295
DEFAULT_CUSTOMERS = 50

FIRST_NAMES = [
    "Alice", "Ben", "Carmen", "Daniel", "Elena", "Farid", "Grace", "Hiro", "Isabel", "Jamal",
    "Kavya", "Liam", "Mei", "Noah", "Olivia", "Priya", "Quentin", "Rosa", "Samuel", "Tara",
    "Umar", "Valeria", "Wei", "Ximena", "Yusuf", "Zoe", "Aaron", "Bianca", "Chen", "Diego",
    "Esther", "Felix", "Gabriela", "Hassan", "Ingrid", "Jonah", "Keiko", "Luis", "Maya", "Nikhil",
]  # fmt: skip
LAST_NAMES = [
    "Anderson", "Bautista", "Chen", "Dimitrov", "Esposito", "Fernandez", "Gupta", "Hoang",
    "Ibrahim", "Jensen", "Kowalski", "Lopez", "Morales", "Nakamura", "Okafor", "Patel", "Quinn",
    "Ramirez", "Singh", "Tanaka", "Underwood", "Vasquez", "Williams", "Xu", "Yamamoto", "Zhang",
    "Abbott", "Brennan", "Castillo", "Duarte", "Eriksen", "Fischer", "Garcia", "Haddad", "Ivanova",
]  # fmt: skip
STREETS = [
    "Almaden Blvd", "Bascom Ave", "Calaveras Blvd", "Dana St", "El Camino Real", "First St",
    "Grant Rd", "Hamilton Ave", "Lincoln Ave", "Meridian Ave", "Park Ave", "Santa Clara St",
    "Saratoga Ave", "Stevens Creek Blvd", "Tully Rd", "Winchester Blvd",
]  # fmt: skip
CITIES = [
    ("San Jose", "95112"), ("Santa Clara", "95050"), ("Sunnyvale", "94086"),
    ("Mountain View", "94041"), ("Campbell", "95008"), ("Milpitas", "95035"),
    ("Cupertino", "95014"), ("Palo Alto", "94301"), ("Fremont", "94538"), ("Los Gatos", "95030"),
]  # fmt: skip
EMAIL_DOMAINS = ["example.com", "example.net", "example.org"]
TIERS = ["standard", "standard", "standard", "gold", "platinum"]
ITEMS = [
    ("Wireless keyboard", 49.99), ("USB-C hub", 34.50), ("Noise-cancelling headphones", 199.00),
    ("27-inch monitor", 289.99), ("Laptop stand", 39.95), ("Webcam", 74.00),
    ("External SSD 1TB", 109.99), ("Desk lamp", 28.75), ("Ergonomic mouse", 59.00),
    ("Office chair", 349.00), ("Cable organizer", 12.49), ("Portable charger", 45.00),
]  # fmt: skip
ORDER_STATUSES = ["delivered", "delivered", "delivered", "shipped", "processing", "returned"]
NOTES = [
    "Prefers email contact.",
    "Asked about bulk pricing last quarter.",
    "Called support about a late delivery; resolved.",
    "Long-time customer; offer loyalty discount on renewal.",
    "Requested invoices be sent to their accounting team.",
    "",
    "",
    "",
]

SCHEMA = """
CREATE TABLE customers (
    customer_id    TEXT PRIMARY KEY,
    name           TEXT NOT NULL,
    email          TEXT NOT NULL,
    phone          TEXT NOT NULL,
    street         TEXT NOT NULL,
    city           TEXT NOT NULL,
    state          TEXT NOT NULL,
    zip            TEXT NOT NULL,
    account_number TEXT NOT NULL,
    tier           TEXT NOT NULL,
    customer_since TEXT NOT NULL,
    notes          TEXT NOT NULL
);
CREATE TABLE orders (
    order_id    TEXT PRIMARY KEY,
    customer_id TEXT NOT NULL REFERENCES customers(customer_id),
    order_date  TEXT NOT NULL,
    item        TEXT NOT NULL,
    quantity    INTEGER NOT NULL,
    total       REAL NOT NULL,
    status      TEXT NOT NULL
);
"""


def generate(seed: int = DEFAULT_SEED, customers: int = DEFAULT_CUSTOMERS) -> sqlite3.Connection:
    """Build the customer database in memory and return the open connection.

    Customer ids run `c_001`, `c_002`, ... and order ids `o_0001`, ... in generation order, so a
    given seed and count always assign the same id to the same person.
    """
    rng = Random(seed)
    db = sqlite3.connect(":memory:", check_same_thread=False)
    db.row_factory = sqlite3.Row
    db.executescript(SCHEMA)

    order_no = 0
    used_emails: set[str] = set()
    for n in range(1, customers + 1):
        first, last = rng.choice(FIRST_NAMES), rng.choice(LAST_NAMES)
        email = f"{first}.{last}".lower()
        if email in used_emails:
            email = f"{email}{n}"
        used_emails.add(email)
        city, zip_code = rng.choice(CITIES)
        since = date(2018, 1, 1) + timedelta(days=rng.randrange(8 * 365))
        customer_id = f"c_{n:03d}"
        db.execute(
            "INSERT INTO customers VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                customer_id,
                f"{first} {last}",
                f"{email}@{rng.choice(EMAIL_DOMAINS)}",
                f"408-555-01{rng.randrange(100):02d}",
                f"{rng.randrange(100, 9999)} {rng.choice(STREETS)}",
                city,
                "CA",
                zip_code,
                "".join(str(rng.randrange(10)) for _ in range(10)),
                rng.choice(TIERS),
                since.isoformat(),
                rng.choice(NOTES),
            ),
        )
        for _ in range(rng.randrange(6)):
            order_no += 1
            item, price = rng.choice(ITEMS)
            quantity = rng.choice([1, 1, 1, 2, 3])
            ordered = since + timedelta(days=rng.randrange((date(2026, 10, 31) - since).days))
            db.execute(
                "INSERT INTO orders VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    f"o_{order_no:04d}",
                    customer_id,
                    ordered.isoformat(),
                    item,
                    quantity,
                    round(price * quantity, 2),
                    rng.choice(ORDER_STATUSES),
                ),
            )
    db.commit()
    return db


def main() -> None:
    """Write a generated database to a file, for inspection or for a fixture's `database` key."""
    parser = argparse.ArgumentParser(prog="records-generate", description=__doc__.splitlines()[0])
    parser.add_argument("out", type=Path, help="SQLite file to write (replaced if it exists)")
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    parser.add_argument("--customers", type=int, default=DEFAULT_CUSTOMERS)
    args = parser.parse_args()

    args.out.unlink(missing_ok=True)
    with sqlite3.connect(args.out) as out:
        generate(args.seed, args.customers).backup(out)
    out.close()
    print(f"Wrote {args.customers} customers (seed {args.seed}) to {args.out}")
