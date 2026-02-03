---
phase: 12-plan-system-enhancement
plan: 02
subsystem: build
tags: [json, plan-override, mobile-api, cdn, hierarchy]

# Dependency graph
requires:
  - phase: 06-content-pipeline
    provides: Build queue infrastructure, publisher pattern
  - phase: 12-01
    provides: Grade-Major linking for plan metadata
provides:
  - Plan-centric JSON generator with Plan Overrides
  - generate_plan_json() function for build worker
  - manifest.json schema with subject stats
  - _h.json hierarchy schema with Plan Overrides
  - _c.json unit content schema
  - is_free_preview derivation logic
affects:
  - 12-03: FastAPI endpoint for Plan JSON serving
  - 12-04: Build worker integration, hooks

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "Plan-centric JSON folder structure (plans/{plan_id}/subjects/{subject_id})"
    - "Plan Overrides pattern (Hide, Set Free) for per-plan content visibility"
    - "is_free_preview derivation from visible free units/topics"
    - "Shared lesson files at root level (lessons/{lesson_id}.json)"

key-files:
  created:
    - memora_admin/memora_admin/services/build/plan_generator.py
  modified:
    - memora_admin/memora_admin/services/build/__init__.py

key-decisions:
  - "Plan Overrides loaded once per plan for efficiency"
  - "is_free_preview derived at generation time from visible content"
  - "Lesson JSON files shared at root level, not duplicated per plan"
  - "content_url in hierarchy uses plan_id for correct path"

patterns-established:
  - "Plan Override checking: _is_hidden(), _is_override_free() helpers"
  - "Stats calculation with override application: _calculate_subject_stats()"
  - "File dict output pattern: {filename, content} for publisher compatibility"

# Metrics
duration: 8min
completed: 2026-02-03
---

# Phase 12 Plan 02: Plan JSON Generator Summary

**Plan-centric JSON generator with Plan Overrides support generating manifest.json, hierarchy (_h.json), and unit content (_c.json) files**

## Performance

- **Duration:** 8 min
- **Started:** 2026-02-03T16:00:00Z
- **Completed:** 2026-02-03T16:08:00Z
- **Tasks:** 2 (merged into 1 commit)
- **Files modified:** 2

## Accomplishments

- Created `plan_generator.py` (490 lines) with complete Plan JSON generation logic
- Implemented Plan Overrides support (Hide removes from output, Set Free adjusts is_free)
- Derived is_free_preview from visible free units/topics after overrides applied
- Generated correct folder structure per v1.2-ROADMAP.md specification
- Shared lesson files at root level (not duplicated per plan)

## Task Commits

Tasks were combined for correct implementation:

1. **Task 1 + 2: Create plan_generator.py with manifest generation and correct paths** - `f8db9a3` (feat)

**Plan metadata:** pending (docs: complete plan)

_Note: Task 2 (fix content_url paths) was included in Task 1 to avoid creating broken intermediate code_

## Files Created/Modified

- `memora_admin/memora_admin/services/build/plan_generator.py` - Plan-centric JSON generator (490 lines)
  - `generate_plan_json(plan_id)` - Main entry point returning list of file dicts
  - `_load_plan_overrides()` - Efficient override loading (one query per plan)
  - `_generate_manifest()` - Plan metadata with subject stats
  - `_generate_hierarchy()` - Subject hierarchy with Plan Overrides applied
  - `_generate_unit_content()` - Unit content with topic/lesson metadata
  - `_generate_lesson_json()` - Shared lesson content generation
  - `_calculate_subject_stats()` - Stats with is_free_preview derivation
- `memora_admin/memora_admin/services/build/__init__.py` - Export generate_plan_json

## Decisions Made

1. **Combined Tasks 1+2**: Implemented correct `plan_id` parameter in `_generate_hierarchy()` from the start rather than creating broken code and fixing it
2. **Efficient override loading**: Load all Plan Overrides once per plan into dict keyed by (doctype, name) for O(1) lookups
3. **is_free_preview derivation**: Check both units and topics for is_free=True after Plan Overrides applied
4. **No plan_id/subject_id params in unit content**: `_generate_unit_content()` doesn't need plan_id since content is same, only paths differ

## Deviations from Plan

### Plan Optimization

**1. [Merged Task 2 into Task 1] Correct paths from start**
- **Issue:** Plan specified creating `_generate_hierarchy()` with bug, then fixing in Task 2
- **Action:** Implemented correct version in Task 1 with `plan_id` parameter
- **Rationale:** Atomic commits should not contain known bugs that are immediately fixed
- **Result:** Single correct implementation, single commit

---

**Total deviations:** 1 plan optimization (merged tasks)
**Impact on plan:** Positive - cleaner commit history, no broken intermediate state

## Issues Encountered

None - implementation followed patterns from existing `generator.py`.

## User Setup Required

None - no external service configuration required.

## Next Phase Readiness

**Ready for Phase 12-03:**
- `generate_plan_json()` function exported and importable
- Returns list of file dicts with `{filename, content}` format (publisher compatible)
- File paths follow `/files/cdn/plans/{plan_id}/...` pattern for CDN serving

**Ready for Phase 12-04:**
- Plan Overrides are queried, integration hooks can trigger rebuilds
- Build worker can call `generate_plan_json(plan_id)` with same pattern as subjects

**Verification in Frappe console:**
```python
from memora_admin.memora_admin.services.build.plan_generator import generate_plan_json
files = generate_plan_json("PLAN-00001")
print(f"Generated {len(files)} files")
for f in files[:5]:
    print(f"  {f['filename']}")
```

---
*Phase: 12-plan-system-enhancement*
*Completed: 2026-02-03*
