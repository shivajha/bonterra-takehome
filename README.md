# bonterra-takehome

MCP server for emergency assistance funding – take-home

# Community Bridge – Emergency Assistance Funding MCP Server

A headless MCP server (Python, official MCP SDK **2.3.0**, SQLite, stdio) that lets nonprofit staff submit, approve, reject and pay emergency-assistance requests against grant funds, with exact cent arithmetic, atomic budget reservation, role checks and nonprofit isolation.

| Document                                           | Purpose                                                                                       |
| -------------------------------------------------- | --------------------------------------------------------------------------------------------- |
| [`docs/SPEC.md`](docs/SPEC.md)                     | Specification (v1.0 committed before code; v1.1 clarifications) incl. implementation plan §11 |
| [`docs/CHANGELOG-SPEC.md`](docs/CHANGELOG-SPEC.md) | Spec revisions and reasons                                                                    |
| [`docs/TRACEABILITY.md`](docs/TRACEABILITY.md)     | Requirement → implementation → test mapping                                                   |
| [`docs/AI-LOG.md`](docs/AI-LOG.md)                 | AI-assisted workflow evidence and time log                                                    |
| [`docs/demo-output.txt`](docs/demo-output.txt)     | Captured output of the MCP client demo                                                        |

## 1. Setup (Windows PowerShell; tested on Python 3.14.6, Windows 11)

```powershell
git clone https://github.com/shivajha/bonterra-takehome.git
cd bonterra-takehome
python -m venv .venv
.venv\Scripts\Activate.ps1            # macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt       # pinned versions (mcp==2.3.0, pytest==9.2.0, ...)
pip install -e .                      # installs the `cb` package from src/
```

## 2. Seed / reset (separate from startup)

```powershell
python -m cb.seed --reset     # deletes data/cb.db and recreates it with synthetic data
python -m cb.seed             # refuses if the DB already exists (no accidental wipe)
```

Optional `CB_DB_PATH` overrides the DB location. The server never seeds or resets.

**Seed data:** 2 nonprofits — _Community Bridge_ (`np-cb`: funds `fund-cb-rent` $10,000.00, `fund-cb-utility` $500.00; participants `part-cb-001..003`) and _Harbor Help_ (`np-hh`: fund `fund-hh-transit` $2,000.00; participants `part-hh-001..002`).

**Fixture identities:** `cb-caseworker`, `cb-manager`, `cb-finance`, `hh-caseworker`, `hh-manager`, `hh-finance` (CASE_WORKER / PROGRAM_MANAGER / FINANCE in each nonprofit).

## 3. Run the server (stdio)

```powershell
$env:CB_STAFF_ID="cb-caseworker"; python -m cb.server
```

The identity is resolved **on the server** at startup from `CB_STAFF_ID` → role + nonprofit from the `staff` table. Missing or unknown identity → `Refusing to start` on stderr, exit code 2. Logs go to stderr only (optional `CB_LOG_FILE`); stdout carries only MCP protocol.

**MCP client config** (e.g. Claude Desktop `claude_desktop_config.json`; VS Code `.vscode/mcp.json` uses `"servers"` instead of `"mcpServers"`). One entry per identity — the launcher chooses who you are:

```json
{
  "mcpServers": {
    "cb-caseworker": {
      "command": "D:\\AI study\\bonterra-takehome\\.venv\\Scripts\\python.exe",
      "args": ["-m", "cb.server"],
      "env": {
        "CB_STAFF_ID": "cb-caseworker",
        "CB_DB_PATH": "D:\\AI study\\bonterra-takehome\\data\\cb.db"
      }
    },
    "cb-manager": { "...": "same, with CB_STAFF_ID = cb-manager" },
    "cb-finance": { "...": "same, with CB_STAFF_ID = cb-finance" }
  }
}
```

**Tools (10):** `whoami`, `list_participants`, `list_funds`, `create_request`, `approve_request`, `reject_request`, `record_payment`, `get_request`, `list_requests`, `get_funding_summary`. Every tool has an input schema and a Pydantic output schema; business errors return `isError` with `{"error": {"code", "message", "details"}}` (codes in SPEC §5.3).

## 4. Tests and demo

```powershell
pytest -q                                  # all tests (unit + concurrency + E2E over MCP)
python -m cb.seed --reset
python demo\demo_client.py                 # discover tools → create → approve → pay → summary
```

The demo launches the server three times over stdio as `cb-caseworker`, `cb-manager` and `cb-finance` (no language model involved), and also shows that a payment retry returns `INVALID_TRANSITION`. Final summary: committed 0.00, paid 300.00, available 9700.00, exactly as in the brief.

### Actual results (24 Nov 2026, Windows, Python 3.14.6)

```
106 passed in 27.32s
```

| Test file             | Tests | Covers                                                                                              |
| --------------------- | ----- | --------------------------------------------------------------------------------------------------- |
| `test_money.py`       | 27    | exact cents, amount validation (NFR-1, REQ-2)                                                       |
| `test_db.py`          | 6     | seed/reset repeatable, DB CHECK constraints, rollback                                               |
| `test_auth.py`        | 6     | missing/unknown identity, server-side role lookup                                                   |
| `test_service.py`     | 41    | transitions, funding math, insufficient funds, retries, roles, cross-nonprofit, validation, reports |
| `test_concurrency.py` | 22    | competing approvals (20 repeated runs + 12-way + same-request race)                                 |
| `test_e2e_mcp.py`     | 4     | full workflow over real MCP stdio; schema-level validation; startup refusal                         |

Failed or unrun checks: none failed. Not run: multi-**process** concurrency (see limitations), and a manual session in Claude Desktop/VS Code with the config above.

## 5. How competing approvals are handled (WF-5)

Approval runs in one SQLite write transaction opened with `BEGIN IMMEDIATE`, which takes the database write lock up front, so approvals are serialized. Inside it, the reservation is a **single conditional update**:

```sql
UPDATE fund SET committed_cents = committed_cents + :amt
 WHERE id = :fund AND nonprofit_id = :np
   AND allocation_cents - committed_cents - paid_cents >= :amt;   -- 0 rows → INSUFFICIENT_FUNDS
```

then the request moves `PENDING → APPROVED` with `WHERE status = 'PENDING'`. Any failure raises inside the transaction and rolls everything back. A table-level `CHECK (committed_cents + paid_cents <= allocation_cents)` is a last line of defence. Payment uses the same pattern (`WHERE status = 'APPROVED'`), so it can succeed only once.

**Verification:** `tests/test_concurrency.py` starts threads that each open their **own** DB connection and release them simultaneously with a barrier: (a) two $300 approvals on a $500 fund → exactly one APPROVED, one INSUFFICIENT_FUNDS, committed 300.00 (repeated 20×); (b) twelve $100 approvals → exactly 5 approved, committed = sum of approved = $500; (c) five approvals of the same request → exactly one succeeds, four INVALID_TRANSITION.

## 6. Time, limitations, next priorities

**Actual active time:** ≈ 1 h 35 min (spec ≈ 25 min, implementation + tests ≈ 55 min, docs ≈ 15 min). Setup (installing tools, repo, Copilot sign-in) and breaks excluded; timestamps in `docs/AI-LOG.md`.

**Known limitations**

- Identity is fixed per server process (one `CB_STAFF_ID`); switching users means a separate client config entry. Production would resolve identity per request from an access token (SPEC §9).
- SQLite with one connection per server process; concurrency verified with threads + separate connections, not with multiple server processes against the same file (same SQLite locking applies, but untested).
- `create_request` without `client_request_id` is not deduplicated (documented in SPEC §3.4).
- No pagination beyond the 200-row cap on `list_requests`.
- Audit events are written but not exposed through a tool.

**Next priorities:** multi-process concurrency test; `get_request_history` tool over `audit_event`; pagination; HTTP transport with OAuth token validation per SPEC §9; CI workflow running `pytest` on push.
