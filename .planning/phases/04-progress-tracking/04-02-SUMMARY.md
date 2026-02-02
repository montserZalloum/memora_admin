---
phase: 04-progress-tracking
plan: 02
subsystem: api
tags: [redis, caching, hierarchy, frappe]

# Dependency graph
requires:
  - phase: 01-infrastructure-foundation
    provides: Redis connection pool, FrappeClient
  - phase: 04-progress-tracking
    plan: 01
    provides: Progress models (SubjectHierarchy, LessonInfo, etc.)
provides:
  - Frappe API for subject hierarchy with is_linear flags and bit_index allocation
  - HierarchyService with Redis caching for fast unlock calculations
  - Generic FrappeClient.call() method for arbitrary API calls
affects:
  - 04-progress-tracking (progress endpoint needs hierarchy for percentages)
  - 06-content-build (hierarchy cache invalidation on build)

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "Redis cache with TTL and explicit invalidation for read-heavy data"
    - "Frappe whitelisted method → FrappeClient.call() → HierarchyService pattern"

key-files:
  created:
    - memora_admin/memora_admin/api/hierarchy.py
    - fastapi_app/services/hierarchy.py
  modified:
    - fastapi_app/services/__init__.py
    - fastapi_app/services/frappe_client.py

key-decisions:
  - "Sequential bit_index allocation starting from 0 for dense bitmap storage"
  - "1 hour cache TTL for hierarchy (balances freshness vs. performance)"
  - "Public call() method added to FrappeClient for generic API calls"
  - "SCAN-based invalidate_all for pattern matching (safe for large key counts)"

patterns-established:
  - "Frappe API returns dict hierarchy, FastAPI caches as Pydantic model JSON"
  - "Service layer handles cache-aside pattern (check cache → miss → fetch → cache)"

# Metrics
duration: 2min
completed: 2026-02-02
---

# Phase 4 Plan 02: Subject Hierarchy and Caching Summary

**Frappe API for subject hierarchy with nested is_linear flags, HierarchyService with Redis caching for <20ms unlock calculations**

## Performance

- **Duration:** 2 min
- **Started:** 2026-02-02T11:45:55Z
- **Completed:** 2026-02-02T11:48:09Z
- **Tasks:** 2
- **Files modified:** 4

## Accomplishments
- Frappe whitelisted API returns full subject hierarchy (Subject → Tracks → Units → Topics → Lessons)
- Sequential bit_index allocation for bitmap progress tracking
- HierarchyService caches hierarchy in Redis with 1 hour TTL
- Cache invalidation methods ready for Phase 6 build pipeline integration

## Task Commits

Each task was committed atomically:

1. **Task 1: Create Frappe API for subject hierarchy** - `81af949` (feat)
2. **Task 2: Create HierarchyService with Redis caching** - `9e56d8a` (feat)

## Files Created/Modified
- `memora_admin/memora_admin/api/hierarchy.py` - Frappe whitelisted get_subject_hierarchy returning nested hierarchy
- `fastapi_app/services/hierarchy.py` - HierarchyService with cache-aside pattern for hierarchy lookups
- `fastapi_app/services/__init__.py` - Export HierarchyService
- `fastapi_app/services/frappe_client.py` - Added generic call() method for arbitrary API calls

## Decisions Made
- **Sequential bit allocation:** bit_index starts at 0 and increments per lesson for dense bitmap storage
- **1 hour TTL:** Balances cache freshness with performance; Phase 6 will add explicit invalidation on build
- **Generic call() method:** Added to FrappeClient for flexibility; specific methods still preferred for type safety
- **SCAN for invalidate_all:** Uses cursor-based iteration to avoid blocking Redis on large key counts

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 3 - Blocking] Added public call() method to FrappeClient**
- **Found during:** Task 2 (HierarchyService implementation)
- **Issue:** Plan specified `frappe_client.call()` pattern but FrappeClient only had internal `_call_method()`
- **Fix:** Added public `call(method, params)` method wrapping `_call_method`
- **Files modified:** fastapi_app/services/frappe_client.py
- **Verification:** HierarchyService imports and methods work correctly
- **Committed in:** 9e56d8a (Task 2 commit)

---

**Total deviations:** 1 auto-fixed (blocking)
**Impact on plan:** Essential for HierarchyService to call Frappe API. No scope creep.

## Issues Encountered
None

## User Setup Required
None - no external service configuration required.

## Next Phase Readiness
- Hierarchy service ready for progress endpoint integration (Plan 03)
- Cache invalidation methods available for Phase 6 build pipeline
- No blockers

---
*Phase: 04-progress-tracking*
*Completed: 2026-02-02*
