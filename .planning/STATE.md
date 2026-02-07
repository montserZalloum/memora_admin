# Project State

## Project Reference

See: .planning/PROJECT.md (updated 2026-02-03)

**Core value:** Students can track their learning progress and earn rewards (XP, streaks) with instant feedback and sub-second response times, even at 100K concurrent users.
**Current focus:** Milestone v1.3 — Phase 16: Admin Device Management (remaining)

## Current Position

Phase: 19 of 19 (Stage Content Editor)
Plan: 1 of 1 in current phase
Status: Phase 19 complete, Phase 16 pending
Last activity: 2026-02-07 — Completed 19-01-PLAN.md (Stage Content Editor Wiring)

Progress: [##################] ~98% (57/59 plans)

**Next Phase:** 16 (Admin Device Management) — 2 plans remaining

## Performance Metrics

**Velocity:**
- Total plans completed: 56
- Milestones shipped: 4

**By Milestone:**

| Milestone | Phases | Plans | Status |
|-----------|--------|-------|--------|
| v1.0 MVP | 7 | 30 | Shipped 2026-02-02 |
| v1.1 Feature Expansion | 4 | 13 | Shipped 2026-02-03 |
| v1.2 Plan System Enhancement | 1 | 4 | Shipped 2026-02-03 |
| v1.2.1 Gap Closure | 1 | 1 | Shipped 2026-02-03 |
| v1.3 Profiles & Devices | 5 | 10 | In Progress (8/10) |

## Accumulated Context

### Decisions

All decisions logged in PROJECT.md Key Decisions table.

**Phase 18 Plan 01 decisions:**
- Pipeline GETBIT instead of full bitmap load for <5ms response
- Route placed before /{subject} catch-all for correct routing
- Return bit_index in response for debugging/verification purposes

**Phase 17 Plan 02 decisions:**
- sse-starlette library for mature SSE support
- Subject summary first event (within 10ms target)
- Nested units/topics in track events for complete data
- Empty data for complete event (signal only)
- X-Accel-Buffering: no header for nginx SSE passthrough

**Phase 17 Plan 01 decisions:**
- 1 hour TTL on stats cache, matching HierarchyService
- Stats update only on non-replay completion (is_replay=False)
- Keep completed_bits loading for unlock state calculation
- Lazy initialization from bitmap on cold start

**Phase 15 decisions:**
- Store plan_id in Redis session JSON to avoid Frappe roundtrip on refresh
- Timezone hardcoded to Asia/Amman (removed from token)
- Role removed from token (all FastAPI users are players)
- Gender field optional in Player Profile schema
- Session JSON format: {fid, plan} for extensibility
- is_email helper uses simple @ check (no regex)
- Mobile lookup returns None on any error (generic failure)
- Plan change invalidates session immediately (no graceful transition)

**Phase 14 decisions:**
- Use individual Redis keys with pipeline MGET (not HEXPIRE) for Redis <7.4 compatibility
- Limit Frappe batch fetch to 50 profiles to avoid timeouts
- Empty display_name treated as missing, apply fallback
- Use set_value with expires_in_sec for Frappe cache TTL
- Hourly idempotency via Redis key rather than daily task log
- Rename avatar_url to avatar in LeaderboardEntry (file identifier, client constructs URL)
- Register ProfileService in app.state for pub/sub cache invalidation

### Pending Todos

None.

### Roadmap Evolution

- Phase 17 completed: Progress API Optimization (Caching + Streaming for scalable progress tracking)
- Phase 18 completed: Lesson Completion Status API (fast per-lesson completion lookups for topic pages at 100K scale)
- Phase 19 completed: Stage Content Editor (Edit Content button for lesson stage dialogs)

### Blockers/Concerns

**Research notes for v1.3:**
- N+1 query risk: Must use Redis pipeline for batch profile fetch from day 1 (RESOLVED: pipeline MGET implemented)
- Performance target: Leaderboard with profiles must stay under 25ms (was 20ms raw) (RESOLVED: batch fetch implemented)
- Session invalidation deferred: Removed devices work until token expiry (15 min acceptable)

## Session Continuity

Last session: 2026-02-07
Stopped at: Completed 19-01-PLAN.md (Stage Content Editor Wiring) - Phase 19 complete
Resume file: None
Next action: `/gsd:plan-phase 16` or `/gsd:execute-phase 16` (if planned)
