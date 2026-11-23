# AI Workflow Log & Time

## Time

- Start: 23 Nov 2026, 22:46 IST

# Spec change log

## v1.1 — 23 Nov 2026 (before implementation)

Trigger: AI review of v1.0 (GitHub Copilot Chat, Ask mode), checked manually against the brief. 4 of 5 points accepted.

| Change                                                                                                                                      | Section     | Reason                                                                          |
| ------------------------------------------------------------------------------------------------------------------------------------------- | ----------- | ------------------------------------------------------------------------------- |
| Defined "same payload" for create retries as normalized values (`amount_cents`, trimmed `purpose`, IDs, category)                           | REQ-5       | `"300"` vs `"300.00"` would otherwise give inconsistent replay/conflict results |
| List filters: unknown status → `VALIDATION_ERROR`; unknown or cross-tenant `fund_id`/`participant_id` → empty list; tie-break order by `id` | RPT-2       | Behavior was unspecified; empty list avoids revealing other nonprofits' IDs     |
| Defined `counts_by_status` shape (all four keys, integers); unknown `fund_id` in summary → `NOT_FOUND`                                      | RPT-3, §5.2 | Output schema was ambiguous                                                     |
| Store trimmed `purpose`; `reason` trimmed, empty → null                                                                                     | §3.3        | Normalization policy was unstated                                               |
| Marked §9 as design-only, not implemented                                                                                                   | §9          | Clarity                                                                         |

Rejected: "HTTP/OAuth contradicts the stdio implementation". The brief asks only for an explanation of production security and lists OAuth and remote hosting as out of scope, so there is no contradiction (a clarifying note was added instead).

## Example 2 — Coding (money parsing, task T1)

- **Context:** Tests for NFR-1 / REQ-2 / AC-14 / AC-15 written first (`tests/test_money.py`, 27 cases).
- **Prompt (Copilot inline completion):** function signature + docstring: "Convert a USD string like "300.50" to integer cents. Must be a str, at most 2 decimals, > 0 and <= 100000.00. Never use float."
- **AI suggestion:** Complete `parse_amount` + `format_cents`.
- **Independent check:** `pytest -q` → **5 failed, 22 passed**:
  - regex required exactly 2 decimals → "300.5" and "0.1" rejected
  - zero accepted ("0", "0.00") — only checked `< 0`
  - no maximum → "100000.01" accepted
  - Code review also found `format_cents` used `cents / 100` (float), violating NFR-1 — not caught by tests because output looked correct.
- **Correction:** Copilot Agent mode, given the failing tests, rewrote the function (1–2 decimals, `<= 0` check, max check, `divmod` instead of float). Re-ran: **27 passed**. I re-read the final code to confirm no float remains.
- **Lesson:** Tests caught 3 of 4 bugs; manual review caught the 4th.
