"""Shared fixtures: every test gets its own fresh, seeded database file."""
import pytest

from cb.db import connect
from cb.seed import reset


@pytest.fixture
def db_path(tmp_path):
    return reset(tmp_path / "test.db")


@pytest.fixture
def conn(db_path):
    c = connect(db_path)
    yield c
    c.close()


from cb.auth import load_actor


@pytest.fixture
def actors(conn):
    """All six fixture identities, resolved exactly like the server does."""
    ids = ["cb-caseworker", "cb-manager", "cb-finance", "hh-caseworker", "hh-manager", "hh-finance"]
    return {i: load_actor(conn, i) for i in ids}