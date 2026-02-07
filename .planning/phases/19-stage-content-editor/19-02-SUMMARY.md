---
phase: 19-stage-content-editor
plan: 02
subsystem: api
tags: [frappe, json-generator, lesson-content, stage-id]

# Dependency graph
requires:
  - phase: 19-stage-content-editor
    provides: Stage content editor wiring (19-01)
provides:
  - Valid lesson.json output with correct stage_id from Frappe name field
  - Consistent stage_id handling across both build generators
affects: [build-pipeline, mobile-app, analytics]

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "Use stage.name (Frappe child table row identifier) for stage_id in JSON output"

key-files:
  created: []
  modified:
    - memora_admin/memora_admin/services/build/generator.py
    - memora_admin/memora_admin/services/build/plan_generator.py

key-decisions:
  - "Use stage.name instead of stage.stage_id (field was renamed to stage_title)"
  - "JSON output structure unchanged - only value source changed"

patterns-established:
  - "Child table row identifiers use Frappe name field, not custom fields"

# Metrics
duration: 3min
completed: 2026-02-07
---

# Phase 19 Plan 02: Use Frappe name as stage_id Summary

**Build generators now use Frappe child table row name as stage_id, fixing broken reference after stage_id field renamed to stage_title**

## Performance

- **Duration:** 3 min
- **Started:** 2026-02-07T07:19:41Z
- **Completed:** 2026-02-07T07:23:00Z
- **Tasks:** 3 (2 code changes, 1 verification)
- **Files modified:** 2

## Accomplishments
- Updated generator.py to use stage.name for stage_id in lesson JSON
- Updated plan_generator.py to use stage.name for stage_id in lesson JSON
- Verified no remaining stage.stage_id references in build services

## Task Commits

Each task was committed atomically:

1. **Task 1: Update generator.py to use stage.name** - `4ca9388` (fix)
2. **Task 2: Update plan_generator.py to use stage.name** - `9863599` (fix)
3. **Task 3: Verify no remaining stage_id field references** - (verification only, no commit)

## Files Created/Modified
- `memora_admin/memora_admin/services/build/generator.py` - Changed stage.stage_id to stage.name at line 272
- `memora_admin/memora_admin/services/build/plan_generator.py` - Changed stage.stage_id to stage.name at line 525

## Decisions Made
None - followed plan as specified. The plan clearly identified the broken field reference and the correct fix using Frappe's name field.

## Deviations from Plan
None - plan executed exactly as written.

## Issues Encountered
None

## User Setup Required
None - no external service configuration required.

## Next Phase Readiness
- Build generators now produce valid lesson.json with correct stage_id values
- stage_id uses Frappe's auto-generated child table row identifier (format: xxxxxxxxxxxx hash)
- API contract unchanged - clients receive same JSON structure with different value source
- Analytics correctly records stage_id for interaction logs

---
*Phase: 19-stage-content-editor*
*Completed: 2026-02-07*
