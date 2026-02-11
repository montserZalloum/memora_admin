# Project State

## Project Reference

See: .planning/PROJECT.md (updated 2026-02-07)

**Core value:** Students can track their learning progress and earn rewards (XP, streaks) with instant feedback and sub-second response times, even at 100K concurrent users.
**Current focus:** v1.8 Memory State Redesign — Phase 27, Plan 01 complete (schema foundation)

## Current Position

Phase: 27 (Memory State Redesign — Item-Level FSRS)
Plan: 1 of 4
Status: In progress
Last activity: 2026-02-11 — Completed 27-01-PLAN.md (Schema Foundation)

Progress: [██░░░░░░░░] 25%

## Performance Metrics

**Velocity:**
- Total plans completed: 77
- Milestones shipped: 7

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
| v1.7 Profile Page API | 1 | 2 | Shipped 2026-02-10 |
| v1.8 Memory State Redesign | 1 | 4 | In Progress (1/4) |

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
- ProfilePageService composes existing services (no logic duplication)
- Weekly activity uses Redis pipeline (7 ZSCORE in 1 round-trip)
- Profile endpoints are thin; all logic delegated to service layer
- Frappe autoincrement requires explicit BIGINT override in after_migrate for existing tables (only creates BIGINT on new table creation)
- UUID polyfill stored functions for MariaDB 10.6 (no native UUID_TO_BIN/BIN_TO_UUID)
- RANGE partitioning managed via after_migrate with REMOVE PARTITIONING -> re-partition cycle for column type changes
- next_review changed from Datetime to Date on Memory State (already clamped to midnight)

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
- Phase 26 complete: Profile Page API (v1.7)
  - Plan 01: Level system constants, Pydantic models, Frappe whitelisted APIs
  - Plan 02: ProfilePageService aggregation + 7 FastAPI endpoints
  - Hero section, subject-filtered stats/mastery/activity, avatar selection, logout
  - Redis pipeline for weekly activity, mastery cache with 5-min TTL
- Phase 27 added: Memory State Redesign (v1.8)
  - BIGINT AUTO_INCREMENT PK replaces ~80-byte composite string PK
  - Item-level FSRS: 1 memory state per sub-element (question, matching pair, etc.) instead of per stage
  - Item IDs: UUID stored as BINARY(16), generated during content creation
  - RANGE partitioning by season_seq (INT) — instant archival via DROP PARTITION
  - 4 plans: schema foundation → content pipeline → FSRS rewrite → review/profile update
  - Depends on Phase 25 (FSRS) and Phase 26 (profile mastery)

### Blockers/Concerns

- Payment gateway integration (PRCHS-03) deferred to future work — all transactions currently manual approval
- No end-to-end test data available for Phase 23 verification (no pending transactions existed during implementation)

## Session Continuity

Last session: 2026-02-11
Stopped at: Completed 27-01-PLAN.md (Schema Foundation)
Resume file: None
Next action: Execute 27-02-PLAN.md (Content Pipeline)
