---
phase: 25-fsrs-review-system
plan: 02
subsystem: api
tags: [frappe, fsrs, spaced-repetition, review-api, mariadb]

# Dependency graph
requires:
  - phase: 25-fsrs-review-system plan 01
    provides: Composite index on (player, subject, next_review), FSRS processor bug fixes, date clamping
provides:
  - get_review_overview Frappe whitelisted API (due counts per subject)
  - get_due_stages Frappe whitelisted API (FIFO due stages with stage_type validation)
  - submit_reviews Frappe whitelisted API (batch FSRS computation with date clamping)
affects: [25-fsrs-review-system plan 03, FastAPI review endpoints]

# Tech tracking
tech-stack:
  added: []
  patterns: [inline FSRS computation in Frappe API, stage existence validation for graceful skip]

key-files:
  created: [memora_admin/api/reviews.py]
  modified: []

key-decisions:
  - "Inline FSRS computation in submit handler (no dependency on fsrs_processor.py to avoid circular imports)"
  - "Fetch limit+5 rows to account for removed stages being filtered out"
  - "Stage existence validated via Memora Lesson Stage child table lookup"

patterns-established:
  - "Review API pattern: Frappe whitelisted methods for MariaDB-only review queries"
  - "Graceful skip: removed stages silently excluded from results and submissions"

# Metrics
duration: 1min
completed: 2026-02-09
---

# Phase 25 Plan 02: Review API Summary

**Three Frappe whitelisted review endpoints: overview counts by subject, FIFO due stages with stage validation, and batch FSRS submit with midnight date clamping**

## Performance

- **Duration:** 1 min
- **Started:** 2026-02-09T11:07:34Z
- **Completed:** 2026-02-09T11:08:57Z
- **Tasks:** 1
- **Files created:** 1

## Accomplishments
- Created `get_review_overview()` returning due review counts grouped by subject using composite index
- Created `get_due_stages()` returning up to N due stages in FIFO order with stage_type from child table, gracefully skipping removed stages
- Created `submit_reviews()` with inline FSRS computation, fail_count-to-rating mapping, next_review clamped to midnight (minimum tomorrow), and remaining_due count in response
- All three methods follow existing Frappe API patterns (same style as hierarchy.py, products.py)

## Task Commits

Each task was committed atomically:

1. **Task 1: Create Frappe whitelisted review API** - `ba7dc01` (feat)

**Plan metadata:** [pending] (docs: complete plan)

## Files Created/Modified
- `memora_admin/api/reviews.py` - Three whitelisted methods: get_review_overview, get_due_stages, submit_reviews with helper _get_fsrs_scheduler

## Decisions Made
- Inline FSRS computation in submit_reviews (duplicates _get_fsrs_scheduler helper from fsrs_processor.py rather than importing to avoid coupling)
- Fetch limit+5 rows in get_due_stages to compensate for filtered-out removed stages
- Stage existence validated per row via Memora Lesson Stage child table (stage_title + parent)
- has_more indicator based on total_due count vs returned result count

## Deviations from Plan

None - plan executed exactly as written.

## Issues Encountered

None.

## User Setup Required

None - no external service configuration required.

## Next Phase Readiness
- All three Frappe whitelisted review methods are ready for FastAPI sidecar to call
- Plan 03 can build FastAPI endpoints that proxy to these Frappe APIs
- Composite index from Plan 01 ensures efficient query performance
- XP rewards integration (Plan 03) can hook into submit_reviews response

---
*Phase: 25-fsrs-review-system*
*Completed: 2026-02-09*
