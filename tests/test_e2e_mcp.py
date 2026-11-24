"""End-to-end through real MCP over stdio (AC-19, AC-13, AUTH-2/4 at the protocol level).

Each session launches `python -m cb.server` as a subprocess with a fixture identity.
"""
import subprocess
import sys

import anyio
import pytest

sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parents[1] / "demo"))
from mcp_helpers import PROJECT_ROOT, parse, server_params, session_as  # noqa: E402

EXPECTED_TOOLS = {"whoami", "list_participants", "list_funds", "create_request", "approve_request",
                  "reject_request", "record_payment", "get_request", "list_requests", "get_funding_summary"}


def test_full_workflow_over_mcp(db_path):  # AC-19
    async def scenario():
        async with session_as("cb-caseworker", db_path) as s:
            tools = (await s.list_tools()).tools
            assert {t.name for t in tools} == EXPECTED_TOOLS
            create = next(t for t in tools if t.name == "create_request")
            assert create.output_schema is not None
            assert {"staff_id", "role", "nonprofit_id"}.isdisjoint(create.input_schema["properties"])  # AUTH-2
            out = parse(await s.call_tool("create_request", {
                "participant_id": "part-cb-001", "fund_id": "fund-cb-rent", "category": "RENT",
                "purpose": "March rent", "amount": "300.00"}))
            rid = out["request"]["id"]
            assert out["request"]["status"] == "PENDING"
            err = parse(await s.call_tool("approve_request", {"request_id": rid}))
            assert err["error"]["code"] == "FORBIDDEN"

        async with session_as("hh-manager", db_path) as s:  # other nonprofit
            err = parse(await s.call_tool("approve_request", {"request_id": rid}))
            assert err["error"]["code"] == "NOT_FOUND"

        async with session_as("cb-manager", db_path) as s:
            out = parse(await s.call_tool("approve_request", {"request_id": rid}))
            assert (out["fund"]["committed"], out["fund"]["available"]) == ("300.00", "9700.00")

        async with session_as("cb-finance", db_path) as s:
            out = parse(await s.call_tool("record_payment", {"request_id": rid}))
            assert out["request"]["status"] == "PAID"
            err = parse(await s.call_tool("record_payment", {"request_id": rid}))
            assert err["error"]["code"] == "INVALID_TRANSITION"
            f = parse(await s.call_tool("get_funding_summary", {"fund_id": "fund-cb-rent"}))["funds"][0]
            assert (f["committed"], f["paid"], f["available"]) == ("0.00", "300.00", "9700.00")
            assert f["counts_by_status"]["PAID"] == 1

    anyio.run(scenario)


def test_invalid_input_rejected_over_mcp(db_path):  # AC-14 at protocol level
    async def scenario():
        async with session_as("cb-caseworker", db_path) as s:
            err = parse(await s.call_tool("create_request", {
                "participant_id": "part-cb-001", "fund_id": "fund-cb-rent", "category": "RENT",
                "purpose": "x", "amount": "-5"}))
            assert err["error"]["code"] == "VALIDATION_ERROR"
            res = await s.call_tool("create_request", {
                "participant_id": "part-cb-001", "fund_id": "fund-cb-rent", "category": "FOOD",
                "purpose": "x", "amount": "5.00"})
            assert res.is_error  # rejected by the published input schema
            assert parse(await s.call_tool("list_requests", {}))["requests"] == []

    anyio.run(scenario)


@pytest.mark.parametrize("staff_id", [None, "nobody"])
def test_server_refuses_missing_or_unknown_identity(db_path, staff_id):  # AC-13
    params = server_params(staff_id or "", db_path)
    env = dict(params.env)
    if staff_id is None:
        env.pop("CB_STAFF_ID")
    proc = subprocess.run([sys.executable, "-m", "cb.server"], env=env, cwd=PROJECT_ROOT,
                          input="", capture_output=True, text=True, timeout=30)
    assert proc.returncode == 2
    assert proc.stdout == ""  # nothing on the protocol channel
    assert "Refusing to start" in proc.stderr