---
phase: 04-progress-tracking
plan: 03
subsystem: api
tags: [redis, bitmap, unlock, endpoint, progress]

# Dependency graph
requires:
  - phase: 04-progress-tracking
    plan: 01
    provides: Progress models, ProgressService
  - phase: 04-progress-tracking
    plan: 02
    provides: HierarchyService with caching
provides:
  - POST /progress/complete endpoint with unlock enforcement
  - Unlock state calculation service (calculate_unlock_state, is_lesson_unlocked)
  - Progress/Hierarchy service dependency injection
affects:
  - 04-04 (progress query endpoints may need unlock state)
  - 05-rewards (XP/wallet integration on completion)

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "Unlock state calculated on-the-fly from bitmap + hierarchy"
    - "Access check before unlock check (fail fast on no access)"
    - "Idempotent completion via SETBIT return value"

key-files:
  created:
    - fastapi_app/services/unlock.py
  modified:
    - fastapi_app/services/__init__.py
    - fastapi_app/api/deps.py
    - fastapi_app/api/v1/endpoints/progress.py

key-decisions:
  - "Unlock state computed on-demand, not stored (avoids stale cache)"
  - "Subject-level access key (SUB-{subject}) for Gate 2 check"
  - "Replay status logged but not returned (wallet integration in Phase 5)"

patterns-established:
  - "is_linear enforcement at all hierarchy levels"
  - "First item always unlocked rule"
  - "100% completion required to unlock next item"

# Metrics
duration: 2min
completed: 2026-02-02
---

# Phase 4 Plan 03: Lesson Completion Endpoint Summary

**POST /progress/complete endpoint with unlock state enforcement and Double-Gate access validation**

## Performance

- **Duration:** 2 min
- **Started:** 2026-02-02T11:52:17Z
- **Completed:** 2026-02-02T11:54:42Z
- **Tasks:** 3
- **Files modified:** 4

## Accomplishments

- Created unlock state calculation service with is_linear enforcement at Track/Unit/Topic levels
- Added ProgressService and HierarchyService dependency injection
- Implemented POST /progress/complete endpoint with:
  - Subject/lesson existence validation
  - Gate 2 access check (SUB-{subject} key)
  - Unlock state enforcement (403 on locked lesson)
  - Idempotent completion (replays return 200 OK)
  - Structured logging with replay status

## Task Commits

Each task was committed atomically:

1. **Task 1: Create unlock state calculation service** - `e61b87b` (feat)
2. **Task 2: Add progress service dependencies** - `d00729c` (feat)
3. **Task 3: Create completion endpoint** - `0a1e1cf` (feat)

## Files Created/Modified

- `fastapi_app/services/unlock.py` - Unlock state calculation with is_linear enforcement
- `fastapi_app/services/__init__.py` - Export unlock functions
- `fastapi_app/api/deps.py` - ProgressServiceDep, HierarchyServiceDep, FrappeClient singleton
- `fastapi_app/api/v1/endpoints/progress.py` - POST /progress/complete endpoint

## Key Implementation Details

### Unlock State Calculation

```python
def calculate_unlock_state(hierarchy: SubjectHierarchy, completed_bits: set[int]) -> dict[str, bool]:
    """
    Per CONTEXT.md unlock rules:
    - First item in any sequence is ALWAYS unlocked
    - is_linear at Track level: units must complete in order
    - is_linear at Unit level: topics must complete in order
    - is_linear at Topic level: lessons must complete in order
    - Unlock requires 100% completion of previous item
    """
```

Computed on-demand from bitmap + hierarchy, not cached (avoids stale state).

### Completion Endpoint Flow

1. Get hierarchy from cache (HierarchyService)
2. Find lesson by ID in hierarchy
3. Check subject access (SUB-{subject} key)
4. Get completed bits for unlock calculation
5. Check if lesson is unlocked (403 if locked)
6. Mark complete via SETBIT (idempotent)
7. Log completion with replay status
8. Return { success: true }

### Verification Tests

```
Test 1 PASSED: First lesson unlocked, second locked with no completions
Test 2 PASSED: Second lesson unlocked after first complete
```

## Decisions Made

| ID | Decision | Rationale |
|----|----------|-----------|
| 04-03-01 | Compute unlock state on-demand | Avoids stale cached unlock states after completions |
| 04-03-02 | Use SUB-{subject} access key | Consistent with existing grant key pattern |
| 04-03-03 | Log replay but don't return | Wallet integration in Phase 5 will use this |

## Deviations from Plan

None - plan executed exactly as written.

## Issues Encountered

None

## User Setup Required

None - no external service configuration required.

## Next Phase Readiness

- Completion endpoint ready for player usage
- Unlock state calculation ready for progress query endpoints (Plan 04)
- Replay logging in place for Phase 5 wallet integration
- No blockers

---
*Phase: 04-progress-tracking*
*Completed: 2026-02-02*
