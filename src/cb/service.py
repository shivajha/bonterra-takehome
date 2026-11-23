"""Business logic. Every function takes the DB connection and the server-resolved Actor.

Check order in every tool (spec §3.3): role -> input validation -> tenant lookup
-> state / funds check -> write. All writes run inside one write_transaction,
so any ServiceError leaves business state unchanged (NFR-6).
"""
import secrets
from datetime import datetime, timezone

from cb.auth import ALL_ROLES, CASE_WORKER, FINANCE, PROGRAM_MANAGER, Actor, require_role
from cb.db import write_transaction
from cb.errors import (CONFLICT, INSUFFICIENT_FUNDS, INVALID_TRANSITION, NOT_FOUND,
                       VALIDATION_ERROR, ServiceError)
from cb.money import MoneyError, format_cents, parse_amount

CATEGORIES = ("RENT", "UTILITIES", "TRANSPORTATION", "OTHER")
STATUSES = ("PENDING", "APPROVED", "REJECTED", "PAID")
MAX_LIST = 200


# ---------- helpers ----------

def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


def _check_id(value, field: str) -> str:
    if not isinstance(value, str) or not value.strip() or len(value.strip()) > 64:
        raise ServiceError(VALIDATION_ERROR, f"{field} must be a non-empty string of at most 64 characters.",
                           field=field)
    return value.strip()


def _check_text(value, field: str, max_len: int, required: bool):
    if value is None:
        if required:
            raise ServiceError(VALIDATION_ERROR, f"{field} is required.", field=field)
        return None
    if not isinstance(value, str):
        raise ServiceError(VALIDATION_ERROR, f"{field} must be a string.", field=field)
    text = value.strip()
    if not text:
        if required:
            raise ServiceError(VALIDATION_ERROR, f"{field} must not be empty.", field=field)
        return None  # spec v1.1: empty reason stored as null
    if len(text) > max_len:
        raise ServiceError(VALIDATION_ERROR, f"{field} must be at most {max_len} characters.", field=field)
    return text


def _fund_dict(row) -> dict:
    available = row["allocation_cents"] - row["committed_cents"] - row["paid_cents"]
    return {
        "fund_id": row["id"],
        "name": row["name"],
        "allocation": format_cents(row["allocation_cents"]),
        "committed": format_cents(row["committed_cents"]),
        "paid": format_cents(row["paid_cents"]),
        "available": format_cents(available),
        "allocation_cents": row["allocation_cents"],
        "committed_cents": row["committed_cents"],
        "paid_cents": row["paid_cents"],
        "available_cents": available,
    }


def _request_dict(row) -> dict:
    return {
        "id": row["id"],
        "participant_id": row["participant_id"],
        "fund_id": row["fund_id"],
        "category": row["category"],
        "purpose": row["purpose"],
        "amount": format_cents(row["amount_cents"]),
        "amount_cents": row["amount_cents"],
        "status": row["status"],
        "created_by": row["created_by"],
        "created_at": row["created_at"],
        "decided_by": row["decided_by"],
        "decided_at": row["decided_at"],
        "decision_reason": row["decision_reason"],
        "paid_by": row["paid_by"],
        "paid_at": row["paid_at"],
    }


def _get_request_row(conn, actor: Actor, request_id: str):
    # AUTH-4: always filtered by the caller's nonprofit; other tenants' rows look non-existent.
    row = conn.execute("SELECT * FROM request WHERE id = ? AND nonprofit_id = ?",
                       (request_id, actor.nonprofit_id)).fetchone()
    if row is None:
        raise ServiceError(NOT_FOUND, f"Request '{request_id}' not found.", request_id=request_id)
    return row


def _get_fund_row(conn, actor: Actor, fund_id: str):
    row = conn.execute("SELECT * FROM fund WHERE id = ? AND nonprofit_id = ?",
                       (fund_id, actor.nonprofit_id)).fetchone()
    if row is None:
        raise ServiceError(NOT_FOUND, f"Fund '{fund_id}' not found.", fund_id=fund_id)
    return row


def _audit(conn, actor: Actor, request_id: str, action: str, from_status, to_status: str, at: str) -> None:
    # WF-6: written inside the same transaction as the state change.
    conn.execute(
        """INSERT INTO audit_event (nonprofit_id, request_id, actor_id, action, from_status, to_status, at)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (actor.nonprofit_id, request_id, actor.staff_id, action, from_status, to_status, at),
    )


def _invalid_transition(action: str, current: str) -> ServiceError:
    return ServiceError(INVALID_TRANSITION, f"Cannot {action} a request in status {current}.",
                        current_status=current)


# ---------- read tools (all roles) ----------

def whoami(actor: Actor) -> dict:
    return {"staff_id": actor.staff_id, "name": actor.name, "role": actor.role,
            "nonprofit_id": actor.nonprofit_id, "nonprofit_name": actor.nonprofit_name}


def list_participants(conn, actor: Actor) -> dict:  # LKP-1
    require_role(actor, *ALL_ROLES)
    rows = conn.execute("SELECT id, name FROM participant WHERE nonprofit_id = ? ORDER BY id",
                        (actor.nonprofit_id,)).fetchall()
    return {"participants": [{"id": r["id"], "name": r["name"]} for r in rows]}


def list_funds(conn, actor: Actor) -> dict:  # LKP-2
    require_role(actor, *ALL_ROLES)
    rows = conn.execute("SELECT * FROM fund WHERE nonprofit_id = ? ORDER BY id",
                        (actor.nonprofit_id,)).fetchall()
    return {"funds": [_fund_dict(r) for r in rows]}


def get_request(conn, actor: Actor, request_id) -> dict:  # RPT-1
    require_role(actor, *ALL_ROLES)
    request_id = _check_id(request_id, "request_id")
    return {"request": _request_dict(_get_request_row(conn, actor, request_id))}


def list_requests(conn, actor: Actor, status=None, fund_id=None, participant_id=None) -> dict:  # RPT-2
    require_role(actor, *ALL_ROLES)
    sql = "SELECT * FROM request WHERE nonprofit_id = ?"
    params = [actor.nonprofit_id]
    if status is not None:
        if status not in STATUSES:
            raise ServiceError(VALIDATION_ERROR, f"status must be one of {list(STATUSES)}.", field="status")
        sql += " AND status = ?"
        params.append(status)
    if fund_id is not None:
        sql += " AND fund_id = ?"  # unknown / other-tenant IDs simply match nothing (spec v1.1)
        params.append(_check_id(fund_id, "fund_id"))
    if participant_id is not None:
        sql += " AND participant_id = ?"
        params.append(_check_id(participant_id, "participant_id"))
    sql += f" ORDER BY created_at DESC, id DESC LIMIT {MAX_LIST}"
    return {"requests": [_request_dict(r) for r in conn.execute(sql, params).fetchall()]}


def get_funding_summary(conn, actor: Actor, fund_id=None) -> dict:  # RPT-3
    require_role(actor, *ALL_ROLES)
    if fund_id is not None:
        funds = [_get_fund_row(conn, actor, _check_id(fund_id, "fund_id"))]
    else:
        funds = conn.execute("SELECT * FROM fund WHERE nonprofit_id = ? ORDER BY id",
                             (actor.nonprofit_id,)).fetchall()
    result = []
    for f in funds:
        counts = {s: 0 for s in STATUSES}
        for r in conn.execute(
            "SELECT status, COUNT(*) AS n FROM request WHERE fund_id = ? AND nonprofit_id = ? GROUP BY status",
            (f["id"], actor.nonprofit_id),
        ):
            counts[r["status"]] = r["n"]
        result.append({**_fund_dict(f), "counts_by_status": counts})
    return {"funds": result}


# ---------- write tools ----------

def create_request(conn, actor: Actor, participant_id, fund_id, category, purpose, amount,
                   client_request_id=None) -> dict:  # REQ-1..REQ-5
    require_role(actor, CASE_WORKER)
    participant_id = _check_id(participant_id, "participant_id")
    fund_id = _check_id(fund_id, "fund_id")
    if category not in CATEGORIES:
        raise ServiceError(VALIDATION_ERROR, f"category must be one of {list(CATEGORIES)}.", field="category")
    purpose = _check_text(purpose, "purpose", 200, required=True)
    try:
        amount_cents = parse_amount(amount)
    except MoneyError as e:
        raise ServiceError(VALIDATION_ERROR, str(e), field="amount") from None
    if client_request_id is not None:
        client_request_id = _check_id(client_request_id, "client_request_id")

    with write_transaction(conn):
        if conn.execute("SELECT 1 FROM participant WHERE id = ? AND nonprofit_id = ?",
                        (participant_id, actor.nonprofit_id)).fetchone() is None:
            raise ServiceError(NOT_FOUND, f"Participant '{participant_id}' not found.", participant_id=participant_id)
        _get_fund_row(conn, actor, fund_id)

        if client_request_id is not None:  # REQ-5 safe retry
            existing = conn.execute(
                "SELECT * FROM request WHERE nonprofit_id = ? AND created_by = ? AND client_request_id = ?",
                (actor.nonprofit_id, actor.staff_id, client_request_id),
            ).fetchone()
            if existing is not None:
                same = (existing["participant_id"], existing["fund_id"], existing["category"],
                        existing["purpose"], existing["amount_cents"]) == (
                        participant_id, fund_id, category, purpose, amount_cents)
                if not same:
                    raise ServiceError(CONFLICT, "client_request_id was already used with a different payload.",
                                       client_request_id=client_request_id, request_id=existing["id"])
                return {"request": _request_dict(existing), "replayed": True}

        request_id = "req_" + secrets.token_hex(6)
        now = _now()
        # REQ-3: created as PENDING; no fund balance changes.
        conn.execute(
            """INSERT INTO request (id, nonprofit_id, participant_id, fund_id, category, purpose, amount_cents,
                                    status, created_by, created_at, client_request_id)
               VALUES (?, ?, ?, ?, ?, ?, ?, 'PENDING', ?, ?, ?)""",
            (request_id, actor.nonprofit_id, participant_id, fund_id, category, purpose, amount_cents,
             actor.staff_id, now, client_request_id),
        )
        _audit(conn, actor, request_id, "CREATED", None, "PENDING", now)
        row = _get_request_row(conn, actor, request_id)
    return {"request": _request_dict(row), "replayed": False}


def approve_request(conn, actor: Actor, request_id) -> dict:  # WF-1, WF-4, WF-5
    require_role(actor, PROGRAM_MANAGER)
    request_id = _check_id(request_id, "request_id")
    with write_transaction(conn):  # BEGIN IMMEDIATE: competing approvals are serialized
        req = _get_request_row(conn, actor, request_id)
        if req["status"] != "PENDING":
            raise _invalid_transition("approve", req["status"])
        amount = req["amount_cents"]
        # Check-and-reserve in ONE conditional statement: cannot overspend.
        cur = conn.execute(
            """UPDATE fund SET committed_cents = committed_cents + ?
                WHERE id = ? AND nonprofit_id = ?
                  AND allocation_cents - committed_cents - paid_cents >= ?""",
            (amount, req["fund_id"], actor.nonprofit_id, amount),
        )
        if cur.rowcount != 1:
            fund = _get_fund_row(conn, actor, req["fund_id"])
            available = fund["allocation_cents"] - fund["committed_cents"] - fund["paid_cents"]
            raise ServiceError(INSUFFICIENT_FUNDS,
                               f"Fund has {format_cents(available)} available; request needs {format_cents(amount)}.",
                               available=format_cents(available), requested=format_cents(amount))
        now = _now()
        cur = conn.execute(
            """UPDATE request SET status = 'APPROVED', decided_by = ?, decided_at = ?
                WHERE id = ? AND nonprofit_id = ? AND status = 'PENDING'""",
            (actor.staff_id, now, request_id, actor.nonprofit_id),
        )
        if cur.rowcount != 1:  # defensive: rolls back the fund update too
            raise _invalid_transition("approve", req["status"])
        _audit(conn, actor, request_id, "APPROVED", "PENDING", "APPROVED", now)
        req = _get_request_row(conn, actor, request_id)
        fund = _get_fund_row(conn, actor, req["fund_id"])
    return {"request": _request_dict(req), "fund": _fund_dict(fund)}


def reject_request(conn, actor: Actor, request_id, reason=None) -> dict:  # WF-2, WF-4
    require_role(actor, PROGRAM_MANAGER)
    request_id = _check_id(request_id, "request_id")
    reason = _check_text(reason, "reason", 500, required=False)
    with write_transaction(conn):
        req = _get_request_row(conn, actor, request_id)
        if req["status"] != "PENDING":
            raise _invalid_transition("reject", req["status"])
        now = _now()
        conn.execute(
            """UPDATE request SET status = 'REJECTED', decided_by = ?, decided_at = ?, decision_reason = ?
                WHERE id = ? AND nonprofit_id = ? AND status = 'PENDING'""",
            (actor.staff_id, now, reason, request_id, actor.nonprofit_id),
        )
        _audit(conn, actor, request_id, "REJECTED", "PENDING", "REJECTED", now)
        req = _get_request_row(conn, actor, request_id)
    return {"request": _request_dict(req)}


def record_payment(conn, actor: Actor, request_id) -> dict:  # WF-3, WF-4
    require_role(actor, FINANCE)
    request_id = _check_id(request_id, "request_id")
    with write_transaction(conn):
        req = _get_request_row(conn, actor, request_id)
        if req["status"] != "APPROVED":
            raise _invalid_transition("pay", req["status"])
        amount = req["amount_cents"]
        now = _now()
        cur = conn.execute(
            """UPDATE request SET status = 'PAID', paid_by = ?, paid_at = ?
                WHERE id = ? AND nonprofit_id = ? AND status = 'APPROVED'""",
            (actor.staff_id, now, request_id, actor.nonprofit_id),
        )
        if cur.rowcount != 1:
            raise _invalid_transition("pay", req["status"])
        # Move committed -> paid. Available is unchanged.
        conn.execute(
            """UPDATE fund SET committed_cents = committed_cents - ?, paid_cents = paid_cents + ?
                WHERE id = ? AND nonprofit_id = ?""",
            (amount, amount, req["fund_id"], actor.nonprofit_id),
        )
        _audit(conn, actor, request_id, "PAID", "APPROVED", "PAID", now)
        req = _get_request_row(conn, actor, request_id)
        fund = _get_fund_row(conn, actor, req["fund_id"])
    return {"request": _request_dict(req), "fund": _fund_dict(fund)}