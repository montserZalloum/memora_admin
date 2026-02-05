# Project State

## Project Reference

See: .planning/PROJECT.md (updated 2026-02-03)

**Core value:** Students can track their learning progress and earn rewards (XP, streaks) with instant feedback and sub-second response times, even at 100K concurrent users.
**Current focus:** Milestone v1.3 Complete

## Current Position

Phase: 17 of 17 (Progress API Optimization)
Plan: 2 of 2 in current phase
Status: Phase 17 complete - Milestone v1.3 complete
Last activity: 2026-02-05 — Completed 17-02 (SSE Streaming)

Progress: [##################] 100% (56/56 plans)

## Performance Metrics

**Velocity:**
- Total plans completed: 56
- Milestones shipped: 5

**By Milestone:**

| Milestone | Phases | Plans | Status |
|-----------|--------|-------|--------|
| v1.0 MVP | 7 | 30 | Shipped 2026-02-02 |
| v1.1 Feature Expansion | 4 | 13 | Shipped 2026-02-03 |
| v1.2 Plan System Enhancement | 1 | 4 | Shipped 2026-02-03 |
| v1.2.1 Gap Closure | 1 | 1 | Shipped 2026-02-03 |
| v1.3 Profiles & Devices | 4 | 8 | Shipped 2026-02-05 |

## Accumulated Context

### Decisions

All decisions logged in PROJECT.md Key Decisions table.

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

### Blockers/Concerns

None - all v1.3 milestones complete.

## Session Continuity

Last session: 2026-02-05
Stopped at: Completed 17-02-PLAN.md (SSE Streaming) - Phase 17 complete
Resume file: None
Next action: Project complete - all phases delivered
