"""Caller identity and role checks.

Requirements: AUTH-1 (identity resolved on the server at startup),
AUTH-2 (role/nonprofit come only from the staff table), AUTH-3 (role matrix).
"""
import os
from dataclasses import dataclass

from cb.errors import FORBIDDEN, ServiceError

CASE_WORKER = "CASE_WORKER"
PROGRAM_MANAGER = "PROGRAM_MANAGER"
FINANCE = "FINANCE"
ALL_ROLES = (CASE_WORKER, PROGRAM_MANAGER, FINANCE)


@dataclass(frozen=True)
class Actor:
    """The authenticated caller. Immutable: nothing in a tool call can change it."""

    staff_id: str
    name: str
    role: str
    nonprofit_id: str
    nonprofit_name: str


class IdentityError(Exception):
    """Missing or unknown identity. The server must not start (AUTH-1, AC-13)."""


def load_actor(conn, staff_id) -> Actor:
    if not staff_id or not str(staff_id).strip():
        raise IdentityError("CB_STAFF_ID is not set. Configure a fixture staff identity, e.g. CB_STAFF_ID=cb-caseworker.")
    row = conn.execute(
        """SELECT s.id, s.name, s.role, s.nonprofit_id, n.name AS nonprofit_name
             FROM staff s JOIN nonprofit n ON n.id = s.nonprofit_id
            WHERE s.id = ?""",
        (str(staff_id).strip(),),
    ).fetchone()
    if row is None:
        raise IdentityError(f"Unknown staff identity '{staff_id}'.")
    return Actor(row["id"], row["name"], row["role"], row["nonprofit_id"], row["nonprofit_name"])


def load_actor_from_env(conn) -> Actor:
    return load_actor(conn, os.environ.get("CB_STAFF_ID"))


def require_role(actor: Actor, *allowed: str) -> None:
    """Checked first in every tool, before any lookup, so unauthorized callers learn nothing."""
    if actor.role not in allowed:
        raise ServiceError(FORBIDDEN, f"Role {actor.role} is not allowed to perform this action.",
                           required_roles=list(allowed))