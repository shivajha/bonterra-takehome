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
