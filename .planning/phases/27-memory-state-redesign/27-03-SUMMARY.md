---
phase: 27-memory-state-redesign
plan: 03
subsystem: database
tags: [fsrs, spaced-repetition, uuid, binary, raw-sql, partitioning, autoincrement, item-level]

# Dependency graph
requires:
  - phase: 27-memory-state-redesign-01
    provides: "BIGINT PK, BINARY(16) item_id column, UUID_TO_BIN polyfill, RANGE partitioning, composite indexes"
  - phase: 27-memory-state-redesign-02
    provides: "item_id field on Interaction Log populated by content pipeline and session API"
provides:
  - "Item-level FSRS processing (1 Memory State per sub-element, not per stage)"
  - "Raw SQL INSERT/UPDATE with UUID_TO_BIN for BINARY(16) item_id"
  - "BIGINT PK generation via frappe.db.get_next_sequence_val"
  - "season_seq in all queries for RANGE partition pruning"
  - "Legacy backward compatibility via deterministic uuid5 from stage_id"
affects:
  - 27-04 (review/profile endpoints query Memory States with item-level granularity)

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "Raw SQL for BINARY column operations: ORM cannot handle UUID_TO_BIN, use frappe.db.sql() for lookup/insert/update"
    - "Sequence-based PK: frappe.db.get_next_sequence_val('Memora Memory State') for BIGINT autoincrement"
    - "Partition-aware queries: include season_seq in every WHERE clause for pruning"
    - "Deterministic UUID fallback: uuid5(NAMESPACE_OID, stage_id) for legacy interactions without item_id"

key-files:
  created: []
  modified:
    - "memora_admin/tasks/fsrs_processor.py"

key-decisions:
  - "Extracted _lookup_memory_state, _update_memory_state, _insert_memory_state as reusable raw SQL helpers"
  - "next_review stored as date (not datetime) -- consistent with schema change in 27-01"
  - "next_review converted to datetime with UTC timezone for FSRS Card.due reconstruction"
  - "Legacy interactions use deterministic uuid5(NAMESPACE_OID, stage_id) -- consistent across reruns"

patterns-established:
  - "Raw SQL CRUD pattern for BINARY columns: lookup/insert/update via dedicated functions with UUID_TO_BIN"
  - "season_seq partition key in every query: prevents full-table scans across partitions"

# Metrics
duration: 4min
completed: 2026-02-11
---

# Phase 27 Plan 03: FSRS Processor Rewrite Summary

**Item-level FSRS processing with raw SQL UUID_TO_BIN lookups, BIGINT sequence PK inserts, and season_seq partition-aware queries**

## Performance

- **Duration:** 4 min
- **Started:** 2026-02-11T09:53:41Z
- **Completed:** 2026-02-11T09:57:53Z
- **Tasks:** 1
- **Files modified:** 1

## Accomplishments
- Rewrote process_fsrs_reviews() to create 1 Memory State per item (not per stage)
- All Memory State operations use raw SQL with UUID_TO_BIN for BINARY(16) item_id column
- New records use frappe.db.get_next_sequence_val for BIGINT autoincrement PK
- season_seq included in every SELECT, UPDATE, and INSERT for RANGE partition pruning
- Legacy interactions without item_id fall back to deterministic uuid5 from stage_id
- Redis cache key and idempotency key both use item_id (not stage_id)
- Extracted 3 helper functions for clean separation of SQL operations

## Task Commits

Each task was committed atomically:

1. **Task 1: Rewrite FSRS processor for item-level Memory State creation** - `04642c8` (feat)

## Files Created/Modified
- `memora_admin/tasks/fsrs_processor.py` - Complete rewrite of process_fsrs_reviews() and _get_active_season(); new _lookup_memory_state, _update_memory_state, _insert_memory_state helpers

## Decisions Made
- **Extracted SQL helpers:** Instead of inline raw SQL in process_fsrs_reviews(), created _lookup_memory_state(), _update_memory_state(), and _insert_memory_state() for readability and reuse. Plan 27-04 may also need these for review queries.
- **next_review as date object:** Since the schema column is DATE (not DATETIME per 27-01), store as date. Convert to datetime with UTC timezone only for FSRS Card.due reconstruction.
- **next_review_date (not next_review_naive):** Renamed variable to reflect it's a date, not a datetime clamped to midnight. Consistent with the DATE column type.
- **Redis cache includes stage_id:** Added stage_id to the Redis cache JSON so consumers can resolve back to the stage without a DB lookup.

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] Fixed _get_active_season falsy check on season_seq=0**
- **Found during:** Task 1 (verification)
- **Issue:** The plan specified `if result and result.season_seq:` which evaluates to False when season_seq=0. The existing season (SEAS-00027) had season_seq=0, causing the processor to skip all processing with "No active season found."
- **Fix:** Changed to `if result and result.season_seq is not None:` so season_seq=0 is treated as valid.
- **Files modified:** `memora_admin/tasks/fsrs_processor.py`
- **Verification:** bench execute runs successfully with season_seq=0 and season_seq=1
- **Committed in:** 04642c8 (Task 1 commit)

---

**Total deviations:** 1 auto-fixed (1 bug)
**Impact on plan:** Essential fix -- without it, any season with season_seq=0 would silently skip all FSRS processing. No scope creep.

## Issues Encountered
- Season SEAS-00027 had season_seq=0 (default value from migration). Updated to season_seq=1 as the first season. Future seasons should set season_seq explicitly via admin UI.

## User Setup Required
None - no external service configuration required.

## Next Phase Readiness
- FSRS processor fully rewritten for item-level Memory State creation
- Plan 27-04 (Review/Profile Update) can proceed: Memory States now use item-level granularity with (player, item_id, season_seq) lookups
- Existing helper functions (_lookup_memory_state, etc.) can be reused by review endpoints if needed
- No blockers or concerns

---
*Phase: 27-memory-state-redesign*
*Completed: 2026-02-11*
