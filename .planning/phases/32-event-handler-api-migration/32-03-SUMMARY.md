---
phase: 32-event-handler-api-migration
plan: 03
subsystem: tasks
tags: [redis, fsrs, profile-cache, scheduled-tasks, identity-migration]

# Dependency graph
requires:
  - phase: 32-01
    provides: "get_fastapi_redis() pattern established in access_sync.py; event handlers migrated to doc.name"
  - phase: 32-02
    provides: "Frappe APIs migrated to docname identity; user field removed from schema"
provides:
  - "profile_cache.py queries by name field and writes cache keys as memora:profile:PLAYER-#####"
  - "profile_cache.py uses get_fastapi_redis() for correct Redis namespace shared with FastAPI"
  - "fsrs_processor.py resolves player seasons via pp.name SQL JOIN (includes PLAYER-##### profiles)"
affects: []

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "Scheduled tasks use get_fastapi_redis() for data consumed by FastAPI sidecar"
    - "SQL JOINs on Player Profile use pp.name (docname) not pp.user"

key-files:
  created: []
  modified:
    - "memora_admin/tasks/profile_cache.py"
    - "memora_admin/tasks/fsrs_processor.py"

key-decisions:
  - "Left fsrs_processor.py get_redis() unchanged for FSRS-internal keys (memora:fsrs:*, memora:task_ran:*) since they are not consumed by FastAPI"
  - "Kept redis import in profile_cache.py for redis.Redis type hint in _do_warm_cache() signature"

patterns-established:
  - "Scheduled tasks that write data consumed by FastAPI must use get_fastapi_redis(), not frappe.conf.redis_cache"

# Metrics
duration: 2min
completed: 2026-02-12
---

# Phase 32 Plan 03: Scheduled Tasks Migration Summary

**Migrated profile_cache.py and fsrs_processor.py from old user-based identity model to PLAYER-##### docname with correct Redis namespace**

## Performance

- **Duration:** 2 min
- **Started:** 2026-02-12T16:39:20Z
- **Completed:** 2026-02-12T16:41:16Z
- **Tasks:** 2
- **Files modified:** 2

## Accomplishments
- profile_cache.py now queries Player Profile by `name` field (not `user`), writes cache keys as `memora:profile:PLAYER-#####`, and uses `get_fastapi_redis()` for correct Redis namespace
- fsrs_processor.py now resolves player seasons via `pp.name` SQL JOIN, ensuring PLAYER-##### profiles are included in FSRS spaced repetition processing
- Removed old `get_redis()` function from profile_cache.py that used `frappe.conf.redis_cache` (wrong Redis namespace)
- Added Phase 32 identity model comments to both files

## Task Commits

Each task was committed atomically:

1. **Task 1: Migrate profile_cache.py to docname identity + get_fastapi_redis()** - `2f97b24` (fix)
2. **Task 2: Migrate fsrs_processor.py SQL to use pp.name** - `e9d4261` (fix)

## Files Created/Modified
- `memora_admin/tasks/profile_cache.py` - Profile cache pre-warming: queries by name, writes PLAYER-##### keys, uses get_fastapi_redis()
- `memora_admin/tasks/fsrs_processor.py` - FSRS processor: SQL JOIN uses pp.name for player resolution

## Decisions Made
- Left `get_redis()` in fsrs_processor.py unchanged -- it serves FSRS-internal keys (`memora:fsrs:*`, `memora:task_ran:*`) that are only consumed within the scheduler, not by FastAPI. The critical fix was the SQL JOIN that determines which players get FSRS processing.
- Kept `import redis` in profile_cache.py because `redis.Redis` is used as a type hint in `_do_warm_cache()` function signature.

## Deviations from Plan

None - plan executed exactly as written.

## Issues Encountered

None

## User Setup Required

None - no external service configuration required.

## Next Phase Readiness
- All 3 plans of Phase 32 (Event Handler & API Migration) are complete
- The entire v2.0 Mobile-First Player Authentication milestone is complete
- All Frappe event handlers, APIs, and scheduled tasks use PLAYER-##### docname identity
- All Redis keys consistently use PLAYER-##### across both Frappe and FastAPI

## Self-Check: PASSED

All files verified present, all commits verified in git log.

---
*Phase: 32-event-handler-api-migration*
*Completed: 2026-02-12*
