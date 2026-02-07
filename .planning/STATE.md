# Project State

## Project Reference

See: .planning/PROJECT.md (updated 2026-02-03)

**Core value:** Students can track their learning progress and earn rewards (XP, streaks) with instant feedback and sub-second response times, even at 100K concurrent users.
**Current focus:** Milestone v1.3 — Phase 16: Admin Device Management

## Current Position

Phase: 16 of 20 (Admin Device Management)
Plan: 1 of 2 in current phase
Status: In progress
Last activity: 2026-02-07 — Completed 16-01-PLAN.md

Progress: [####################] ~98% (63/64 plans)

**Completed:** 16-01 — Device Management APIs (sync + removal + bug fixes)
**Remaining:** 16-02 (Admin Device Management UI)

## Performance Metrics

**Velocity:**
- Total plans completed: 63
- Milestones shipped: 4

**By Milestone:**

| Milestone | Phases | Plans | Status |
|-----------|--------|-------|--------|
| v1.0 MVP | 7 | 30 | Shipped 2026-02-02 |
| v1.1 Feature Expansion | 4 | 13 | Shipped 2026-02-03 |
| v1.2 Plan System Enhancement | 1 | 4 | Shipped 2026-02-03 |
| v1.2.1 Gap Closure | 1 | 1 | Shipped 2026-02-03 |
| v1.3 Profiles & Devices | 8 | 16 | In Progress (15/16) |

## Accumulated Context

### Decisions

All decisions logged in PROJECT.md Key Decisions table.

**Phase 16 Plan 01 decisions:**
- Both device APIs use get_fastapi_redis() from access_sync (not frappe.cache)
- Session invalidation via r.delete() (no Frappe site prefix)
- Redis errors: APIs use frappe.throw (surfaces to admin), hook uses frappe.log_error (silent)
- device_sync.py hook kept as safety net for manual child table edits

**Phase 20 Plan 04 decisions:**
- FSRS processing in background task, not hot path (keeps completion under 10ms)
- Read from Memora Interaction Log (already synced), not Redis buffer directly
- Idempotency via 5min TTL Redis key per player:stage:creation
- Subject resolved from Lesson.subject (direct field) with hierarchy chain fallback
- Rating mapping: 0 fails=Good, 1 fail=Hard, 2+ fails=Again

**Phase 20 Plan 03 decisions:**
- Inline stats HINCRBY in pipeline instead of StatsService call (saves round-trip)
- Remove both ProgressServiceDep and StatsServiceDep from end_session signature
- Hearts bonus not applied to replays (fixed replay_xp only)
- Remove unused StatsServiceDep import from sessions.py (no other endpoint uses it)

**Phase 20 Plan 02 decisions:**
- Keep CompleteRequest/CompleteResponse with DEPRECATED comments (models/__init__.py re-exports)
- Remove calculate_xp_award from progress.py (duplicated in sessions.py)
- time_spent stays int pass-through (FSRS consumes as milliseconds)

**Phase 20 Plan 01 decisions:**
- XP fallback from Memora Settings base_lesson_xp (not hardcoded 10)
- max_hearts fallback from Memora Settings default_max_hearts
- xp_per_heart defaults to 0 (hearts bonus opt-in)
- fsrs_weights NOT exposed in settings API (FSRS task fetches directly)

**Phase 19 Plan 02 decisions:**
- Use stage.name (Frappe child table row identifier) for stage_id in JSON output
- JSON output structure unchanged - only value source changed

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
- Phase 19 completed: Stage Content Editor (Edit Content button + build generator stage_id fix)
- Phase 20 completed: Lesson Complete Pipeline Overhaul — 4/4 plans complete (hierarchy/settings + legacy removal + Lua hot path + FSRS background processor)
- Phase 16 in progress: Admin Device Management — 1/2 plans complete (device APIs + sync bug fixes)

### Blockers/Concerns

**Research notes for v1.3:**
- N+1 query risk: Must use Redis pipeline for batch profile fetch from day 1 (RESOLVED: pipeline MGET implemented)
- Performance target: Leaderboard with profiles must stay under 25ms (was 20ms raw) (RESOLVED: batch fetch implemented)
- Session invalidation deferred: Removed devices work until token expiry (15 min acceptable)

## Session Continuity

Last session: 2026-02-07
Stopped at: Completed 16-01-PLAN.md (device APIs + device_sync bug fixes)
Resume file: None
Next action: Phase 16-02 (Admin Device Management UI)
