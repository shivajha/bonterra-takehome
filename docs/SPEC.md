# Community Bridge – Emergency Assistance Funding MCP Server

## Specification v1.0 (initial, written before implementation)

Status: v1.0 – committed before any implementation code. Later changes are logged in `docs/CHANGELOG-SPEC.md` with the reason.

---

## 1. Context, users, goals

**Problem.** Community Bridge (a nonprofit) tracks emergency-assistance requests (rent, utilities, transportation) and grant-fund balances in spreadsheets. Staff need the same workflow through AI assistants / MCP clients, with correct money handling and strict access control.

**Users (staff roles, one role per staff member, one nonprofit per staff member):**

| Role            | Code              | Can do                                              |
| --------------- | ----------------- | --------------------------------------------------- |
| Case worker     | `CASE_WORKER`     | Read; submit assistance requests                    |
| Program manager | `PROGRAM_MANAGER` | Read; approve / reject pending requests             |
| Finance staff   | `FINANCE`         | Read; record simulated payment on approved requests |

**Goals**

- G1. Submit → decide → pay workflow exposed as discoverable MCP tools.
- G2. Fund balances are always correct: no overspending, no double reservation, no double payment.
- G3. Staff only ever see or change their own nonprofit's records.

**Non-goals (out of scope):** UI; real payments/banking; OAuth implementation or hosting; MCP resources/prompts; participant intake or grant applications; partial payments, cancellations, refunds, payment failures; editing requests; multi-currency; fund creation/top-ups via tools.

**Assumptions** (see §10 for the questions behind them)

- A1. Each staff member belongs to exactly one nonprofit and has exactly one role.
- A2. Participants and funds already exist (created by seed data only).
- A3. Currency is USD only. Max single request amount is $100,000.00.
- A4. A fund has a fixed allocation that does not change during the exercise.
- A5. Separation of duties is achieved by roles (a manager cannot pay, finance cannot approve). The same person cannot hold two roles.
- A6. Fund balance is not checked at submission (submission does not reserve money); it is checked at approval.

---

## 2. Requirements (IDs used in code comments and tests)

### Identity & permissions

- **AUTH-1** The server resolves its caller identity once at startup from env var `CB_STAFF_ID` against the `staff` table. Missing or unknown ID → server refuses to start (non-zero exit, message on stderr). No tool can be called without a resolved identity.
- **AUTH-2** Role and nonprofit are always taken from the server-side staff record. No tool accepts `staff_id`, `role` or `nonprofit_id` as input. Free-text fields (purpose, reason) are stored as data and never affect permissions.
- **AUTH-3** Role matrix (§6) is enforced in the service layer for every tool.
- **AUTH-4** Tenant isolation: every query and update is filtered by the caller's `nonprofit_id`. A record belonging to another nonprofit is reported as `NOT_FOUND` (we do not reveal that it exists).

### Lookups

- **LKP-1** List participants of the caller's nonprofit.
- **LKP-2** List funds of the caller's nonprofit with balances.

### Requests

- **REQ-1** Case worker creates a request with `participant_id`, `fund_id`, `category`, `purpose`, `amount`. Participant and fund must belong to the caller's nonprofit.
- **REQ-2** Amount validation: decimal string, > 0, at most 2 decimal places, ≤ 100000.00. Stored as integer cents.
- **REQ-3** New request status is `PENDING`. Creation does not change any fund balance.
- **REQ-4** Requests are immutable after submission: no tool edits participant, fund, category, purpose or amount.
- **REQ-5** Optional `client_request_id` for safe retries of create. Same caller + same key + same payload → return the original request (`replayed: true`), no new row. Same key + different payload → `CONFLICT`.

### Workflow

- **WF-1** Approve: `PENDING → APPROVED`. Atomically reserves the full amount: `committed += amount`. If `available < amount` → `INSUFFICIENT_FUNDS`, nothing changes.
- **WF-2** Reject: `PENDING → REJECTED`, optional `reason`. Funding unchanged.
- **WF-3** Pay: `APPROVED → PAID`, full amount only. `committed -= amount`, `paid += amount`; `available` unchanged.
- **WF-4** Any other transition → `INVALID_TRANSITION` (response includes `current_status`); nothing changes. This makes retries safe: a repeated approve or pay can never create a second financial effect.
- **WF-5** Concurrency: approval check-and-reserve is a single atomic database transaction. Competing approvals can never push `committed + paid` above `allocation`.
- **WF-6** Every successful state change writes an `audit_event` row (who, when, from-status, to-status) in the same transaction.

### Reporting

- **RPT-1** Get one request by ID.
- **RPT-2** List requests, optional filters `status`, `fund_id`, `participant_id`.
- **RPT-3** Funding summary per fund: allocation, committed, paid, available, and request counts by status.

### Non-functional

- **NFR-1** Exact money: integer cents everywhere; no floating point. Display as `"300.00"` strings.
- **NFR-2** Persistent local storage: SQLite file (path from `CB_DB_PATH`, default `data/cb.db`).
- **NFR-3** Separate, repeatable seed/reset command: `python -m cb.seed --reset`. Normal startup never seeds or resets.
- **NFR-4** Local stdio transport. Application logs go to stderr / log file only, never stdout (stdout is MCP protocol).
- **NFR-5** Pinned dependencies in `requirements.txt`.
- **NFR-6** Any invalid or unauthorized action leaves all business state unchanged.

---

## 3. Business behavior

### 3.1 State machine

```
            approve (PM, funds ok)          pay (FINANCE)
 PENDING ─────────────────────────▶ APPROVED ─────────────▶ PAID
    │
    │ reject (PM)
    ▼
 REJECTED
```

`REJECTED` and `PAID` are final. Allowed transitions only: `PENDING→APPROVED`, `PENDING→REJECTED`, `APPROVED→PAID`.

### 3.2 Budget calculation (per fund)

```
available = allocation − committed − paid
```

| Event          | committed | paid | available     |
| -------------- | --------- | ---- | ------------- |
| Submit request | —         | —    | —             |
| Approve $X     | +X        | —    | −X            |
| Reject         | —         | —    | —             |
| Pay $X         | −X        | +X   | — (unchanged) |

Example (from the brief): fund $10,000.00. Approve $300 → committed 300, paid 0, available 9,700. Pay → committed 0, paid 300, available 9,700.

Invariant (enforced by code **and** a DB `CHECK` constraint): `0 ≤ committed`, `0 ≤ paid`, `committed + paid ≤ allocation`.

### 3.3 Validation rules

| Field               | Rule                                                               |
| ------------------- | ------------------------------------------------------------------ |
| `amount`            | string matching `^\d{1,6}(\.\d{1,2})?$`, value > 0 and ≤ 100000.00 |
| `category`          | one of `RENT`, `UTILITIES`, `TRANSPORTATION`, `OTHER`              |
| `purpose`           | 1–200 chars after trimming                                         |
| `reason` (reject)   | optional, ≤ 500 chars                                              |
| IDs                 | non-empty string ≤ 64 chars; must exist **in caller's nonprofit**  |
| `client_request_id` | optional, 1–64 chars                                               |

Check order inside every tool: **role → input validation → tenant lookup → state/funds check → write**. Role is checked first so unauthorized callers learn nothing about records.

### 3.4 Retry behavior

| Retry                                               | Result                                          | Money effect                    |
| --------------------------------------------------- | ----------------------------------------------- | ------------------------------- |
| Create with same `client_request_id` + same payload | original request, `replayed: true`              | none                            |
| Create, same key, different payload                 | `CONFLICT`                                      | none                            |
| Create without key, sent twice                      | two requests (documented limitation)            | none (create never moves money) |
| Approve an already-APPROVED / PAID request          | `INVALID_TRANSITION`, `current_status` returned | none                            |
| Pay an already-PAID request                         | `INVALID_TRANSITION`, `current_status: PAID`    | none                            |

---

## 4. Data model (SQLite)

```
nonprofit   (id PK, name)
staff       (id PK, nonprofit_id FK, name, role CHECK IN (CASE_WORKER, PROGRAM_MANAGER, FINANCE))
participant (id PK, nonprofit_id FK, name)
fund        (id PK, nonprofit_id FK, name,
             allocation_cents INT CHECK > 0,
             committed_cents  INT DEFAULT 0 CHECK >= 0,
             paid_cents       INT DEFAULT 0 CHECK >= 0,
             CHECK (committed_cents + paid_cents <= allocation_cents))
request     (id PK, nonprofit_id FK, participant_id FK, fund_id FK,
             category, purpose, amount_cents INT CHECK > 0,
             status CHECK IN (PENDING, APPROVED, REJECTED, PAID),
             created_by FK staff, created_at,
             decided_by, decided_at, decision_reason,
             paid_by, paid_at,
             client_request_id NULL,
             UNIQUE (nonprofit_id, created_by, client_request_id))
audit_event (id PK, nonprofit_id, request_id, actor_id, action, from_status, to_status, at)
```

- **Ownership:** every business row carries `nonprofit_id`. A request's participant and fund must share the request's `nonprofit_id` (checked in the service layer on create).
- **Money:** integer cents (`amount_cents`). Parsed from strings with `decimal.Decimal` (never `float`), then converted to `int`.
- **Fund counters:** `committed_cents` / `paid_cents` are stored on the fund and updated in the same transaction as the request status. This keeps approval a single conditional update (fast, atomic). A test re-derives them from request rows to prove they match.
- **IDs:** readable text IDs in seed data (`fund-cb-rent`); new requests get `req_` + random hex.
- **Timestamps:** UTC ISO-8601.

---

## 5. MCP interface

Transport: stdio. Server name `community-bridge`. All tools return structured content (JSON) matching the output schema. Money outputs are given both as `"300.00"` strings and integer cents.

### 5.1 Common types

```
Money      = { "amount": "300.00", "amount_cents": 30000 }
Request    = { id, participant_id, fund_id, category, purpose, amount, amount_cents,
               status, created_by, created_at, decided_by?, decided_at?,
               decision_reason?, paid_by?, paid_at? }
FundStatus = { fund_id, name, allocation, committed, paid, available   (each "0.00" string),
               allocation_cents, committed_cents, paid_cents, available_cents }
Error      = { "error": { "code": <ErrorCode>, "message": str, "details": {...} } }
```

### 5.2 Tools

| Tool                  | Roles           | Input                                                                    | Output                                                 | Side effects                                              |
| --------------------- | --------------- | ------------------------------------------------------------------------ | ------------------------------------------------------ | --------------------------------------------------------- |
| `whoami`              | all             | —                                                                        | `{staff_id, name, role, nonprofit_id, nonprofit_name}` | none                                                      |
| `list_participants`   | all             | —                                                                        | `{participants: [{id, name}]}`                         | none                                                      |
| `list_funds`          | all             | —                                                                        | `{funds: [FundStatus]}`                                | none                                                      |
| `create_request`      | CASE_WORKER     | `participant_id, fund_id, category, purpose, amount, client_request_id?` | `{request: Request, replayed: bool}`                   | inserts request + audit (`CREATED`)                       |
| `approve_request`     | PROGRAM_MANAGER | `request_id`                                                             | `{request: Request, fund: FundStatus}`                 | status → APPROVED, fund.committed += amount, audit        |
| `reject_request`      | PROGRAM_MANAGER | `request_id, reason?`                                                    | `{request: Request}`                                   | status → REJECTED, audit                                  |
| `record_payment`      | FINANCE         | `request_id`                                                             | `{request: Request, fund: FundStatus}`                 | status → PAID, committed −= amount, paid += amount, audit |
| `get_request`         | all             | `request_id`                                                             | `{request: Request}`                                   | none                                                      |
| `list_requests`       | all             | `status?, fund_id?, participant_id?`                                     | `{requests: [Request]}` (newest first, max 200)        | none                                                      |
| `get_funding_summary` | all             | `fund_id?`                                                               | `{funds: [FundStatus + counts_by_status]}`             | none                                                      |

### 5.3 Errors

Errors are returned as an MCP tool error (`isError: true`) whose content is the `Error` JSON above. No partial writes ever happen on error.

| Code                 | When                                                           |
| -------------------- | -------------------------------------------------------------- |
| `FORBIDDEN`          | caller's role may not use this tool                            |
| `VALIDATION_ERROR`   | input fails §3.3 rules (`details.field`)                       |
| `NOT_FOUND`          | ID does not exist in caller's nonprofit                        |
| `INVALID_TRANSITION` | status does not allow the action (`details.current_status`)    |
| `INSUFFICIENT_FUNDS` | approval amount > available (`details.available`)              |
| `CONFLICT`           | `client_request_id` reused with different payload              |
| `INTERNAL`           | unexpected error (logged to stderr; generic message to client) |

Missing/unknown identity is handled at startup (AUTH-1) — the server never reaches the tool stage.

---

## 6. Permissions

| Tool                                               | CASE_WORKER | PROGRAM_MANAGER | FINANCE |
| -------------------------------------------------- | ----------- | --------------- | ------- |
| whoami, list\_\*, get_request, get_funding_summary | ✅          | ✅              | ✅      |
| create_request                                     | ✅          | ❌              | ❌      |
| approve_request, reject_request                    | ❌          | ✅              | ❌      |
| record_payment                                     | ❌          | ❌              | ✅      |

**How it is enforced**

1. **Identity:** the launcher sets `CB_STAFF_ID` (e.g. in the MCP client config). At startup the server loads that staff row → an immutable `Actor(staff_id, role, nonprofit_id)` held by the server process. This stands in for an authenticated token in production (§9).
2. **Role:** each service function begins with `require_role(actor, ...)`.
3. **Nonprofit boundary:** every SQL statement includes `AND nonprofit_id = :actor_nonprofit`. Cross-tenant IDs therefore look like non-existent IDs.
4. **No escalation via input:** tool schemas have no identity fields; text fields are stored verbatim and never interpreted.

---

## 7. Concurrency design (WF-5)

Approval runs in one SQLite write transaction (`BEGIN IMMEDIATE`, `busy_timeout = 5s`):

```sql
UPDATE request SET status='APPROVED', decided_by=?, decided_at=?
 WHERE id=? AND nonprofit_id=? AND status='PENDING';            -- 0 rows → INVALID_TRANSITION / NOT_FOUND
UPDATE fund SET committed_cents = committed_cents + :amt
 WHERE id=? AND nonprofit_id=?
   AND allocation_cents - committed_cents - paid_cents >= :amt;  -- 0 rows → INSUFFICIENT_FUNDS
INSERT INTO audit_event ...;
COMMIT;  -- any failure → ROLLBACK, nothing changes
```

`BEGIN IMMEDIATE` takes SQLite's write lock up front, so two approvals are serialized; the second one sees the updated balance. The conditional `WHERE` clauses mean the check and the write cannot be separated, and the DB `CHECK` constraint is a final safety net. Payment uses the same pattern with `WHERE status='APPROVED'`, so it can succeed only once.

**Verification:** a test starts N threads, each with its own DB connection, approving different requests against one fund whose allocation covers only some of them. Expected: approvals that fit succeed, the rest get `INSUFFICIENT_FUNDS`, and final `committed ≤ allocation` and equals the sum of APPROVED amounts.

---

## 8. Acceptance criteria

Seed: nonprofit **CB** (fund `fund-cb-rent` $10,000.00, fund `fund-cb-utility` $500.00) and nonprofit **HH** (fund `fund-hh-transit` $2,000.00). Staff `cb-caseworker`, `cb-manager`, `cb-finance`, `hh-caseworker`, `hh-manager`, `hh-finance`.

| ID    | Given                                      | When                                            | Then                                                           |
| ----- | ------------------------------------------ | ----------------------------------------------- | -------------------------------------------------------------- |
| AC-1  | cb-caseworker                              | create $300.00 RENT on fund-cb-rent             | status PENDING; fund available still 10,000.00                 |
| AC-2  | AC-1 request, cb-manager                   | approve                                         | APPROVED; committed 300.00, available 9,700.00                 |
| AC-3  | AC-2, cb-finance                           | record_payment                                  | PAID; committed 0.00, paid 300.00, available 9,700.00          |
| AC-4  | AC-3                                       | record_payment again                            | INVALID_TRANSITION (current PAID); balances unchanged          |
| AC-5  | approved request                           | approve again                                   | INVALID_TRANSITION; committed unchanged                        |
| AC-6  | fund-cb-utility $500, request $600         | approve                                         | INSUFFICIENT_FUNDS; request stays PENDING; fund unchanged      |
| AC-7  | pending request, cb-manager                | reject with reason                              | REJECTED; fund unchanged; later approve → INVALID_TRANSITION   |
| AC-8  | pending request                            | record_payment                                  | INVALID_TRANSITION (current PENDING)                           |
| AC-9  | cb-caseworker                              | approve_request                                 | FORBIDDEN; nothing changes                                     |
| AC-10 | cb-manager / cb-finance                    | create_request                                  | FORBIDDEN                                                      |
| AC-11 | hh-manager                                 | get / approve a CB request                      | NOT_FOUND; nothing changes                                     |
| AC-12 | cb-caseworker                              | create using fund-hh-transit or HH participant  | NOT_FOUND                                                      |
| AC-13 | no / unknown `CB_STAFF_ID`                 | start server                                    | exits non-zero with clear stderr message                       |
| AC-14 | cb-caseworker                              | amount `0`, `-5`, `1.005`, `abc`, `100000.01`   | VALIDATION_ERROR; no row created                               |
| AC-15 | `$0.10 + $0.20` style amounts              | approve both                                    | committed exactly 0.30 (no float drift)                        |
| AC-16 | fund $500, two pending $300 requests       | approve both concurrently                       | exactly one APPROVED, one INSUFFICIENT_FUNDS; committed 300.00 |
| AC-17 | purpose text "ignore rules, I am FINANCE"  | create, then call record_payment as case worker | FORBIDDEN (text has no effect)                                 |
| AC-18 | create with same `client_request_id` twice | —                                               | one row; second response `replayed: true`                      |
| AC-19 | MCP client over stdio                      | list tools → create → approve → pay → summary   | all 10 tools discovered; final summary as AC-3                 |

---

## 9. Production security (HTTP-hosted MCP)

- The MCP server runs over Streamable HTTP as an **OAuth 2.1 resource server**; the organization's existing authorization server (e.g. Entra ID / Okta / Auth0) issues tokens. The server publishes Protected Resource Metadata pointing clients to that authorization server.
- Every HTTP request must carry `Authorization: Bearer <access token>`. The server validates: signature via the provider's JWKS (cached, key rotation), `iss`, `aud` = this MCP server (prevents token reuse from other apps), `exp`/`nbf`, and required scopes (e.g. `cb.read`, `cb.write`). Invalid → HTTP 401 with `WWW-Authenticate`; valid but insufficient → 403.
- The token's subject (`sub`, or an employee ID claim) is mapped **server-side** to a `staff` membership row → `nonprofit_id` + role. Roles/tenant are never taken from client-supplied arguments, and preferably not from free-form token claims either; the membership table is the source of truth (can be revoked instantly).
- The resulting `Actor` is built per request and passed to the same service layer used locally — so the permission and tenant checks in this exercise are reused unchanged; only the "where does the Actor come from" step changes (env var → validated token).
- Additional: TLS only, no token passthrough to downstream APIs, audit log includes token subject, rate limiting, short token lifetimes.

---

## 10. Open questions (with working assumptions)

| #   | Question for the business                                               | Assumption made                                  |
| --- | ----------------------------------------------------------------------- | ------------------------------------------------ |
| Q1  | Can one staff member work for several nonprofits or hold several roles? | No (A1).                                         |
| Q2  | Is there a per-request or per-participant maximum?                      | $100,000.00 per request cap only (A3).           |
| Q3  | Should submission warn if the fund currently lacks money?               | No check at submission (A6).                     |
| Q4  | Should a manager be blocked from approving a request they created?      | Not possible — managers cannot create (A5).      |
| Q5  | Can fund allocations change (top-ups, new grants)?                      | Fixed for this exercise (A4).                    |
| Q6  | Should rejected requests be resubmittable?                              | Case worker creates a new request.               |
| Q7  | Do approvals ever expire, releasing the reservation?                    | No expiry; cancellation is out of scope.         |
| Q8  | What participant data may be shown to each role (privacy)?              | ID and name only.                                |
| Q9  | Required audit retention / reporting?                                   | Audit rows kept indefinitely; no reporting tool. |

---

## 11. Implementation plan

| #   | Task                                                           | Depends on | Done when (gate)                                                           |
| --- | -------------------------------------------------------------- | ---------- | -------------------------------------------------------------------------- |
| T1  | `money.py`: parse/format cents                                 | —          | unit tests for AC-14, AC-15 pass                                           |
| T2  | `db.py` schema + `seed.py --reset`                             | —          | reset runs twice in a row with identical result; CHECK constraints present |
| T3  | `auth.py`: load Actor from `CB_STAFF_ID`, `require_role`       | T2         | AC-13 test passes                                                          |
| T4  | `service.py`: lookups, create, approve, reject, pay, summaries | T1–T3      | AC-1…AC-12, AC-17, AC-18 pass                                              |
| T5  | Concurrency test                                               | T4         | AC-16 passes 20 repeated runs                                              |
| T6  | `server.py`: MCPServer tools (stdio), logging to stderr        | T4         | `list_tools` shows 10 tools with schemas                                   |
| T7  | E2E MCP test + `demo/demo_client.py`                           | T6         | AC-19 passes; demo prints final summary                                    |
| T8  | README, requirement→code→test map, AI log, results             | all        | fresh clone → documented commands work                                     |

**Verification gate after every task:** `pytest -q` green before the next task starts; commit per task.

**How I scope and review work delegated to an AI coding agent**

- One task (T-row) per prompt. The prompt includes the relevant spec sections and the requirement IDs, plus "do not add features not in the spec".
- Agent must write/extend tests for the listed AC IDs; I read the tests first to check they assert the spec (including "state unchanged" after failures).
- I review the diff for: float usage, missing `nonprofit_id` filters, role check order, writes outside a transaction, `print()` to stdout.
- I run the tests myself; I never accept "tests pass" from the agent without running them.
- Any behavior the agent needed to invent is a spec gap → update the spec + CHANGELOG first, then code.
