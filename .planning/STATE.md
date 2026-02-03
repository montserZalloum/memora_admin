# Project State

## Project Reference

See: .planning/PROJECT.md (updated 2026-02-02)

**Core value:** Students can track their learning progress and earn rewards (XP, streaks) with instant feedback and sub-second response times, even at 100K concurrent users.
**Current focus:** Phase 11 (Scheduled Tasks) - Plan 03 complete, Task Dashboard ready

## Current Position

Phase: 11 of 11 (Scheduled Tasks)
Plan: 3 of 4 in current phase
Status: In progress
Last activity: 2026-02-03 - Completed 11-03-PLAN.md (Task Dashboard)

Progress: [█████████░] 93% (38 of 41 plans completed across v1.0 and v1.1)

## Performance Metrics

**Velocity:**
- Total plans completed: 38 (v1.0 milestone + Phase 8-10 + 11-01, 11-03)
- Average duration: ~38 min
- Total execution time: ~20.4 hours

**By Phase:**

| Phase | Plans | Total | Avg/Plan |
|-------|-------|-------|----------|
| 1. Project Foundation | 3 | ~2h | ~40 min |
| 2. Authentication | 4 | ~3h | ~45 min |
| 3. Access Control | 4 | ~3h | ~45 min |
| 4. Progress Tracking | 3 | ~2.5h | ~50 min |
| 5. Gamification | 4 | ~3h | ~45 min |
| 6. Content Pipeline | 4 | ~3.5h | ~52 min |
| 7. Sync Mechanisms | 4 | ~3h | ~45 min |
| 8. Device Management | 2 | ~5 min | ~2.5 min |
| 9. Game Sessions | 4 | ~12 min | ~3 min |
| 10. Leaderboards | 3 | ~7 min | ~2.5 min |
| 11. Scheduled Tasks | 2 | ~5 min | ~2.5 min |

**Recent Trend:**
- Last 5 plans: stable at ~2-3 min per plan (service/integration plans)
- Trend: Fast execution for foundation and service plans

*Updated after 11-01 completion*

## Accumulated Context

### Decisions

Decisions are logged in PROJECT.md Key Decisions table.
Recent decisions affecting current work:

- [v1.0]: FastAPI sidecar for sub-20ms game API performance
- [v1.0]: Bitmap progress tracking for O(1) operations
- [v1.0]: Double-Gate access control for instant updates
- [v1.0]: Redis as hot data source with eventual consistency to MariaDB
- [v1.1 planning]: 4-phase structure following dependency-driven sequencing
- [08-01]: Fingerprint uses stable UA components (no versions) for device recognition
- [08-01]: Lua script for atomic device registration with race condition prevention
- [08-01]: Device hash structure: memora:devices:{user_id} with device:{id}:{attr} fields
- [08-02]: Device registration after credential verification, before session creation
- [08-02]: HTTP 429 for device limit exceeded (matches rate limiting semantics)
- [08-02]: Immediate session invalidation on admin device removal
- [09-01]: Lua script atomically force-closes existing session when creating new
- [09-01]: 1-hour TTL (3600s) for session auto-expiry
- [09-01]: Redis key pattern: memora:gamesession:{user_id}
- [09-02]: XP calculation function inlined in sessions.py to avoid circular imports
- [09-02]: 403 NO_ACTIVE_SESSION for end without active session
- [09-02]: Stage analytics pushed to INTERACTION_BUFFER_KEY via RPUSH
- [09-04]: 404 for absent session (resource not found, not access denied)
- [09-04]: GET /sessions/current endpoint for crash recovery
- [09-03]: Session check placed after access check, before unlock check (early failure pattern)
- [09-03]: Uses has_active_session() O(1) EXISTS check for session validation
- [10-01]: Composite score formula: xp + (1.0 - (timestamp % 1e9) / 1e9) for tie-breaking
- [10-01]: Dense rank via ZCOUNT of scores strictly greater
- [10-01]: Unranked users get rank = total + 1, xp = 0
- [10-01]: ISO week format (%G-W%V) for weekly board keys
- [10-03]: Leaderboard update happens AFTER wallet.award_xp() for accurate composite score
- [10-03]: Subject-specific boards updated when session provides subject_id
- [11-01]: Task Admin role created via after_install hook
- [11-01]: prometheus_client for task metrics (Grafana compatible)
- [11-03]: trigger_task() passes triggered_by="Manual" for accurate log attribution

### Pending Todos

None.

### Blockers/Concerns

None. Phase 11 infrastructure and dashboard complete:
- Memora Task Run Log DocType for execution history
- Task Admin role with full permissions
- prometheus_client metrics (TASK_RUNS, TASK_DURATION, USERS_PROCESSED, USERS_FAILED)
- task_utils.py utilities (date helpers, logging, idempotency, notifications)
- Task Dashboard at /app/task_dashboard for viewing history and manual triggers

Research pitfalls being addressed:
- Phase 8: Device limit race conditions (COMPLETE - addressed via Lua script)
- Phase 9: Session TTL memory leaks (COMPLETE - addressed via 1-hour TTL)
- Phase 10: Leaderboard hot key bottlenecks (COMPLETE - monitor; sharding available)
- Phase 11: Timezone-naive streak resets (IN PROGRESS - AMMAN_TZ constant ready)
- Phase 11: Non-idempotent tasks (IN PROGRESS - has_run_today() ready)

## Session Continuity

Last session: 2026-02-03
Stopped at: Completed 11-03-PLAN.md (Task Dashboard)
Resume file: None
Next action: Execute 11-02-PLAN.md (Streak Reset Task) or 11-04-PLAN.md (Leaderboard Archive Task)
