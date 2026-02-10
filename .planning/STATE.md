# Project State

## Project Reference

See: .planning/PROJECT.md (updated 2026-02-07)

**Core value:** Students can track their learning progress and earn rewards (XP, streaks) with instant feedback and sub-second response times, even at 100K concurrent users.
**Current focus:** v1.7 Profile Page API — Phase 26 (Profile Page API) In progress

## Current Position

Phase: 26 (Profile Page API)
Plan: 1 of 2
Status: In progress
Last activity: 2026-02-10 — Completed 26-01-PLAN.md (Level system, models, Frappe APIs)

Progress: [█████░░░░░] 50%

## Performance Metrics

**Velocity:**
- Total plans completed: 75
- Milestones shipped: 6

**By Milestone:**

| Milestone | Phases | Plans | Status |
|-----------|--------|-------|--------|
| v1.0 MVP | 7 | 30 | Shipped 2026-02-02 |
| v1.1 Feature Expansion | 4 | 13 | Shipped 2026-02-03 |
| v1.2 Plan System Enhancement | 1 | 4 | Shipped 2026-02-03 |
| v1.2.1 Gap Closure | 1 | 1 | Shipped 2026-02-03 |
| v1.3 Profiles & Devices | 7 | 16 | Shipped 2026-02-07 |
| v1.4 Product Store | 3 | 4 | Shipped 2026-02-08 |
| v1.5 Real-Time Notifications | 1 | 2 | Shipped 2026-02-08 |
| v1.6 FSRS Review System | 1 | 3 | Shipped 2026-02-09 |
| v1.7 Profile Page API | 1 | 2 | In progress (1/2) |

## Accumulated Context

### Decisions

All decisions logged in PROJECT.md Key Decisions table.

- Catalog cache: no TTL, event-driven invalidation only
- Purchased detection: use existing access set (all subjects in set = purchased)
- Pending detection: read from memora:pending:{player_id} set (Phase 22 populates)
- Two-pronged invalidation: direct Redis delete + pubsub notification for reliability
- Unpublished products return DoesNotExistError (don't reveal existence)
- Purchase endpoint returns 201 Created on success
- Purchase flow: manual payment only (payment gateway auto-approval deferred)
- Pending products: hidden from catalog (not shown with badge) to prevent duplicates
- Approval uses season end_date for expires_at with 2099-12-31 fallback
- No catalog cache invalidation on approval/rejection (filtering is live against Redis sets)
- ConnectionManager returns first/last connection booleans for pub/sub subscribe/unsubscribe lifecycle
- Notification publish never blocks approval/rejection flow (fire-and-forget with try/except)
- Separate notification pub/sub listener from cache invalidation listener (dynamic per-user channels vs static)
- JWT auth before WebSocket accept; Starlette rejects at HTTP layer for invalid tokens
- notify_pubsub object on app.state for dynamic subscribe/unsubscribe from WebSocket endpoint
- after_migrate hook for composite index persistence (Frappe only creates Property Setters for single-column indexes)
- Per-stage is_skippable override takes priority over global stage type setting
- next_review clamped to midnight with minimum tomorrow to prevent same-day reviews
- Inline FSRS computation in review submit API (no import from fsrs_processor.py to avoid coupling)
- Fetch limit+5 rows in get_due_stages to compensate for removed stages filtered out
- 3 XP per review session (not per stage) - reviews reward participation not volume
- No cache on get_due_stages (must be fresh); overview cache 5-min TTL with invalidation on submit
- Level thresholds as static constants (15 levels, expandable) -- not admin-configurable
- FSRS maturity threshold = 21.0 days stability (standard convention)
- Avatar validation reads from DocType field options, not hardcoded

### Pending Todos

1 todo(s) in `.planning/todos/pending/`:
- **Implement track-level access enforcement and CDN flag** (api) — backend `TRK-*` grant check + `is_sold_separately` in `_h.json`

### Roadmap Evolution

- Phase 24 complete: Real-Time Subscription Notifications (WebSockets + Redis Pub/Sub)
  - Deprecated SSE removed, replaced by WebSocket notification system
  - Scales to 100K+ concurrent users, <20ms propagation
  - Leverages existing Redis pub/sub infrastructure
- Phase 25 complete: FSRS Review System
  - Plan 01: Fix FSRS bugs (skippable filter, is_reviewable enforcement, date clamping) + composite index
  - Plan 02: Frappe whitelisted review API (get_review_overview, get_due_stages, submit_reviews)
  - Plan 03: FastAPI review endpoints with ReviewService (Redis cache, XP award via WalletService)
  - MariaDB composite index for 200K+ users, no Redis sorted sets
  - 3 XP per review session, no streak contribution
  - Content: Option B (client handles via local cache/CDN)
- Phase 26 added: Profile Page API (v1.7)
  - Hero section (avatar, username, level, XP progress)
  - Subject-filtered stats (streak, items learned, XP)
  - Memory mastery breakdown (mature/learning/new from FSRS)
  - Weekly activity chart (XP per day)
  - Avatar selection from predefined options
  - Logout endpoint

### Blockers/Concerns

- Payment gateway integration (PRCHS-03) deferred to future work — all transactions currently manual approval
- No end-to-end test data available for Phase 23 verification (no pending transactions existed during implementation)

## Session Continuity

Last session: 2026-02-10
Stopped at: Completed 26-01-PLAN.md (Level system, models, Frappe APIs)
Resume file: None
Next action: Execute 26-02-PLAN.md (ProfilePageService + FastAPI endpoints)
