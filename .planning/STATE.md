# Project State

## Project Reference

See: .planning/PROJECT.md (updated 2026-02-02)

**Core value:** Students can track their learning progress and earn rewards (XP, streaks) with instant feedback and sub-second response times, even at 100K concurrent users.
**Current focus:** Phase 10 complete (verified), ready for Phase 11 (Scheduled Tasks)

## Current Position

Phase: 10 of 11 (Leaderboards)
Plan: 3 of 3 in current phase
Status: Phase verified (5/5 must-haves passed)
Last activity: 2026-02-03 - All 3 plans executed, verification passed

Progress: [█████████░] 88% (36 of 41 plans completed across v1.0 and v1.1)

## Performance Metrics

**Velocity:**
- Total plans completed: 33 (v1.0 milestone + Phase 8 + Phase 9 + 10-01)
- Average duration: ~40 min
- Total execution time: ~20.3 hours

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

**Recent Trend:**
- Last 5 plans: stable at ~2-3 min per plan (service/integration plans)
- Trend: Fast execution for service-only and integration plans

*Updated after 10-03 completion*

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

### Pending Todos

None.

### Blockers/Concerns

None. Phase 10 complete:
- LeaderboardService with ZSET operations (get_top, get_my_rank, update_leaderboards)
- Pydantic models (LeaderboardEntry, LeaderboardResponse, MyRankResponse, LeaderboardType)
- compute_composite_score function for "earlier achiever wins" tie-breaking
- Dense ranking via ZCOUNT for fair position display
- API endpoints for top N and my-rank queries
- Session integration: leaderboards updated on every XP award

Research identified key pitfalls to address:
- Phase 8: Device limit race conditions (COMPLETE - addressed via Lua script)
- Phase 9: Session TTL memory leaks (COMPLETE - addressed via 1-hour TTL), connection pool exhaustion
- Phase 10: Leaderboard hot key bottlenecks (COMPLETE - monitor; sharding strategy available if needed)
- Phase 11: Timezone-naive streak resets (Asia/Amman enforcement), non-idempotent tasks

## Session Continuity

Last session: 2026-02-03
Stopped at: Phase 10 complete, verification passed (5/5)
Resume file: None
Next action: Plan Phase 11 - Scheduled Tasks
