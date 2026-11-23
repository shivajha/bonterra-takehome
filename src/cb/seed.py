"""Seed / reset command (NFR-3). Separate from normal server startup.

Usage:
    python -m cb.seed --reset      # delete the DB and recreate it with synthetic data
    python -m cb.seed              # create only if the DB does not exist yet
"""
import argparse
import sys
from pathlib import Path

from cb.db import connect, get_db_path, init_schema, write_transaction

NONPROFITS = [
    ("np-cb", "Community Bridge"),
    ("np-hh", "Harbor Help"),
]

# Fixture staff identities: one per role per nonprofit (AUTH-1).
STAFF = [
    ("cb-caseworker", "np-cb", "Asha Rao", "CASE_WORKER"),
    ("cb-manager", "np-cb", "Ben Ortiz", "PROGRAM_MANAGER"),
    ("cb-finance", "np-cb", "Chen Li", "FINANCE"),
    ("hh-caseworker", "np-hh", "Dana Kim", "CASE_WORKER"),
    ("hh-manager", "np-hh", "Eli Moss", "PROGRAM_MANAGER"),
    ("hh-finance", "np-hh", "Fay Nair", "FINANCE"),
]

PARTICIPANTS = [
    ("part-cb-001", "np-cb", "Maria Lopez"),
    ("part-cb-002", "np-cb", "James Carter"),
    ("part-cb-003", "np-cb", "Priya Shah"),
    ("part-hh-001", "np-hh", "Tom Becker"),
    ("part-hh-002", "np-hh", "Aisha Bello"),
]

# Allocations in integer cents.
FUNDS = [
    ("fund-cb-rent", "np-cb", "City Rent Relief Grant", 1_000_000),       # $10,000.00
    ("fund-cb-utility", "np-cb", "Utility Assistance Grant", 50_000),      # $500.00
    ("fund-hh-transit", "np-hh", "Transit Access Grant", 200_000),         # $2,000.00
]


def seed(conn) -> None:
    """Create the schema and insert synthetic data into an empty database."""
    init_schema(conn)
    with write_transaction(conn):
        conn.executemany("INSERT INTO nonprofit (id, name) VALUES (?, ?)", NONPROFITS)
        conn.executemany("INSERT INTO staff (id, nonprofit_id, name, role) VALUES (?, ?, ?, ?)", STAFF)
        conn.executemany("INSERT INTO participant (id, nonprofit_id, name) VALUES (?, ?, ?)", PARTICIPANTS)
        conn.executemany(
            "INSERT INTO fund (id, nonprofit_id, name, allocation_cents) VALUES (?, ?, ?, ?)", FUNDS
        )


def delete_db(path: Path) -> None:
    for p in (path, Path(f"{path}-wal"), Path(f"{path}-shm")):
        if p.exists():
            p.unlink()


def reset(path=None) -> Path:
    """Delete the database file and recreate it with seed data. Repeatable."""
    db_path = Path(path) if path else get_db_path()
    delete_db(db_path)
    conn = connect(db_path)
    try:
        seed(conn)
    finally:
        conn.close()
    return db_path


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Create or reset the Community Bridge database.")
    parser.add_argument("--reset", action="store_true", help="delete existing data and reseed")
    args = parser.parse_args(argv)

    db_path = get_db_path()
    if db_path.exists() and not args.reset:
        print(f"Database already exists at {db_path}. Use --reset to wipe and reseed.", file=sys.stderr)
        return 1
    reset(db_path)
    print(f"Seeded database at {db_path}: {len(NONPROFITS)} nonprofits, {len(STAFF)} staff, "
          f"{len(PARTICIPANTS)} participants, {len(FUNDS)} funds.")
    return 0


if __name__ == "__main__":
    sys.exit(main())