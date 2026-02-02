---
phase: 06-build-pipeline
plan: 04
subsystem: api
tags: [frappe-scheduler, redis-pubsub, asyncio, background-tasks, cache-invalidation]

# Dependency graph
requires:
  - phase: 06-02
    provides: JSON generator service for subject content
  - phase: 06-03
    provides: CDN publisher with atomic swap pattern
  - phase: 04-02
    provides: HierarchyService with cache invalidation method
provides:
  - Build worker scheduled task running every 2 minutes
  - Redis pub/sub cache invalidation channel
  - FastAPI background listener for hierarchy cache invalidation
  - End-to-end build pipeline wiring
affects: [07-testing, mobile-app-integration]

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "Frappe scheduler cron for build worker (*/2 * * * *)"
    - "Redis pub/sub for cross-service cache invalidation"
    - "asyncio background task for pub/sub listener"
    - "Redis INCR for atomic retry tracking"

key-files:
  created:
    - memora_admin/memora_admin/tasks/__init__.py
    - memora_admin/memora_admin/tasks/build_worker.py
    - fastapi_app/core/pubsub.py
  modified:
    - memora_admin/hooks.py
    - fastapi_app/main.py

key-decisions:
  - "Use frappe.cache.publish() for Redis pub/sub from Frappe side"
  - "Redis INCR for atomic retry count tracking"
  - "Store FrappeClient and HierarchyService in app.state for shared access"
  - "Dedicated Redis client for pub/sub (separate from pool)"

patterns-established:
  - "Redis pub/sub channel: memora:cache:invalidate"
  - "Pub/sub message format: {type, subject_id, timestamp}"
  - "Retry key pattern: memora:build:retry:{build_name}"

# Metrics
duration: 2min
completed: 2026-02-02
---

# Phase 06 Plan 04: Build Worker and Cache Invalidation Summary

**Scheduled build worker with Redis pub/sub cache invalidation wiring Frappe to FastAPI for end-to-end build pipeline**

## Performance

- **Duration:** 2 min
- **Started:** 2026-02-02T17:00:59Z
- **Completed:** 2026-02-02T17:03:25Z
- **Tasks:** 2
- **Files modified:** 5

## Accomplishments

- Build worker processes pending builds every 2 minutes via Frappe scheduler
- Redis-based retry tracking with atomic INCR (max 3 retries)
- Cache invalidation published to Redis pub/sub on build success
- FastAPI background task listens for invalidation and clears hierarchy cache
- FrappeClient and HierarchyService properly instantiated in lifespan

## Task Commits

Each task was committed atomically:

1. **Task 1: Create build worker scheduled task** - `0e95d83` (feat)
2. **Task 2: Create FastAPI pub/sub listener for cache invalidation** - `6746d95` (feat)

## Files Created/Modified

- `memora_admin/memora_admin/tasks/__init__.py` - Tasks module init
- `memora_admin/memora_admin/tasks/build_worker.py` - Scheduled worker for processing pending builds
- `memora_admin/hooks.py` - Added scheduler_events with cron pattern
- `fastapi_app/core/pubsub.py` - Redis pub/sub listener for cache invalidation
- `fastapi_app/main.py` - Wired FrappeClient, HierarchyService, and pubsub background task

## Decisions Made

- **frappe.cache.publish()**: Used Frappe's cache.publish() for Redis pub/sub from build worker (cleaner than raw Redis)
- **Redis INCR for retries**: Atomic increment for retry tracking prevents race conditions
- **Services in app.state**: FrappeClient and HierarchyService stored in app.state for access by endpoints and pubsub handler
- **Dedicated pub/sub client**: Separate Redis client for pub/sub to avoid blocking connection pool

## Deviations from Plan

None - plan executed exactly as written.

## Issues Encountered

None.

## User Setup Required

None - no external service configuration required.

## Next Phase Readiness

- Phase 6 (Build Pipeline) is complete
- End-to-end flow: Content edit -> Build trigger -> JSON generation -> CDN upload -> Cache invalidation
- Ready for Phase 7 (Testing and Deployment)

---
*Phase: 06-build-pipeline*
*Completed: 2026-02-02*
