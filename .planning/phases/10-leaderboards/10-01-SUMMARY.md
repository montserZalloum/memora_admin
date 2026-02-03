---
phase: 10-leaderboards
plan: 01
subsystem: api
tags: [redis, zset, leaderboard, pydantic, rankings]

# Dependency graph
requires:
  - phase: 05-gamification
    provides: Wallet service with XP tracking
provides:
  - LeaderboardService for Redis ZSET operations
  - Pydantic models for leaderboard API responses
  - Composite score function for tie-breaking
affects: [10-02 API endpoints, 05 wallet integration]

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "Composite score: XP + inverted timestamp for tie-breaking"
    - "Dense ranking: same score = same rank via ZCOUNT"
    - "Date-suffixed keys for daily/weekly boards"

key-files:
  created:
    - fastapi_app/models/leaderboard.py
    - fastapi_app/services/leaderboard.py
  modified: []

key-decisions:
  - "Composite score formula: xp + (1.0 - (timestamp % 1e9) / 1e9)"
  - "Dense rank via ZCOUNT of scores strictly greater"
  - "Unranked users get rank = total + 1, xp = 0"
  - "ISO week format (%G-W%V) for weekly board keys"

patterns-established:
  - "LeaderboardService: ZADD for all-time, ZINCRBY for daily/weekly"
  - "Neighbor fetching via ZRANGE around player position"
  - "xp_to_next calculation: above_xp - my_xp + 1"

# Metrics
duration: 3min
completed: 2026-02-03
---

# Phase 10 Plan 01: Leaderboard Service Foundation Summary

**Redis ZSET-backed leaderboard service with composite scoring for tie-breaking and dense ranking for fair position display**

## Performance

- **Duration:** 3 min
- **Started:** 2026-02-03T09:21:53Z
- **Completed:** 2026-02-03T09:24:21Z
- **Tasks:** 2
- **Files created:** 2

## Accomplishments

- Pydantic models for leaderboard API responses (LeaderboardEntry, LeaderboardResponse, MyRankResponse, LeaderboardType)
- LeaderboardService with Redis ZSET operations (get_top, get_my_rank, update_leaderboards)
- Composite score function implementing "earlier achiever wins" tie-breaking
- Dense ranking using ZCOUNT for players sharing same score

## Task Commits

Each task was committed atomically:

1. **Task 1: Create Pydantic models** - `2985388` (feat)
2. **Task 2: Create LeaderboardService** - `add71a1` (feat)

## Files Created

- `fastapi_app/models/leaderboard.py` - Pydantic models for leaderboard responses (LeaderboardType, LeaderboardEntry, LeaderboardResponse, MyRankResponse)
- `fastapi_app/services/leaderboard.py` - LeaderboardService with ZSET operations and composite_score function

## Decisions Made

1. **Composite score formula:** `xp + (1.0 - (timestamp % 1e9) / 1e9)` - Earlier timestamps produce smaller fractional parts, so higher score ranks better when XP is equal
2. **Dense rank calculation:** Use ZCOUNT with exclusive lower bound `(score` to count scores strictly greater than player's score
3. **Unranked users:** Return rank = total_players + 1, xp = 0, empty neighbors list
4. **ISO week format:** Use `%G-W%V` for weekly keys to handle year boundaries correctly
5. **XP to next tier:** Calculate as `above_xp - my_xp + 1` (need to exceed, not just match)

## Deviations from Plan

None - plan executed exactly as written.

## Issues Encountered

None - ruff linting tool not available in environment but syntax validation and import checks passed.

## User Setup Required

None - no external service configuration required.

## Next Phase Readiness

- LeaderboardService ready for API endpoint wiring (Plan 10-02)
- Models ready for response serialization
- Service needs integration with wallet.award_xp() flow for automatic updates

---
*Phase: 10-leaderboards*
*Completed: 2026-02-03*
