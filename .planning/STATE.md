# Project State

## Project Reference

See: .planning/PROJECT.md (updated 2026-02-03)

**Core value:** Students can track their learning progress and earn rewards (XP, streaks) with instant feedback and sub-second response times, even at 100K concurrent users.
**Current focus:** Milestone v1.3 — Phase 15: JWT Simplification

## Current Position

Phase: 15 of 16 (JWT Simplification)
Plan: 1 of 3 in current phase
Status: In progress
Last activity: 2026-02-05 — Completed 15-01-PLAN.md

Progress: [###############---] 96% (52/54 plans)

## Performance Metrics

**Velocity:**
- Total plans completed: 52
- Milestones shipped: 4

**By Milestone:**

| Milestone | Phases | Plans | Status |
|-----------|--------|-------|--------|
| v1.0 MVP | 7 | 30 | Shipped 2026-02-02 |
| v1.1 Feature Expansion | 4 | 13 | Shipped 2026-02-03 |
| v1.2 Plan System Enhancement | 1 | 4 | Shipped 2026-02-03 |
| v1.2.1 Gap Closure | 1 | 1 | Shipped 2026-02-03 |
| v1.3 Profiles & Devices | 3 | 6 | In Progress |

## Accumulated Context

### Decisions

All decisions logged in PROJECT.md Key Decisions table.

**Phase 15 decisions:**
- Store plan_id in Redis session JSON to avoid Frappe roundtrip on refresh
- Timezone hardcoded to Asia/Amman (removed from token)
- Role removed from token (all FastAPI users are players)
- Gender field optional in Player Profile schema
- Session JSON format: {fid, plan} for extensibility

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

### Blockers/Concerns

**Research notes for v1.3:**
- N+1 query risk: Must use Redis pipeline for batch profile fetch from day 1 (RESOLVED: pipeline MGET implemented)
- Performance target: Leaderboard with profiles must stay under 25ms (was 20ms raw) (RESOLVED: batch fetch implemented)
- Session invalidation deferred: Removed devices work until token expiry (15 min acceptable)

## Session Continuity

Last session: 2026-02-05
Stopped at: Completed 15-01-PLAN.md
Resume file: None
Next action: Execute 15-02-PLAN.md (Login flow with identifier)
