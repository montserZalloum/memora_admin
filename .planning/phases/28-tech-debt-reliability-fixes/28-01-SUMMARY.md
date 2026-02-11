---
phase: 28-tech-debt-reliability-fixes
plan: 01
subsystem: sync-tasks
tags: [redis, ltrim, constants, data-integrity, sync]

dependency_graph:
  requires: []
  provides:
    - "Safe LTRIM in flush_interaction_buffer (no data loss on partial flush)"
    - "Unified Redis key constants (single source of truth in fastapi_app/core/constants.py)"
  affects:
    - "28-02 (deps.py DRY consolidation may reference same constants pattern)"

tech_stack:
  added: []
  patterns:
    - "Cross-module constant sharing: Frappe tasks import from FastAPI core"

key_files:
  created: []
  modified:
    - memora_admin/tasks/sync.py

decisions:
  - id: "28-01-D1"
    decision: "Import constants from fastapi_app.core.constants into Frappe sync tasks"
    reason: "Eliminates risk of key name drift between FastAPI and Frappe background jobs"

metrics:
  duration: "79s"
  completed: "2026-02-11"
---

# Phase 28 Plan 01: LTRIM Race Condition Fix & Constant Unification Summary

**One-liner:** Fixed LTRIM data loss race in flush_interaction_buffer (uses `inserted` not `count`) and replaced duplicate Redis key constants with import from canonical location.

## Performance

| Metric | Value |
|--------|-------|
| Duration | 79 seconds |
| Start | 2026-02-11T21:05:39Z |
| End | 2026-02-11T21:06:58Z |
| Tasks | 1/1 |
| Files modified | 1 |

## Accomplishments

### Task 1: Fix LTRIM race condition and unify constants in sync.py

**The bug:** `flush_interaction_buffer()` used `r.ltrim(INTERACTION_BUFFER_KEY, count, -1)` which trimmed ALL fetched items from the Redis buffer, even when some inserts failed. Failed items were silently lost -- removed from Redis but never written to MariaDB.

**The fix:** Changed to `r.ltrim(INTERACTION_BUFFER_KEY, inserted, -1)` so only successfully inserted items are trimmed. Failed items remain at the head of the list for retry on the next flush cycle (every 1 minute).

**Additional improvement:** Added a `logger.warning()` call when `inserted < count` to surface partial flush failures in logs, making them visible for monitoring/alerting.

**Constant unification:** Removed the local definitions of `DIRTY_PROGRESS_KEY`, `DIRTY_WALLETS_KEY`, and `INTERACTION_BUFFER_KEY` (which had a "must match FastAPI constants" comment) and replaced them with a direct import from `fastapi_app.core.constants`. This eliminates any risk of key name drift between the two modules.

## Task Commits

| Task | Name | Commit | Key Changes |
|------|------|--------|-------------|
| 1 | Fix LTRIM race condition and unify constants | `7070b84` | LTRIM uses `inserted`, constants imported from `fastapi_app.core.constants` |

## Files Modified

- `memora_admin/tasks/sync.py` -- Replaced local constant definitions with import; fixed LTRIM offset; added partial flush warning log

## Decisions Made

| ID | Decision | Rationale |
|----|----------|-----------|
| 28-01-D1 | Import constants from `fastapi_app.core.constants` into Frappe sync tasks | Eliminates risk of key name drift between FastAPI sidecar and Frappe background jobs |

## Deviations from Plan

None -- plan executed exactly as written.

## Issues / Risks

- Pre-existing ruff lint warnings in sync.py (7 issues: import sorting, f-string style, str(e) conversion flags) were not addressed as they are outside this plan's scope. These could be cleaned up in a future tech debt pass.

## Next Phase Readiness

- No blockers for subsequent plans (28-02 through 28-04)
- The constant import pattern established here can be referenced by 28-02 (deps.py consolidation) if it also needs shared constants
