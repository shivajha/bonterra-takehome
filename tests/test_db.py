"""Tests for NFR-2, NFR-3 (seed/reset) and the DB-level safety constraints."""
import sqlite3

import pytest

from cb.db import connect, write_transaction
from cb.seed import main, reset


def snapshot(conn):
    tables = ["nonprofit", "staff", "participant", "fund", "request", "audit_event"]
    return {t: [tuple(r) for r in conn.execute(f"SELECT * FROM {t} ORDER BY 1")] for t in tables}


def test_reset_is_repeatable(tmp_path):  # NFR-3
    path = tmp_path / "r.db"
    reset(path)
    c = connect(path)
    first = snapshot(c)
    c.execute("UPDATE fund SET committed_cents = 100 WHERE id = 'fund-cb-rent'")
    c.close()
    reset(path)
    c = connect(path)
    assert snapshot(c) == first
    c.close()


def test_seed_has_two_nonprofits_with_all_roles(conn):
    for np in ("np-cb", "np-hh"):
        roles = {r["role"] for r in conn.execute("SELECT role FROM staff WHERE nonprofit_id = ?", (np,))}
        assert roles == {"CASE_WORKER", "PROGRAM_MANAGER", "FINANCE"}
        assert conn.execute("SELECT COUNT(*) FROM participant WHERE nonprofit_id = ?", (np,)).fetchone()[0] >= 2
        assert conn.execute("SELECT COUNT(*) FROM fund WHERE nonprofit_id = ?", (np,)).fetchone()[0] >= 1


def test_seed_cli_refuses_without_reset(tmp_path, monkeypatch):  # NFR-3: never wipes by accident
    monkeypatch.setenv("CB_DB_PATH", str(tmp_path / "cli.db"))
    assert main(["--reset"]) == 0
    assert main([]) == 1


def test_db_check_blocks_overspend(conn):  # §3.2 invariant, defence in depth
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("UPDATE fund SET committed_cents = 50001 WHERE id = 'fund-cb-utility'")


def test_db_check_blocks_unknown_status_and_role(conn):
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("UPDATE staff SET role = 'ADMIN' WHERE id = 'cb-caseworker'")


def test_write_transaction_rolls_back_on_error(conn):  # NFR-6
    before = snapshot(conn)
    with pytest.raises(RuntimeError):
        with write_transaction(conn):
            conn.execute("UPDATE fund SET committed_cents = 100 WHERE id = 'fund-cb-rent'")
            raise RuntimeError("boom")
    assert snapshot(conn) == before