# Project State

## Project Reference

See: .planning/PROJECT.md (updated 2026-02-13)

**Core value:** Students can track their learning progress and earn rewards (XP, streaks) with instant feedback and sub-second response times, even at 100K concurrent users.
**Current focus:** v3.0 Voucher Management System — Phase 35: Allocation & Distribution

## Current Position

Phase: 35 of 38 (Allocation & Distribution)
Plan: 1 of 2 complete
Status: In Progress
Last activity: 2026-02-14 — Completed 35-01 (Allocation API & Controller)

Progress: [##################################........] 89% (34/38 phases)

## Performance Metrics

**Velocity:**
- Total plans completed: 100
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
- [33-02]: Data fieldtype for pin_hmac (not Password) to enable WHERE clause queries for O(1) redemption lookup
- [33-02]: index_web_pages_for_search=0 on Voucher Card to prevent 10K+ cards from polluting global search
- [33-02]: Terminal states (Redeemed/Void/Expired) enforce immutability at the ORM level
- [33-03]: Redemption Log permissions are create+read only (no write/delete/cancel/share) for immutable audit trail
- [33-03]: Commission value stored as Data (string) for Decimal precision in Python
- [33-03]: voucher_hmac_secret is manual site_config.json requirement, not auto-generated
- [34-01]: HKDF with fixed versioned salt for Fernet key derivation (not PBKDF2) -- designed for high-entropy input
- [34-01]: PIN alphabet 30 chars excluding ambiguous 0/O/1/I/L for print readability
- [34-01]: Serial block reservation uses single FOR UPDATE lock for entire block, not per-card
- [34-02]: Single bulk_insert for all cards (no chunking) since max batch quantity is 1000
- [34-02]: Document name = serial_no via bulk_insert, bypassing autoname VCH-.#####. pattern
- [34-02]: grant_label from Product Grant used for CSV export product_names column
- [34-03]: Direct SQL UPDATE for void_batch instead of ORM per-card save (performance for up to 1000 cards)
- [34-03]: File doc deletion via frappe.delete_doc handles both DB record and physical file cleanup
- [34-03]: 30-day TTL for encrypted exports as security/storage tradeoff
- [35-01]: Two-step save for auto-approve (Draft->Approved->Completed) to respect VALID_TRANSITIONS map
- [35-01]: status IN ('Available', 'Allocated') in _apply_allocation for re-allocation support without return-first
- [35-01]: frappe.db.count for batch allocated_count (recount, not increment/decrement) to avoid counter drift
- [35-01]: Card-batch validation in both API layer and controller validate() for defense-in-depth

### Pending Todos

1 todo(s) in `.planning/todos/pending/`:
- **Implement track-level access enforcement and CDN flag** (api) — backend `TRK-*` grant check + `is_sold_separately` in `_h.json`

### Blockers/Concerns

- ERPNext Sales Invoice availability needs verification during Phase 37 (if not installed, may need lightweight custom invoice DocType)
- _handle_approval() commit behavior needs integration test in Phase 36 (on_update vs after_insert for status=Completed)

## Session Continuity

Last session: 2026-02-14
Stopped at: Completed 35-01-PLAN.md (Allocation API & Controller)
Resume file: None
Next action: Execute 35-02-PLAN.md (Form Buttons & UI)
