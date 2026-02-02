---
phase: 07-sync-mechanisms
plan: 04
subsystem: scheduler
tags: [frappe-scheduler, hooks, cron, sync-tasks, build-worker]

# Dependency graph
requires:
  - phase: 07-02
    provides: sync_dirty_progress function in sync.py
  - phase: 07-03
    provides: sync_dirty_wallets and flush_interaction_buffer functions
provides:
  - Frappe scheduler cron entries for all sync tasks
  - 1-minute sync cycle for Redis-to-MariaDB persistence
affects: [production-scheduler, background-jobs]

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "Frappe scheduler_events cron configuration"
    - "Multiple tasks in single cron pattern"

key-files:
  created: []
  modified:
    - memora_admin/hooks.py
    - memora_admin/memora_admin/tasks/__init__.py

key-decisions:
  - "All three sync tasks run every 1 minute (minimizes data loss window)"
  - "Build worker remains on 2-minute schedule (unchanged)"
  - "Full dotted paths in scheduler_events for explicit task resolution"

patterns-established:
  - "Cron comment documentation for task purpose"
  - "Separate cron patterns for different task frequencies"

# Metrics
duration: 1min
completed: 2026-02-02
---

# Phase 07 Plan 04: Scheduler Wiring Summary

**Wire sync tasks into Frappe scheduler enabling 1-minute background sync cycle**

## Performance

- **Duration:** 1 min
- **Started:** 2026-02-02T18:47:04Z
- **Completed:** 2026-02-02T18:47:54Z
- **Tasks:** 2
- **Files modified:** 2

## Accomplishments
- Updated tasks/__init__.py with comprehensive module documentation
- Added scheduler_events cron entries for all three sync functions
- Preserved existing build_worker 2-minute schedule
- Phase 7 implementation now complete and active

## Task Commits

Each task was committed atomically:

1. **Task 1: Update tasks/__init__.py with sync module** - `a1db7f3` (docs)
2. **Task 2: Add sync scheduler events to hooks.py** - `71602de` (feat)

## Files Modified
- `memora_admin/memora_admin/tasks/__init__.py` - Updated docstring to document both build_worker and sync modules
- `memora_admin/hooks.py` - Added scheduler_events cron entries for sync tasks

## Key Implementation Details

### tasks/__init__.py
- Module docstring now documents both scheduled task modules
- Lists all three sync functions with their purpose
- Provides IDE support and documentation for task discovery

### hooks.py scheduler_events
```python
scheduler_events = {
    "cron": {
        # Every 1 minute: Sync dirty data from Redis to MariaDB
        "* * * * *": [
            "memora_admin.memora_admin.tasks.sync.sync_dirty_progress",
            "memora_admin.memora_admin.tasks.sync.sync_dirty_wallets",
            "memora_admin.memora_admin.tasks.sync.flush_interaction_buffer",
        ],
        # Every 2 minutes: Process pending content builds
        "*/2 * * * *": [
            "memora_admin.memora_admin.tasks.build_worker.process_pending_builds"
        ]
    }
}
```

## Decisions Made
- All three sync tasks share the 1-minute cron pattern (per RESEARCH.md recommendation)
- Full dotted paths ensure explicit task resolution by Frappe scheduler
- Comments above each cron pattern document their purpose

## Deviations from Plan

None - plan executed exactly as written.

## Issues Encountered

None.

## User Setup Required

None - Frappe scheduler automatically picks up scheduler_events from hooks.py.

## Phase 7 Complete

With this plan, Phase 7 (Sync Mechanisms) is complete:

| Plan | Name | Status |
|------|------|--------|
| 07-01 | Dirty Set Tracking | Complete |
| 07-02 | Progress Sync Task | Complete |
| 07-03 | Wallet and Interaction Sync | Complete |
| 07-04 | Scheduler Wiring | Complete |

**End-to-end flow now active:**
1. FastAPI services mark dirty keys on state mutations (07-01)
2. Frappe scheduler runs sync tasks every 1 minute (07-04)
3. sync_dirty_progress persists progress bitmaps (07-02)
4. sync_dirty_wallets persists wallet data (07-03)
5. flush_interaction_buffer persists interaction logs (07-03)

---
*Phase: 07-sync-mechanisms*
*Completed: 2026-02-02*
