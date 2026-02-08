# Project State

## Project Reference

See: .planning/PROJECT.md (updated 2026-02-07)

**Core value:** Students can track their learning progress and earn rewards (XP, streaks) with instant feedback and sub-second response times, even at 100K concurrent users.
**Current focus:** v1.5 Real-Time Notifications — Phase 24 (Real-Time Subscription Notifications) COMPLETE

## Current Position

Phase: 24 (Real-Time Subscription Notifications)
Plan: 02 of 2 (WebSocket endpoint + pub/sub integration)
Status: Phase complete
Last activity: 2026-02-08 — Completed 24-02-PLAN.md

Progress: [██████████] 100%

## Performance Metrics

**Velocity:**
- Total plans completed: 71
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

### Pending Todos

1. Add dedicated subscriptions endpoint (`GET /api/v1/subscriptions`) — see `.planning/todos/pending/2026-02-08-add-subscriptions-endpoint.md`

### Roadmap Evolution

- Phase 24 complete: Real-Time Subscription Notifications (WebSockets + Redis Pub/Sub)
  - Deprecated SSE removed, replaced by WebSocket notification system
  - Scales to 100K+ concurrent users, <20ms propagation
  - Leverages existing Redis pub/sub infrastructure

### Blockers/Concerns

- Payment gateway integration (PRCHS-03) deferred to future work — all transactions currently manual approval
- No end-to-end test data available for Phase 23 verification (no pending transactions existed during implementation)

## Session Continuity

Last session: 2026-02-08
Stopped at: Completed 24-02-PLAN.md (WebSocket endpoint + pub/sub integration) — Phase 24 complete
Resume file: None
Next action: None — all planned phases complete
