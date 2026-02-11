---
phase: 27-memory-state-redesign
plan: 05
subsystem: content-pipeline
tags: [item_id, is_skippable, build-generator, editor, gap-closure]
requires:
  - 27-02 (item_id generation in content pipeline)
  - 27-03 (FSRS two-tier skippable logic)
provides:
  - Skippable-aware item_id generation in stage config editor
  - Correct is_skippable output in build JSON (two-tier resolution)
  - Clean build output (no item_ids on skippable stages)
affects:
  - Player app build consumption (correct is_skippable flags)
  - CDN content (clean JSON without stale item_ids)
tech-stack:
  added: []
  patterns:
    - Two-tier is_skippable resolution (per-stage override then global Memora Lesson Stage Settings)
    - Async frappe.db.get_value for client-side DocType lookups
key-files:
  created: []
  modified:
    - memora_admin/public/js/game_lesson.js
    - memora_admin/memora_admin/services/build/generator.py
    - memora_admin/memora_admin/services/build/plan_generator.py
decisions:
  - "frappe.db.get_value used for client-side skippable check (not frappe.call with whitelisted method)"
  - "item_id keys omitted entirely (not set to null) on skippable stages to keep config clean"
  - "_strip_item_ids helper recurses into nested children for MINDMAP config shape"
metrics:
  duration: "~5 minutes"
  completed: "2026-02-11"
---

# Phase 27 Plan 05: Skippable Stage item_id Fix (Gap Closure) Summary

**One-liner:** Fixed editor and build generators to skip item_id generation for effectively-skippable stages using two-tier resolution (per-stage override then global Memora Lesson Stage Settings fallback).

## Performance

- **Duration:** ~5 minutes
- **Start:** 2026-02-11T11:54:03Z
- **End:** 2026-02-11T11:58:39Z
- **Tasks:** 2/2 completed
- **Files modified:** 3

## Accomplishments

1. **Skippable-aware stage config editor (game_lesson.js)**
   - Added `isEffectivelySkippable(row)` async helper that checks per-stage `is_skippable` override first, then falls back to `frappe.db.get_value("Memora Lesson Stage Settings", ...)` for global setting
   - Made `edit_content_btn` handler async to await skippable resolution before opening dialog
   - Added `skipItemIds` parameter to all 4 dialog functions (MATCHING, REVEAL, SENTENCE_BUILDER, MINDMAP)
   - When `skipItemIds` is true: item_id field is omitted entirely from saved config_json
   - When `skipItemIds` is false: existing behavior preserved (generate UUID if missing)
   - Existing stale item_ids on skippable stages are stripped on re-save (not preserved)

2. **Two-tier is_skippable in build generators (generator.py + plan_generator.py)**
   - Added `_get_skippable_stage_types()` helper (same pattern as `fsrs_processor.py` lines 42-49)
   - Replaced `bool(stage.is_skippable)` with `bool(stage.is_skippable) or (stage.stage_type in skippable_types)`
   - Added `_strip_item_ids(config)` helper that recursively removes item_id keys from all config shapes (pairs, highlights, words, nested children)
   - Called once per lesson (not per stage) for performance
   - Applied identically to both `generator.py` and `plan_generator.py`

## Task Commits

| Task | Name | Commit | Key Files |
|------|------|--------|-----------|
| 1 | Add skippable-awareness to stage config editor | `7719db7` | `memora_admin/public/js/game_lesson.js` |
| 2 | Fix build generators two-tier is_skippable | `4dcb512` | `generator.py`, `plan_generator.py` |

## Files Modified

| File | Changes |
|------|---------|
| `memora_admin/public/js/game_lesson.js` | Added `isEffectivelySkippable()`, async `edit_content_btn`, `skipItemIds` param to all 4 dialogs |
| `memora_admin/memora_admin/services/build/generator.py` | Added `_get_skippable_stage_types()`, `_strip_item_ids()`, two-tier resolution in `_generate_lesson_json` |
| `memora_admin/memora_admin/services/build/plan_generator.py` | Same additions as generator.py |

## Decisions Made

1. **frappe.db.get_value for client-side check:** Used Frappe's standard client-side DB access (not frappe.call with whitelisted method) for checking global is_skippable setting. This is simpler and sufficient for the single-value lookup.

2. **Omit item_id entirely (not null):** When a stage is skippable, the item_id key is completely omitted from the config object rather than being set to null. This keeps the config clean and makes it unambiguous that the stage has no tracked items.

3. **Recursive _strip_item_ids:** The helper recurses into nested "children" arrays to handle MINDMAP's two-level structure (branches with child items).

## Deviations from Plan

None - plan executed exactly as written.

## Issues Encountered

None.

## Next Phase Readiness

This plan closes the gap identified in Phase 27 UAT. The full pipeline is now consistent:
- **Editor:** Generates item_ids only for non-skippable stages
- **Build generators:** Output correct is_skippable flag and clean config (no item_ids on skippable stages)
- **FSRS processor:** Already had correct two-tier logic (no changes needed)
- **Session API:** Already handles this correctly (no changes needed)

No blockers for future work.

## Self-Check: PASSED
