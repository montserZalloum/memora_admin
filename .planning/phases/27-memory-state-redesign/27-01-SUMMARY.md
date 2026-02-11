---
phase: 27-memory-state-redesign
plan: 01
subsystem: database
tags: [mariadb, partitioning, uuid, binary, autoincrement, frappe-doctype, after-migrate]

# Dependency graph
requires:
  - phase: 25-fsrs-review-system
    provides: "Original Memory State DocType with composite string PK and after_migrate hook pattern"
provides:
  - "BIGINT autoincrement PK on Memory State (replaces ~80-byte composite string)"
  - "item_id BINARY(16) column for item-level FSRS tracking"
  - "season_seq INT column for RANGE partition routing"
  - "UUID_TO_BIN / BIN_TO_UUID polyfill stored functions"
  - "RANGE partitioning by season_seq (p_season_1, p_future)"
  - "UNIQUE index (player, item_id, season_seq) preventing duplicate records"
  - "Composite index (player, subject, next_review, season_seq) for review queries"
  - "item_id field on Interaction Log for item-level event tracking"
  - "season_seq field on Season for partition routing"
affects:
  - 27-02 (content pipeline needs item_id generation + UUID_TO_BIN)
  - 27-03 (FSRS rewrite queries against new schema/indexes)
  - 27-04 (review/profile endpoints use new Memory State structure)

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "after_migrate column type override: Frappe Data -> BINARY(16) via raw SQL"
    - "after_migrate PK type override: varchar(140) -> BIGINT via ALTER + sequence creation"
    - "MariaDB RANGE partitioning managed by after_migrate hook"
    - "UUID polyfill stored functions for MariaDB 10.6 (no native UUID_TO_BIN)"
    - "Idempotent DDL: INFORMATION_SCHEMA checks before every ALTER"

key-files:
  created: []
  modified:
    - "memora_admin/memora_admin/doctype/memora_memory_state/memora_memory_state.json"
    - "memora_admin/memora_admin/doctype/memora_interaction_log/memora_interaction_log.json"
    - "memora_admin/memora_admin/doctype/memora_season/memora_season.json"
    - "memora_admin/memora_admin/setup.py"

key-decisions:
  - "Frappe autoincrement + explicit BIGINT override in after_migrate (Frappe doesn't alter existing varchar columns)"
  - "UUID polyfill via stored functions (MariaDB 10.6 lacks native UUID_TO_BIN/BIN_TO_UUID)"
  - "RANGE partitioning with REMOVE PARTITIONING -> re-partition cycle for column type changes"
  - "Table truncation during initial setup (no production data per roadmap decision)"
  - "next_review changed from Datetime to Date (already clamped to midnight per Phase 25)"

patterns-established:
  - "after_migrate column override: define field as Data in DocType JSON, override to BINARY/BIGINT in after_migrate"
  - "Idempotent DDL pattern: check INFORMATION_SCHEMA before every ALTER, wrap in try/except"
  - "Sequence creation: frappe.db.create_sequence() with check_not_exists=True for autoincrement PKs"

# Metrics
duration: 8min
completed: 2026-02-11
---

# Phase 27 Plan 01: Schema Foundation Summary

**BIGINT autoincrement PK with BINARY(16) item_id, RANGE partitioning by season_seq, UUID polyfill functions, and composite indexes on Memory State**

## Performance

- **Duration:** 8 min
- **Started:** 2026-02-11T09:35:48Z
- **Completed:** 2026-02-11T09:43:47Z
- **Tasks:** 2
- **Files modified:** 4

## Accomplishments
- Memory State DocType redesigned: autoincrement BIGINT PK, item_id (BINARY(16)), season_seq (INT), next_review as Date
- Comprehensive after_migrate hook with 5 idempotent operations: UUID polyfills, BIGINT name override, BINARY item_id override, RANGE partitioning, composite indexes
- UUID_TO_BIN / BIN_TO_UUID polyfill stored functions verified with round-trip test
- RANGE partitioning active with p_season_1 and p_future partitions
- Interaction Log augmented with optional item_id for item-level event tracking
- Season DocType augmented with unique season_seq for partition routing

## Task Commits

Each task was committed atomically:

1. **Task 1: Update DocType JSON schemas** - `fe721a9` (feat)
2. **Task 2: Rewrite after_migrate with UUID polyfills, BINARY override, partitioning, and indexes** - `c605f01` (feat)

## Files Created/Modified
- `memora_admin/memora_admin/doctype/memora_memory_state/memora_memory_state.json` - Autoincrement PK, item_id/season_seq fields, removed season Link, next_review as Date
- `memora_admin/memora_admin/doctype/memora_interaction_log/memora_interaction_log.json` - Added optional item_id Data field
- `memora_admin/memora_admin/doctype/memora_season/memora_season.json` - Added required unique season_seq Int field
- `memora_admin/memora_admin/setup.py` - Complete after_migrate rewrite with UUID polyfills, BIGINT/BINARY overrides, RANGE partitioning, composite indexes

## Decisions Made
- **Frappe autoincrement requires explicit BIGINT override:** Frappe's `autoname: "autoincrement"` only creates BIGINT columns for NEW tables. For existing tables (originally varchar(140)), the column type is not altered during migrate. The after_migrate hook explicitly converts `name` to BIGINT and creates the Frappe sequence (`memora_memory_state_id_seq`).
- **REMOVE PARTITIONING -> re-partition cycle:** When converting `name` from varchar to BIGINT on an already-partitioned table, partitioning must be removed first, then the column altered, then partitioning re-applied. This is safe because the table is truncated (no data).
- **next_review as Date, not Datetime:** Already clamped to midnight per Phase 25 decision. Date type is more efficient and semantically correct.
- **item_id optional on Interaction Log:** Older interactions won't have item_id (backward compatibility). Required on Memory State since all new records will have item-level tracking.

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] Frappe autoincrement does not convert existing varchar name column to BIGINT**
- **Found during:** Task 2 (after_migrate verification)
- **Issue:** After running bench migrate with `autoname: "autoincrement"`, the `name` column remained `varchar(140)` instead of becoming `bigint(20)`. Frappe only sets BIGINT for NEW table creation, not existing table alteration. The Frappe sequence was also not created.
- **Fix:** Added `_ensure_name_bigint_column()` to after_migrate that: (1) checks column type via INFORMATION_SCHEMA, (2) removes partitioning if present, (3) alters name to BIGINT, (4) re-applies composite PK and partitioning, (5) creates Frappe sequence via `frappe.db.create_sequence()`.
- **Files modified:** `memora_admin/memora_admin/setup.py`
- **Verification:** `COLUMN_TYPE = bigint(20)`, sequence `memora_memory_state_id_seq` exists, partitions intact after conversion
- **Committed in:** c605f01 (Task 2 commit)

---

**Total deviations:** 1 auto-fixed (1 bug)
**Impact on plan:** Essential fix -- without BIGINT name column, autoincrement PK generation would fail. No scope creep.

## Issues Encountered
- Frappe's `autoname: "autoincrement"` behavior differs between new table creation and existing table migration. The code path in `frappe/database/mariadb/schema.py` only sets `name bigint primary key` in the CREATE TABLE statement, not via ALTER TABLE during migrate. This required explicit handling in after_migrate.

## User Setup Required
None - no external service configuration required.

## Next Phase Readiness
- Schema foundation complete for all subsequent plans
- Plan 27-02 (Content Pipeline) can proceed: UUID_TO_BIN function available for item_id generation
- Plan 27-03 (FSRS Rewrite) can proceed: indexes and partitioning ready for new query patterns
- Plan 27-04 (Review/Profile Update) can proceed: item_id and season_seq columns available
- Old `season` varchar column still exists in table (Frappe doesn't drop columns) -- not blocking, can be cleaned up later

---
*Phase: 27-memory-state-redesign*
*Completed: 2026-02-11*
