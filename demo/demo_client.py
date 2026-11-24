"""Reproducible MCP client demo (no language model).

Discovers tools, then: case worker creates a request -> manager approves -> finance pays
-> final funding summary. Each step launches the server with the right fixture identity.

Run:  python -m cb.seed --reset   then   python demo/demo_client.py
"""
import json
import sys
from pathlib import Path

import anyio

sys.path.insert(0, str(Path(__file__).resolve().parent))
from mcp_helpers import parse, session_as  # noqa: E402

from cb.db import get_db_path  # noqa: E402


def show(title, data):
    print(f"\n=== {title} ===")
    print(json.dumps(data, indent=2))


async def main():
    db = get_db_path()

    async with session_as("cb-caseworker", db) as s:
        tools = await s.list_tools()
        print("Discovered tools:", ", ".join(t.name for t in tools.tools))
        show("whoami (case worker)", parse(await s.call_tool("whoami", {})))
        created = parse(await s.call_tool("create_request", {
            "participant_id": "part-cb-001", "fund_id": "fund-cb-rent", "category": "RENT",
            "purpose": "Demo: March rent shortfall", "amount": "300.00"}))
        show("create_request (case worker)", created)
        rid = created["request"]["id"]

    async with session_as("cb-manager", db) as s:
        show("approve_request (program manager)", parse(await s.call_tool("approve_request", {"request_id": rid})))

    async with session_as("cb-finance", db) as s:
        show("record_payment (finance)", parse(await s.call_tool("record_payment", {"request_id": rid})))
        show("record_payment retry (finance) -> expected INVALID_TRANSITION",
             parse(await s.call_tool("record_payment", {"request_id": rid})))
        show("get_funding_summary fund-cb-rent (finance)",
             parse(await s.call_tool("get_funding_summary", {"fund_id": "fund-cb-rent"})))


if __name__ == "__main__":
    anyio.run(main)