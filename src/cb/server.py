"""Community Bridge MCP server (stdio).

Start:  CB_STAFF_ID=cb-caseworker python -m cb.server
Identity is resolved ONCE at startup from CB_STAFF_ID (AUTH-1). Missing/unknown -> exit code 2.
stdout is reserved for the MCP protocol; all logs go to stderr (NFR-4).
"""
import json
import logging
import os
import sys
import threading
from typing import Annotated, Literal, Optional

from pydantic import Field

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError

from cb import models as m
from cb import service as svc
from cb.auth import Actor, IdentityError, load_actor_from_env
from cb.db import connect, get_db_path
from cb.errors import INTERNAL, ServiceError

log = logging.getLogger("cb.server")

INSTRUCTIONS = (
    "Emergency assistance funding for one nonprofit. The caller's identity, role and nonprofit are fixed "
    "by the server (see whoami) and cannot be changed by arguments. Amounts are USD strings like '300.00'. "
    "Workflow: case worker create_request -> program manager approve_request/reject_request -> "
    "finance record_payment. Errors are JSON: {error: {code, message, details}}."
)

mcp = MCPServer(name="community-bridge", instructions=INSTRUCTIONS, version="0.1.0")

# Set in main() before the server starts.
_state: dict = {}
_lock = threading.Lock()  # one shared SQLite connection per server process

ID = Annotated[str, Field(description="Record ID, e.g. 'req_ab12cd34ef56'", max_length=64)]


def _call(fn, *args, **kwargs):
    """Run a service function with the server-resolved Actor; map business errors to MCP tool errors."""
    conn, actor = _state["conn"], _state["actor"]
    try:
        with _lock:
            return fn(conn, actor, *args, **kwargs)
    except ServiceError as e:
        log.info("tool error %s by %s: %s", e.code, actor.staff_id, e.message)
        raise ToolError(json.dumps(e.to_dict())) from None
    except Exception:  # noqa: BLE001 - never leak internals to the client
        log.exception("unexpected error")
        raise ToolError(json.dumps({"error": {"code": INTERNAL, "message": "Internal error", "details": {}}})) from None


READ = {"readOnlyHint": True}


@mcp.tool(description="Return the caller's staff identity, role and nonprofit (resolved by the server).",
          annotations=READ)
def whoami() -> m.Identity:
    return m.Identity(**svc.whoami(_state["actor"]))


@mcp.tool(description="List participants of the caller's nonprofit.", annotations=READ)
def list_participants() -> m.ParticipantList:
    return m.ParticipantList(**_call(svc.list_participants))


@mcp.tool(description="List funds of the caller's nonprofit with allocation, committed, paid and available amounts.",
          annotations=READ)
def list_funds() -> m.FundList:
    return m.FundList(**_call(svc.list_funds))


@mcp.tool(description=(
    "CASE_WORKER only. Submit an assistance request for an existing participant. Starts PENDING and does "
    "NOT reserve money. Pass client_request_id to make retries safe (same key + same payload returns the "
    "original request)."))
def create_request(
    participant_id: ID,
    fund_id: ID,
    category: Literal["RENT", "UTILITIES", "TRANSPORTATION", "OTHER"],
    purpose: Annotated[str, Field(description="Short description, 1-200 characters")],
    amount: Annotated[str, Field(description="USD amount as a string, > 0, max 2 decimals, e.g. '300.00'")],
    client_request_id: Annotated[Optional[str], Field(description="Optional idempotency key", max_length=64)] = None,
) -> m.CreateResult:
    return m.CreateResult(**_call(svc.create_request, participant_id, fund_id, category, purpose, amount,
                                  client_request_id=client_request_id))


@mcp.tool(description=(
    "PROGRAM_MANAGER only. Approve a PENDING request: atomically reserves the full amount against its fund "
    "(committed += amount). Fails with INSUFFICIENT_FUNDS if the fund cannot cover it."))
def approve_request(request_id: ID) -> m.RequestWithFund:
    return m.RequestWithFund(**_call(svc.approve_request, request_id))


@mcp.tool(description="PROGRAM_MANAGER only. Reject a PENDING request. Funding is unchanged.")
def reject_request(
    request_id: ID,
    reason: Annotated[Optional[str], Field(description="Optional reason, max 500 characters")] = None,
) -> m.RequestResult:
    return m.RequestResult(**_call(svc.reject_request, request_id, reason=reason))


@mcp.tool(description=(
    "FINANCE only. Record a simulated payment for the full amount of an APPROVED request "
    "(committed -> paid; available unchanged). Succeeds only once per request."))
def record_payment(request_id: ID) -> m.RequestWithFund:
    return m.RequestWithFund(**_call(svc.record_payment, request_id))


@mcp.tool(description="Get one request of the caller's nonprofit by ID.", annotations=READ)
def get_request(request_id: ID) -> m.RequestResult:
    return m.RequestResult(**_call(svc.get_request, request_id))


@mcp.tool(description="List requests of the caller's nonprofit, newest first (max 200), with optional filters.",
          annotations=READ)
def list_requests(
    status: Optional[Literal["PENDING", "APPROVED", "REJECTED", "PAID"]] = None,
    fund_id: Optional[ID] = None,
    participant_id: Optional[ID] = None,
) -> m.RequestList:
    return m.RequestList(**_call(svc.list_requests, status=status, fund_id=fund_id, participant_id=participant_id))


@mcp.tool(description="Funding summary per fund (allocation, committed, paid, available, counts by status). "
                      "Omit fund_id for all funds.", annotations=READ)
def get_funding_summary(fund_id: Optional[ID] = None) -> m.FundSummaryList:
    return m.FundSummaryList(**_call(svc.get_funding_summary, fund_id))


def setup_logging() -> None:
    handlers = [logging.StreamHandler(sys.stderr)]  # never stdout
    log_file = os.environ.get("CB_LOG_FILE")
    if log_file:
        handlers.append(logging.FileHandler(log_file, encoding="utf-8"))
    logging.basicConfig(level=os.environ.get("CB_LOG_LEVEL", "INFO"), handlers=handlers,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")


def main() -> int:
    setup_logging()
    db_path = get_db_path()
    if not db_path.exists():
        log.error("Database not found at %s. Run: python -m cb.seed --reset", db_path)
        return 2
    conn = connect(db_path)
    try:
        actor: Actor = load_actor_from_env(conn)
    except IdentityError as e:  # AUTH-1 / AC-13
        log.error("Refusing to start: %s", e)
        conn.close()
        return 2
    _state.update(conn=conn, actor=actor)
    log.info("Starting community-bridge as %s (%s, %s), db=%s", actor.staff_id, actor.role, actor.nonprofit_id, db_path)
    mcp.run("stdio")
    return 0


if __name__ == "__main__":
    sys.exit(main())