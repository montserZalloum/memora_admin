# Project State

## Project Reference

See: .planning/PROJECT.md (updated 2026-02-13)

**Core value:** Students can track their learning progress and earn rewards (XP, streaks) with instant feedback and sub-second response times, even at 100K concurrent users.
**Current focus:** v3.0 Voucher Management System — Phase 33: DocType Foundation

## Current Position

Phase: 33 of 38 (DocType Foundation)
Plan: 1 of 3 complete
Status: Executing
Last activity: 2026-02-14 — Completed 33-01 (Voucher Batch & Batch Grant DocTypes)

Progress: [################################..........] 87% (32/38 phases)

## Performance Metrics

**Velocity:**
- Total plans completed: 94
- Milestones shipped: 12

**By Milestone:**

| Milestone | Phases | Plans | Status |
|-----------|--------|-------|--------|
| v1.0 MVP | 7 | 30 | Shipped 2026-02-02 |
| v1.1 Feature Expansion | 4 | 13 | Shipped 2026-02-03 |
| v1.2 Plan System Enhancement | 1 | 4 | Shipped 2026-02-03 |
| v1.2.1 Gap Closure | 1 | 1 | Shipped 2026-02-03 |
| v1.3 Profiles & Devices | 7 | 16 | Shipped 2026-02-07 |
| v1.4 Product Store | 3 | 5 | Shipped 2026-02-08 |
| v1.5 Real-Time Notifications | 1 | 2 | Shipped 2026-02-08 |
| v1.6 FSRS Review System | 1 | 3 | Shipped 2026-02-09 |
| v1.7 Profile Page API | 1 | 2 | Shipped 2026-02-10 |
| v1.8 Memory State Redesign | 1 | 5 | Shipped 2026-02-11 |
| v1.9 Tech Debt & Reliability | 1 | 4 | Shipped 2026-02-12 |
| v2.0 Mobile-First Auth | 4 | 9 | Shipped 2026-02-12 |
| v3.0 Voucher System | 6 | TBD | Active |

## Accumulated Context

### Decisions

All decisions logged in PROJECT.md Key Decisions table.
Recent decisions affecting current work:

- [v3.0]: Core redeem logic lives in Frappe (SELECT FOR UPDATE), FastAPI is auth/rate-limit proxy
- [v3.0]: HMAC-SHA256 for PIN storage (not bcrypt, not plaintext) -- deterministic for WHERE clause
- [v3.0]: No new Redis keys for voucher state -- cards are NOT hot data, MariaDB provides atomicity
- [v3.0]: Subscription Transaction with status="Completed" triggers existing Phase 23 pipeline
- [33-01]: Data fieldtype for commission_value (not Currency/Float) to avoid float precision issues
- [33-01]: allow_rename=0 on Voucher Batch since card records reference batch names

### Pending Todos

1 todo(s) in `.planning/todos/pending/`:
- **Implement track-level access enforcement and CDN flag** (api) — backend `TRK-*` grant check + `is_sold_separately` in `_h.json`

### Blockers/Concerns

- ERPNext Sales Invoice availability needs verification during Phase 37 (if not installed, may need lightweight custom invoice DocType)
- _handle_approval() commit behavior needs integration test in Phase 36 (on_update vs after_insert for status=Completed)

## Session Continuity

Last session: 2026-02-14
Stopped at: Completed 33-01-PLAN.md (Voucher Batch & Batch Grant DocTypes)
Resume file: None
Next action: Execute 33-02-PLAN.md
