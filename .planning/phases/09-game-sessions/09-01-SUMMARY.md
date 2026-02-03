---
phase: 09-game-sessions
plan: 01
subsystem: api
tags: [redis, lua, game-session, pydantic, session-management]

# Dependency graph
requires:
  - phase: 08-device-management
    provides: Lua script pattern, service architecture
  - phase: 04-progress-tracking
    provides: Progress service for lesson completion
provides:
  - GameSessionService with atomic Lua script for session lifecycle
  - GameSession Pydantic models (request/response)
  - GAME_SESSION_TTL constant for 1-hour auto-expiry
affects:
  - 09-02 (session endpoints integration)
  - progress tracking (session context for completion)
  - leaderboard (session XP attribution)

# Tech tracking
tech-stack:
  added: []
  patterns: [lua-script-atomicity, one-session-per-user, force-close-existing]

key-files:
  created:
    - fastapi_app/models/game_session.py
    - fastapi_app/services/game_session.py
  modified:
    - fastapi_app/core/constants.py

key-decisions:
  - "Lua script atomically force-closes existing session when creating new"
  - "1-hour TTL (3600s) for session auto-expiry"
  - "Redis key pattern: memora:gamesession:{user_id}"

patterns-established:
  - "One session per user enforcement via atomic Lua script"
  - "Session force-close without notification (per CONTEXT.md)"

# Metrics
duration: 4min
completed: 2026-02-03
---

# Phase 9 Plan 01: Game Session Models and Service Summary

**GameSessionService with atomic Lua script for one-session-per-user enforcement, force-close on new session, and 1-hour TTL auto-expiry**

## Performance

- **Duration:** 4 min
- **Started:** 2026-02-03T06:15:00Z
- **Completed:** 2026-02-03T06:19:00Z
- **Tasks:** 3
- **Files modified:** 3

## Accomplishments

- GameSession Pydantic models with from_redis_hash classmethod for bytes/str handling
- GameSessionService with Lua script for atomic session start that force-closes existing
- GAME_SESSION_TTL constant (3600s) for consistent TTL across the codebase

## Task Commits

Each task was committed atomically:

1. **Task 1: Create GameSession Pydantic models** - `d9a4656` (feat)
2. **Task 2: Add GAME_SESSION_TTL constant** - `9a1fce3` (chore)
3. **Task 3: Create GameSessionService with Lua script** - `1c54eb2` (feat)

## Files Created/Modified

- `fastapi_app/models/game_session.py` - GameSession (5 fields), StageResult (5 fields), StartSession/EndSession Request/Response models
- `fastapi_app/services/game_session.py` - GameSessionService (204 lines) with Lua script, start/get/end/has session methods
- `fastapi_app/core/constants.py` - Added GAME_SESSION_TTL = 3600

## Decisions Made

1. **Lua script for atomic session start** - Single Redis round-trip that DELetes any existing session and creates new one. Prevents race conditions where multiple session starts could leave orphaned sessions.

2. **1-hour TTL for auto-expiry** - Sessions that are not explicitly ended (abandoned, network disconnect, etc.) auto-expire after 1 hour. This prevents zombie sessions from accumulating in Redis.

3. **Force-close without notification** - Per CONTEXT.md, existing session is silently closed when new session starts. No error, no notification to the old client. Simplifies client logic.

## Deviations from Plan

None - plan executed exactly as written.

## Issues Encountered

1. **Import sorting** - Ruff flagged unsorted imports in service file. Auto-fixed with `ruff check --fix`.

## User Setup Required

None - no external service configuration required.

## Next Phase Readiness

**Ready for 09-02:** GameSessionService is ready to be integrated into session endpoints. The service provides:
- `start_session()` - Creates session with lesson_id, subject_id, optional device_id
- `get_active_session()` - Returns current session or None
- `end_session()` - Ends session and returns session data for progress/XP processing
- `has_active_session()` - O(1) check if session exists

**Dependencies for 09-02:**
- Session endpoints (POST /sessions/start, POST /sessions/end)
- Integration with ProgressService for lesson completion
- Integration with WalletService for XP award

---
*Phase: 09-game-sessions*
*Completed: 2026-02-03*
