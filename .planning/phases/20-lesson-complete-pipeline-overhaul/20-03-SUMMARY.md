---
phase: 20
plan: 03
subsystem: fastapi-sessions
tags: [lua-script, redis-pipeline, hot-path, performance, hearts-xp, atomic-completion]
requires:
  - "20-01 (LessonInfo.max_hearts, GamificationSettings.xp_per_heart)"
  - "20-02 (StageResult.time_spent as milliseconds, legacy endpoint removed)"
provides:
  - "SESSION_COMPLETE_SCRIPT Lua for atomic session completion"
  - "complete_session() method on GameSessionService"
  - "Rewritten end_session endpoint with ~6-7 Redis round-trips (down from 17+N)"
  - "Hearts bonus XP calculation (remaining_hearts * xp_per_heart)"
affects:
  - "20-04 (integration testing of the full pipeline)"
tech-stack:
  added: []
  patterns:
    - "Lua script for multi-key atomic operations (DEL + SETBIT + SADD + RPUSH)"
    - "Redis pipeline for batching independent writes (XP + dirty + stats)"
    - "Hearts bonus XP added before streak multiplier"
key-files:
  created: []
  modified:
    - "fastapi_app/services/game_session.py"
    - "fastapi_app/api/v1/endpoints/sessions.py"
key-decisions:
  - decision: "Inline stats HINCRBY in pipeline instead of StatsService call"
    reason: "Eliminates separate service dependency and extra round-trip; stats are simple atomic increments"
  - decision: "Remove both ProgressServiceDep and StatsServiceDep from end_session"
    reason: "Lua script handles SETBIT+dirty atomically; pipeline handles stats inline"
  - decision: "Remove StatsServiceDep import from sessions.py"
    reason: "No other endpoint in sessions.py uses it; avoids unused import lint error"
  - decision: "Hearts bonus not applied to replays"
    reason: "Replay gets fixed replay_xp only; hearts bonus rewards fresh completion skill"
duration: "4m 24s"
completed: "2026-02-07"
---

# Phase 20 Plan 03: Session End Hot Path Optimization Summary

**One-liner:** Lua script + Redis pipeline reduces POST /sessions/end from 17+N to ~6-7 Redis round-trips with atomic session completion and hearts bonus XP.

## Performance

| Metric | Value |
|--------|-------|
| Duration | 4m 24s |
| Started | 2026-02-07T09:46:52Z |
| Completed | 2026-02-07T09:51:16Z |
| Tasks | 2/2 |
| Files modified | 2 |

## Accomplishments

### SESSION_COMPLETE_SCRIPT Lua Script
- Atomic operation combining: session DEL, progress SETBIT, dirty SADD, and batch RPUSH interactions
- Single Lua call replaces 4 separate Redis calls + N individual RPUSH calls
- Returns is_replay (from SETBIT return value: 0=first, 1=replay) for idempotent completion
- Returns full session data (HGETALL flat output) for endpoint consumption

### complete_session() Method
- Added to GameSessionService alongside existing methods (no breaking changes)
- Lazy-loaded script registration (same pattern as `_get_start_script`)
- Returns `(success, is_replay, session_data)` tuple
- Handles bytes/str decoding of Lua script results

### Rewritten end_session Endpoint
- Redis round-trips reduced from 17+N to ~6-7:
  - RT1: HGETALL session
  - RT2: GET hierarchy (cache hit)
  - RT3: Lua complete_session (DEL + SETBIT + SADD + batch RPUSH)
  - RT4: GET settings (cache hit)
  - RT5: Lua streak update
  - RT6: Pipeline (XP + dirty wallet + stats HINCRBY)
  - RT7: Leaderboard updates
- Removed `ProgressServiceDep` from endpoint (Lua handles SETBIT)
- Removed `StatsServiceDep` from endpoint (inline pipeline HINCRBY)
- Interaction buffer batch: all stage JSONs pushed in single Lua RPUSH

### Hearts Bonus XP
- Formula: `remaining_hearts * xp_per_heart` (added before streak multiplier)
- `hearts_remaining = max(0, max_hearts - total_fails)`
- Replay completions get no hearts bonus (fixed replay_xp only)
- Streak multiplier applies to both fresh and replay (unchanged behavior)

## Task Commits

| # | Task | Commit | Key Changes |
|---|------|--------|-------------|
| 1 | SESSION_COMPLETE_SCRIPT Lua + complete_session | `0b1f5b4` | Lua script, complete_session() method, constants import |
| 2 | Rewrite end_session endpoint | `f3c9f97` | Lua + pipeline hot path, hearts XP, remove ProgressServiceDep/StatsServiceDep |

## Files Modified

| File | Changes |
|------|---------|
| `fastapi_app/services/game_session.py` | +128 lines: SESSION_COMPLETE_SCRIPT Lua, _get_complete_script, complete_session method |
| `fastapi_app/api/v1/endpoints/sessions.py` | +73/-52 lines: Rewritten end_session, hearts XP in _calculate_xp_award, removed unused imports |

## Decisions Made

1. **Inline stats HINCRBY in pipeline instead of StatsService call** -- Eliminates separate service dependency and extra round-trip. Stats are simple atomic HINCRBY increments that fit naturally in the pipeline alongside XP and dirty wallet operations.

2. **Remove both ProgressServiceDep and StatsServiceDep from end_session** -- The Lua script handles SETBIT + dirty progress atomically; the pipeline handles stats inline. Neither service dependency is needed by end_session anymore.

3. **Remove StatsServiceDep import from sessions.py entirely** -- No other endpoint in sessions.py uses StatsServiceDep, so the import was unused after removing it from end_session. Cleaned up to avoid ruff lint errors.

4. **Hearts bonus not applied to replays** -- Replay completions receive fixed replay_xp only. Hearts bonus rewards fresh completion skill (completing lesson with hearts remaining). This aligns with the replay_xp being a fixed small reward.

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 2 - Missing Critical] Removed unused StatsServiceDep import**
- **Found during:** Task 2
- **Issue:** Plan specified removing ProgressServiceDep but StatsServiceDep was also no longer used (stats now inline in pipeline). Leaving it would cause ruff unused-import lint error.
- **Fix:** Removed StatsServiceDep from both the import block and endpoint signature.
- **Files modified:** `fastapi_app/api/v1/endpoints/sessions.py`
- **Commit:** `f3c9f97`

## Issues Encountered

None.

## Next Phase Readiness

### For Plan 20-04 (Integration Testing)
- SESSION_COMPLETE_SCRIPT and complete_session() are ready for end-to-end testing
- Hearts bonus XP calculation is testable via `_calculate_xp_award` function
- Pipeline-based stats updates need verification with real Redis
- is_replay detection via SETBIT return value is testable
- Leaderboard update flow unchanged (no new testing needed)

### Blockers
None.

## Self-Check: PASSED
