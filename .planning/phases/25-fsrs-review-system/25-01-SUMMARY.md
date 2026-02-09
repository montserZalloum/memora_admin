---
phase: 25
plan: 01
subsystem: fsrs-review
tags: [fsrs, spaced-repetition, database-index, bug-fix]
requires: []
provides:
  - "Corrected FSRS processor with is_reviewable check, proper skippable filter, and date clamping"
  - "Composite index (player, subject, next_review) on Memora Memory State"
affects:
  - "25-02 (review API endpoints depend on correct Memory State records)"
  - "25-03 (review submission depends on correct processor behavior)"
tech-stack:
  added: []
  patterns:
    - "after_migrate hook for composite index persistence"
    - "Per-stage is_skippable override with global fallback"
    - "Date clamping: midnight + minimum tomorrow"
key-files:
  created: []
  modified:
    - memora_admin/tasks/fsrs_processor.py
    - memora_admin/memora_admin/setup.py
    - memora_admin/hooks.py
key-decisions:
  - "after_migrate hook for composite index persistence (Frappe only creates Property Setters for single-column indexes)"
  - "Per-stage is_skippable override takes priority over global stage type setting"
  - "next_review clamped to midnight with minimum tomorrow to prevent same-day reviews"
patterns-established:
  - "Composite index persistence via after_migrate hook"
duration: "~4 minutes"
completed: "2026-02-09"
---

# Phase 25 Plan 01: Fix FSRS Processor Bugs and Add Index Summary

**Fixed three FSRS processor bugs (is_reviewable gate, skippable stage_type lookup, date clamping) and created composite index for <5ms review queries.**

## Accomplishments

### Task 1: Fix FSRS processor bugs and add date clamping
- **Fix 1 (is_reviewable)**: Added check that skips lessons where `is_reviewable=false`, preventing Memory State records for non-reviewable lessons. The check runs before the idempotency key is set, so non-reviewable interactions are fully ignored.
- **Fix 2 (skippable filter)**: Replaced broken `stage_id in skippable` check (compared unique stage IDs against stage type names -- always false) with proper lookup: query `Memora Lesson Stage` child table for the stage's `stage_type` and `is_skippable` fields, then check per-stage override first, then fall back to global `Memora Lesson Stage Settings` skippable types.
- **Fix 3 (date clamping)**: Replaced naive `card.due.replace(tzinfo=None)` with date-only clamping: extract date from `card.due`, compare against tomorrow, use the later date, combine with midnight (`time.min`). This prevents same-day reviews and ensures all `next_review` values are at 00:00:00.
- **Cleanup**: Removed redundant `from datetime import date` inside `_get_active_season()` since `date` is now imported at module level.

### Task 2: Create composite database index
- Created composite index `player_subject_next_review_index` on `tabMemora Memory State` with columns (player, subject, next_review) in that order.
- Added `after_migrate` hook in `memora_admin.memora_admin.setup` that re-creates the index after `bench migrate`, since Frappe's migration only preserves single-column indexes via Property Setters.
- Index enables efficient review queries: `WHERE player=? AND subject=? AND next_review<=?`

## Task Commits

| Task | Name | Commit | Key Files |
|------|------|--------|-----------|
| 1 | Fix FSRS processor bugs and add date clamping | f5c2360 | memora_admin/tasks/fsrs_processor.py |
| 2 | Create composite database index | c2f2252 | memora_admin/hooks.py, memora_admin/memora_admin/setup.py |

## Decisions Made

1. **after_migrate hook for index persistence**: Frappe's `frappe.db.add_index()` only creates Property Setters for single-column indexes (see line 428 of mariadb/database.py: `if len(fields) == 1`). For the 3-column composite index, added an `after_migrate` hook that calls `frappe.db.add_index()` with `IF NOT EXISTS` semantics to ensure the index survives database migrations.

2. **Per-stage override priority**: When checking if a stage is skippable, the per-stage `is_skippable` field on `Memora Lesson Stage` (child table row) takes priority over the global setting on `Memora Lesson Stage Settings` (linked via `stage_type`). This allows individual stages to override their type's default behavior.

3. **Date clamping strategy**: Rather than simply stripping timezone info, `next_review` is now clamped to midnight (00:00:00) with a floor of tomorrow. This means: (a) all reviews are scheduled at day boundaries, (b) no same-day reviews can occur even if FSRS computes a very short interval, (c) the review API can efficiently query by date without time precision concerns.

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 3 - Blocking] Composite index persistence via after_migrate hook**
- **Found during:** Task 2
- **Issue:** The plan assumed `bench add-database-index` creates a Property Setter for composite indexes, but Frappe only does this for single-column indexes. Without persistence, the index would be dropped on `bench migrate`.
- **Fix:** Added `after_migrate` hook in `memora_admin.memora_admin.setup.after_migrate()` that calls `frappe.db.add_index()` with the composite columns. The `IF NOT EXISTS` semantics in the underlying SQL prevent duplicate index creation.
- **Files modified:** `memora_admin/hooks.py`, `memora_admin/memora_admin/setup.py`
- **Commit:** c2f2252

**2. [Rule 1 - Bug] Removed redundant datetime import**
- **Found during:** Task 1
- **Issue:** `_get_active_season()` had `from datetime import date` as a local import, but `date` was already imported at module level after adding it for the clamping fix.
- **Fix:** Removed the redundant local import.
- **Files modified:** `memora_admin/tasks/fsrs_processor.py`
- **Commit:** f5c2360

## Issues Encountered

None. Both tasks completed without errors.

## Next Phase Readiness

- The FSRS processor now correctly creates Memory State records only for reviewable lessons with non-skippable stages
- The composite index is in place for efficient review queries
- Plan 25-02 (review API endpoints) can proceed: it will query `Memora Memory State` using the composite index with filters on player, subject, and next_review
- Plan 25-03 (review submission) can proceed: the processor's corrected behavior ensures only valid Memory States exist for review
