---
phase: 28-tech-debt-reliability-fixes
plan: 02
subsystem: api
tags: [fastapi, dependency-injection, redis, dead-code-removal, refactoring]

# Dependency graph
requires:
  - phase: 28-tech-debt-reliability-fixes
    provides: "Shared Redis constants (28-01)"
provides:
  - "DRY deps.py with RedisClient sub-dependency pattern for all 16 service factories"
  - "Clean redis.py with only pool creation and verification"
  - "Clean progress models without deprecated/dead code"
  - "Simplified services/__init__.py"
affects: [any-future-service-addition]

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "RedisClient sub-dependency injection: all service factories use `redis_client: RedisClient` parameter"
    - "Direct service imports in deps.py (not via services/__init__.py)"

key-files:
  created: []
  modified:
    - "fastapi_app/api/deps.py"
    - "fastapi_app/core/redis.py"
    - "fastapi_app/models/progress.py"
    - "fastapi_app/models/__init__.py"
    - "fastapi_app/services/__init__.py"

key-decisions:
  - "RedisClient sub-dependency via FastAPI Depends() injection eliminates 16 copy-pasted redis.Redis constructions"
  - "services/__init__.py simplified to bare docstring since deps.py uses direct imports"
  - "Removed SSE models (dead since Phase 24 WebSocket migration)"
  - "Kept slow_redis_threshold_ms in config.py (harmless, separate cleanup if needed)"

patterns-established:
  - "RedisClient sub-dependency: new service factories should use `redis_client: RedisClient` parameter, not Request"
  - "Direct imports: deps.py imports services directly from their modules, not via services/__init__.py"

# Metrics
duration: 3min
completed: 2026-02-11
---

# Phase 28 Plan 02: DRY deps.py & Dead Code Removal Summary

**Consolidated 16 service factory Redis boilerplate into RedisClient sub-dependency and removed 136 lines of dead code (log_slow_redis, deprecated models, SSE models, stale exports)**

## Performance

- **Duration:** 3 min
- **Started:** 2026-02-11T21:09:18Z
- **Completed:** 2026-02-11T21:12:47Z
- **Tasks:** 2
- **Files modified:** 5

## Accomplishments
- All 16 deps.py service factories now use `redis_client: RedisClient` sub-dependency instead of manual `redis.Redis(connection_pool=...)` construction
- Removed unused `log_slow_redis` decorator and its imports from `core/redis.py`
- Removed deprecated `CompleteRequest`/`CompleteResponse` models (dead since Phase 20)
- Removed dead SSE streaming models (dead since Phase 24 WebSocket migration)
- Simplified `services/__init__.py` to bare module docstring
- Sorted `__all__` in `models/__init__.py` per ruff RUF022

## Task Commits

Each task was committed atomically:

1. **Task 1: Consolidate deps.py service factories to use RedisClient sub-dependency** - `5b974a8` (refactor)
2. **Task 2: Remove dead code (log_slow_redis, deprecated models, stale exports)** - `99b5f2c` (chore)

## Files Created/Modified
- `fastapi_app/api/deps.py` - 16 service factories refactored to use RedisClient sub-dependency (-32/+16 lines)
- `fastapi_app/core/redis.py` - Removed log_slow_redis decorator and unused imports
- `fastapi_app/models/progress.py` - Removed CompleteRequest, CompleteResponse, and 4 SSE models
- `fastapi_app/models/__init__.py` - Removed deprecated exports, sorted __all__
- `fastapi_app/services/__init__.py` - Simplified to bare module docstring

## Decisions Made
- **RedisClient sub-dependency pattern:** FastAPI's `Depends()` system resolves `get_redis` automatically when a factory declares `redis_client: RedisClient` as a parameter. This is idiomatic FastAPI and eliminates all boilerplate.
- **Kept `slow_redis_threshold_ms` in config:** The setting in `config.py` is harmless and removing it is a separate concern. Only the decorator that consumed it was removed.
- **Removed SSE models:** Verified no imports exist anywhere in the codebase. SSE was replaced by WebSockets in Phase 24.
- **Simplified services/__init__.py:** All 16 service imports in deps.py are direct (e.g., `from fastapi_app.services.access import AccessService`). The `__init__.py` re-exports were unused and out of date.

## Deviations from Plan

None - plan executed exactly as written.

## Issues Encountered
- Ruff flagged `__all__` as unsorted (RUF022) after removing CompleteRequest/CompleteResponse entries. Fixed with `--unsafe-fixes` and cleaned up misplaced category comments.

## User Setup Required
None - no external service configuration required.

## Next Phase Readiness
- deps.py pattern is clean for future service additions (just add `redis_client: RedisClient` parameter)
- Ready for 28-03 (Lua script safety) and 28-04 (input validation)
- No blockers

---
*Phase: 28-tech-debt-reliability-fixes*
*Completed: 2026-02-11*
