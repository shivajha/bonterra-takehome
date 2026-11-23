"""SQLite storage: connection, schema, transactions.

Requirements: NFR-2 (persistent local storage), WF-5 (atomic writes),
NFR-6 (failed actions leave state unchanged), §4 data model.
"""
import os
import sqlite3
from contextlib import contextmanager
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DB_PATH = PROJECT_ROOT / "data" / "cb.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS nonprofit (
    id   TEXT PRIMARY KEY,
    name TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS staff (
    id           TEXT PRIMARY KEY,
    nonprofit_id TEXT NOT NULL REFERENCES nonprofit(id),
    name         TEXT NOT NULL,
    role         TEXT NOT NULL CHECK (role IN ('CASE_WORKER', 'PROGRAM_MANAGER', 'FINANCE'))
);

CREATE TABLE IF NOT EXISTS participant (
    id           TEXT PRIMARY KEY,
    nonprofit_id TEXT NOT NULL REFERENCES nonprofit(id),
    name         TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS fund (
    id               TEXT PRIMARY KEY,
    nonprofit_id     TEXT NOT NULL REFERENCES nonprofit(id),
    name             TEXT NOT NULL,
    allocation_cents INTEGER NOT NULL CHECK (allocation_cents > 0),
    committed_cents  INTEGER NOT NULL DEFAULT 0 CHECK (committed_cents >= 0),
    paid_cents       INTEGER NOT NULL DEFAULT 0 CHECK (paid_cents >= 0),
    -- Final safety net against overspending (§3.2 invariant)
    CHECK (committed_cents + paid_cents <= allocation_cents)
);

CREATE TABLE IF NOT EXISTS request (
    id                TEXT PRIMARY KEY,
    nonprofit_id      TEXT NOT NULL REFERENCES nonprofit(id),
    participant_id    TEXT NOT NULL REFERENCES participant(id),
    fund_id           TEXT NOT NULL REFERENCES fund(id),
    category          TEXT NOT NULL CHECK (category IN ('RENT', 'UTILITIES', 'TRANSPORTATION', 'OTHER')),
    purpose           TEXT NOT NULL,
    amount_cents      INTEGER NOT NULL CHECK (amount_cents > 0),
    status            TEXT NOT NULL CHECK (status IN ('PENDING', 'APPROVED', 'REJECTED', 'PAID')),
    created_by        TEXT NOT NULL REFERENCES staff(id),
    created_at        TEXT NOT NULL,
    decided_by        TEXT REFERENCES staff(id),
    decided_at        TEXT,
    decision_reason   TEXT,
    paid_by           TEXT REFERENCES staff(id),
    paid_at           TEXT,
    client_request_id TEXT,
    UNIQUE (nonprofit_id, created_by, client_request_id)
);
CREATE INDEX IF NOT EXISTS idx_request_np ON request (nonprofit_id, created_at);

CREATE TABLE IF NOT EXISTS audit_event (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    nonprofit_id TEXT NOT NULL,
    request_id   TEXT NOT NULL,
    actor_id     TEXT NOT NULL,
    action       TEXT NOT NULL,
    from_status  TEXT,
    to_status    TEXT NOT NULL,
    at           TEXT NOT NULL
);
"""


def get_db_path() -> Path:
    """DB file from CB_DB_PATH, else <project>/data/cb.db."""
    return Path(os.environ.get("CB_DB_PATH", DEFAULT_DB_PATH))


def connect(path=None) -> sqlite3.Connection:
    """Open a connection. Autocommit mode: we start transactions ourselves."""
    db_path = Path(path) if path else get_db_path()
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path, isolation_level=None, timeout=5.0, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 5000")
    conn.execute("PRAGMA journal_mode = WAL")
    return conn


def init_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)


@contextmanager
def write_transaction(conn: sqlite3.Connection):
    """BEGIN IMMEDIATE takes the write lock up front, so competing writers
    are serialized (WF-5). Any exception rolls everything back (NFR-6)."""
    conn.execute("BEGIN IMMEDIATE")
    try:
        yield conn
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    else:
        conn.execute("COMMIT")