---
phase: 06-build-pipeline
plan: 02
subsystem: api
tags: [json, frappe, hierarchy, bitmap, generator, build-pipeline]

# Dependency graph
requires:
  - phase: 04-progress-tracking
    provides: Lesson bit_index allocation and hierarchy structure
provides:
  - JSON generation service for mobile app content delivery
  - Hierarchy JSON: _subjects.json, track_*.json, unit_*.json, topic_*.json
  - Lesson content JSON with stages array (lesson_*.json)
  - Bitmap metadata JSON for progress tracking (*_b.json)
affects: [06-03-PLAN, 06-04-PLAN]

# Tech tracking
tech-stack:
  added: []
  patterns:
    - File dict return pattern (filename, content, subject_id)
    - Nested generation with child file collection

key-files:
  created:
    - memora_admin/memora_admin/services/__init__.py
    - memora_admin/memora_admin/services/build/__init__.py
    - memora_admin/memora_admin/services/build/generator.py
  modified: []

key-decisions:
  - "generate_subject_json returns list of file dicts for flexible output handling"
  - "unit_*.json contains full topic/lesson metadata inline for content delivery"
  - "topic_*.json contains only lesson_ids for navigation"
  - "Stage config_json parsed from string to dict with graceful fallback"
  - "Malformed data skipped with warning, build continues"

patterns-established:
  - "File dict pattern: {filename, content, subject_id} for generator output"
  - "Services module pattern: memora_admin/memora_admin/services/{domain}/"
  - "Helper functions prefixed with underscore for internal use"

# Metrics
duration: 2min
completed: 2026-02-02
---

# Phase 6 Plan 2: JSON Generator Summary

**JSON generation service producing hierarchical content files (_subjects.json, track_*.json, unit_*.json, topic_*.json), lesson content with stages array (lesson_*.json), and bitmap metadata (*_b.json) for mobile app consumption**

## Performance

- **Duration:** 2 min
- **Started:** 2026-02-02T16:53:17Z
- **Completed:** 2026-02-02T16:55:17Z
- **Tasks:** 2
- **Files modified:** 3

## Accomplishments
- Created services/build/ directory structure for Frappe module
- Implemented generate_subject_json() returning list of file dictionaries
- Hierarchy JSON generation: _subjects.json with track_ids, track_*.json with unit_ids
- Unit content JSON (BUILD-04): unit_*.json with full topics and lessons inline
- Lesson content JSON (BUILD-05): lesson_*.json with stages array and parsed config
- Bitmap metadata JSON: *_b.json with bit_range and excluded_bits

## Task Commits

Each task was committed atomically:

1. **Task 1: Create build services directory structure** - `04de717` (chore)
2. **Task 2: Create JSON generator for hierarchy, lesson content, and bitmap** - `5a6389c` (feat)

## Files Created/Modified
- `memora_admin/memora_admin/services/__init__.py` - Services module root
- `memora_admin/memora_admin/services/build/__init__.py` - Build module with generate_subject_json export
- `memora_admin/memora_admin/services/build/generator.py` - JSON generation logic (375 lines)

## Decisions Made
- **File dict return pattern:** generate_subject_json returns `[{filename, content, subject_id}]` allowing caller to handle file I/O
- **Unit content JSON:** unit_*.json contains full topics array with nested lesson metadata (not just topic_ids) per BUILD-04
- **Topic navigation JSON:** topic_*.json contains lesson_ids only for lightweight navigation
- **Stage config parsing:** config_json stored as string in DB, parsed to dict with empty {} fallback on error
- **Relative paths:** Media URLs stripped of domain prefix, app prepends CDN base URL at runtime
- **Bit range calculation:** Uses max(bit_indices) + 1 from lessons, falls back to subject.last_bit_index

## Deviations from Plan

None - plan executed exactly as written.

## Issues Encountered

None

## User Setup Required

None - no external service configuration required.

## Next Phase Readiness
- Generator service ready for build queue integration (06-03)
- File dict output pattern allows mock CDN upload in 06-03
- Bitmap JSON structure ready for FastAPI cache invalidation (06-04)

---
*Phase: 06-build-pipeline*
*Completed: 2026-02-02*
