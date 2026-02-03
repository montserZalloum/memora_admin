# Project State

## Project Reference

See: .planning/PROJECT.md (updated 2026-02-02)

**Core value:** Students can track their learning progress and earn rewards (XP, streaks) with instant feedback and sub-second response times, even at 100K concurrent users.
**Current focus:** v1.1 COMPLETE - All 4 phases (Device Management, Game Sessions, Leaderboards, Scheduled Tasks) delivered

## Current Position

Phase: 11 of 11 (Scheduled Tasks)
Plan: 4 of 4 in current phase
Status: COMPLETE
Last activity: 2026-02-03 - Completed 11-04-PLAN.md (Scheduler Hooks Registration)

Progress: [██████████] 100% (40 of 40 plans completed across v1.0 and v1.1)

## Performance Metrics

**Velocity:**
- Total plans completed: 40 (v1.0 milestone + v1.1 Phases 8-11)
- Average duration: ~38 min
- Total execution time: ~20.5 hours

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
| 11. Scheduled Tasks | 4 | ~8 min | ~2 min |

**Recent Trend:**
- Last 5 plans: stable at ~2-3 min per plan (service/integration plans)
- Trend: Fast execution for foundation and service plans

*Updated after 11-04 completion*

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
- [11-02]: Delete streak_date on reset (clean state per wallet.py pattern)
- [11-02]: Session cleanup TTL -1 only (safety net, not primary expiry)
- [11-02]: Wildcard SCAN patterns for global + subject leaderboards
- [11-03]: trigger_task() passes triggered_by="Manual" for accurate log attribution
- [11-04]: Cron entries staggered (00:05, 00:10, 00:15) to avoid overlap

### Pending Todos

None.

### Blockers/Concerns

None. v1.1 COMPLETE. All phases delivered:

**Phase 8 - Device Management:**
- Fingerprint-based device recognition
- Lua script for atomic registration with race prevention
- Admin device removal with session invalidation

**Phase 9 - Game Sessions:**
- Single active session per user (atomic force-close)
- 1-hour TTL auto-expiry
- XP/analytics on session end
- Crash recovery endpoint

**Phase 10 - Leaderboards:**
- Composite score with tie-breaking
- Daily/weekly boards (global + per-subject)
- Dense rank calculation

**Phase 11 - Scheduled Tasks:**
- Task Admin role and Memora Task Run Log DocType
- prometheus_client metrics (Grafana compatible)
- streak_reset.py (00:05 daily)
- session_cleanup.py (hourly :15)
- leaderboard_reset.py (00:10 daily, Friday 00:15 weekly)
- All tasks registered in hooks.py scheduler_events

Research pitfalls addressed:
- Phase 8: Device limit race conditions (COMPLETE - addressed via Lua script)
- Phase 9: Session TTL memory leaks (COMPLETE - addressed via 1-hour TTL)
- Phase 10: Leaderboard hot key bottlenecks (COMPLETE - monitor; sharding available)
- Phase 11: Timezone-naive streak resets (COMPLETE - AMMAN_TZ in all tasks)
- Phase 11: Non-idempotent tasks (COMPLETE - has_run_today() in streak/leaderboard)

## Session Continuity

Last session: 2026-02-03
Stopped at: Completed 11-04-PLAN.md (Scheduler Hooks Registration)
Resume file: None
Next action: v1.1 COMPLETE - All phases delivered
