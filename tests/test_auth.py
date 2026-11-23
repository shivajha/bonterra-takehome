"""AUTH-1 / AC-13: identity is resolved on the server; missing or unknown is rejected."""
import pytest

from cb.auth import IdentityError, load_actor, load_actor_from_env


@pytest.mark.parametrize("bad", [None, "", "   "])
def test_missing_identity_rejected(conn, bad):
    with pytest.raises(IdentityError, match="CB_STAFF_ID"):
        load_actor(conn, bad)


def test_unknown_identity_rejected(conn):
    with pytest.raises(IdentityError, match="Unknown"):
        load_actor(conn, "cb-admin")


def test_identity_resolves_role_and_nonprofit_from_db(conn):
    a = load_actor(conn, "hh-finance")
    assert (a.role, a.nonprofit_id) == ("FINANCE", "np-hh")


def test_env_identity(conn, monkeypatch):
    monkeypatch.setenv("CB_STAFF_ID", "cb-manager")
    assert load_actor_from_env(conn).role == "PROGRAM_MANAGER"
    monkeypatch.delenv("CB_STAFF_ID")
    with pytest.raises(IdentityError):
        load_actor_from_env(conn)