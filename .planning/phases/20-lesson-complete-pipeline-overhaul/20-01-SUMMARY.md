---
phase: 20
plan: 01
subsystem: hierarchy-settings
tags: [hierarchy, settings, max-hearts, xp, fsrs, pydantic]
requires:
  - phase-19 (stage_id fix for FSRS)
provides:
  - Per-lesson max_hearts in hierarchy API
  - XP fallback from Memora Settings (not hardcoded)
  - default_max_hearts and xp_per_heart in settings API
  - LessonInfo.max_hearts Pydantic field
  - GamificationSettings.default_max_hearts and xp_per_heart fields
  - fsrs dependency formalized in requirements.txt
affects:
  - 20-02 (StageResult time_spent + legacy removal)
  - 20-03 (Lua hot path needs max_hearts from hierarchy, xp_per_heart from settings)
  - 20-04 (FSRS background task uses fsrs package)
tech-stack:
  added: [fsrs>=6.0.0]
  patterns: [settings-fallback-pattern, enriched-hierarchy]
key-files:
  created: []
  modified:
    - memora_admin/api/hierarchy.py
    - memora_admin/api/settings.py
    - fastapi_app/models/progress.py
    - fastapi_app/models/settings.py
    - requirements.txt
key-decisions:
  - "Fallback default for base_xp is 100 (from settings), not hardcoded 10"
  - "Fallback default for max_hearts is 5 (from settings)"
  - "xp_per_heart defaults to 0 (no hearts bonus until configured)"
  - "fsrs_weights NOT exposed in settings API (fetched directly in FSRS task)"
duration: ~3 minutes
completed: 2026-02-07
---

# Phase 20 Plan 01: Hierarchy & Settings Enrichment Summary

**One-liner:** Per-lesson max_hearts and settings-based XP fallback in hierarchy API, plus hearts/FSRS fields in settings API and Pydantic models.

## Performance

- **Duration:** ~3 minutes
- **Started:** 2026-02-07T09:33:45Z
- **Completed:** 2026-02-07T09:36:37Z
- **Tasks:** 2/2
- **Files modified:** 5

## Accomplishments

1. **Enriched hierarchy API** to return `max_hearts` per lesson alongside `xp`, with fallback to `Memora Settings.default_max_hearts` when lesson value is 0.
2. **Replaced hardcoded XP fallback** (was `10`) with `Memora Settings.base_lesson_xp` (default `100`), making XP configurable from the admin panel.
3. **Enriched settings API** with `default_max_hearts` and `xp_per_heart` fields for the hearts bonus XP calculation in Plan 03.
4. **Updated LessonInfo Pydantic model** with `max_hearts: int = 5` field so FastAPI deserialization carries the value.
5. **Updated GamificationSettings Pydantic model** with `default_max_hearts: int = 5` and `xp_per_heart: int = 0`.
6. **Formalized fsrs dependency** in `requirements.txt` (v6.3.0 already installed, pinned `>=6.0.0`).

## Task Commits

| Task | Name | Commit | Key Files |
|------|------|--------|-----------|
| 1 | Enrich Frappe hierarchy and settings APIs | `79f45d8` | hierarchy.py, settings.py |
| 2 | Update FastAPI models and add fsrs dependency | `0495010` | progress.py, settings.py, requirements.txt |

## Files Modified

| File | Changes |
|------|---------|
| `memora_admin/api/hierarchy.py` | Load Memora Settings for fallback, add max_hearts to query/response, replace hardcoded 10 |
| `memora_admin/api/settings.py` | Add default_max_hearts and xp_per_heart to returned dict |
| `fastapi_app/models/progress.py` | Add max_hearts field to LessonInfo model |
| `fastapi_app/models/settings.py` | Add default_max_hearts and xp_per_heart to GamificationSettings |
| `requirements.txt` | Add fsrs>=6.0.0 |

## Decisions Made

| Decision | Rationale |
|----------|-----------|
| XP fallback = Memora Settings `base_lesson_xp` (not 10) | Admin-configurable, consistent with existing pattern |
| max_hearts fallback = Memora Settings `default_max_hearts` (not hardcoded) | Same admin-configurable pattern |
| xp_per_heart defaults to 0 | Hearts bonus is opt-in (0 = no bonus until admin configures) |
| fsrs_weights NOT in settings API | Plan specifies FSRS task fetches weights directly, not via settings API |
| fsrs pinned at >=6.0.0 | v6.3.0 installed, >=6.0.0 allows patch updates while requiring v6 API |

## Deviations from Plan

None - plan executed exactly as written.

## Issues Encountered

- Bench execute commands could not run (database access denied and app not installed on available sites). Verification done via Python syntax validation, import tests, and pattern checks instead.

## Next Phase Readiness

**Plan 20-02 (StageResult time_spent + legacy removal):** Ready to proceed. No dependencies on this plan's outputs.

**Plan 20-03 (Lua hot path + hearts XP):** This plan's outputs are prerequisites:
- `max_hearts` now available in hierarchy data for hearts bonus calculation
- `xp_per_heart` now available in settings for hearts bonus formula
- `LessonInfo.max_hearts` available for FastAPI service layer

**Plan 20-04 (FSRS background task):** This plan's `fsrs>=6.0.0` dependency is formalized and importable.

## Self-Check: PASSED
