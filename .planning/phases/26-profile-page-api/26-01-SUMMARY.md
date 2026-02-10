---
phase: 26-profile-page-api
plan: 01
subsystem: profile-api
tags: [level-system, pydantic-models, frappe-api, fsrs-mastery, avatar]

dependency_graph:
  requires: []
  provides:
    - "Level calculation constants and function (LEVEL_THRESHOLDS, calculate_level)"
    - "Profile page Pydantic models (HeroResponse, StatsResponse, etc.)"
    - "Frappe whitelisted APIs for mastery, avatar update, avatar options"
  affects:
    - "26-02 (ProfilePageService and FastAPI endpoints depend on these models and APIs)"

tech_stack:
  added: []
  patterns:
    - "Static level lookup table (pure math, no I/O)"
    - "DocType meta-driven avatar validation (no hardcoded options)"
    - "FSRS stability-based memory classification (21.0 day threshold)"

key_files:
  created: []
  modified:
    - "fastapi_app/core/constants.py"
    - "fastapi_app/models/profile.py"
    - "memora_admin/api/profile.py"

decisions:
  - id: "LVL-STATIC"
    decision: "Level thresholds as static constants (15 levels, expandable)"
    rationale: "Levels are game design constants, not admin-configurable. Sub-microsecond calculation."
  - id: "MASTERY-21"
    decision: "FSRS maturity threshold = 21.0 days stability"
    rationale: "Standard FSRS convention; tunable later via constants change."
  - id: "AVATAR-META"
    decision: "Avatar validation reads from DocType field options, not hardcoded"
    rationale: "Prevents stale allow-lists when admin adds new avatars."

metrics:
  duration: "~3 minutes"
  completed: "2026-02-10"
---

# Phase 26 Plan 01: Level System, Models, and Frappe APIs Summary

**XP-to-level constants with calculate_level(), 10 Pydantic response models, 3 Frappe whitelisted APIs for mastery/avatar**

## What Was Done

### Task 1: Level System Constants and Pydantic Models
- Added `LEVEL_THRESHOLDS` (15 levels: 0 to 11000 XP) and `LEVEL_TITLES` to `constants.py`
- Added `calculate_level(total_xp)` pure function returning `(level, title, xp_in_level, xp_to_next_level)`
- Added `MASTERY_MATURE_THRESHOLD` (21.0) and `MASTERY_CACHE_TTL` (300) constants
- Added 10 new Pydantic models to `models/profile.py`: HeroResponse, StatsResponse, MemoryMasteryResponse, DailyXP, WeeklyActivityResponse, AvatarUpdateRequest, AvatarUpdateResponse, AvatarOptionsResponse, LogoutResponse
- Existing `PlayerProfile` model unchanged

### Task 2: Frappe Whitelisted APIs
- `get_memory_mastery(player_id, subject_id=None)`: SQL aggregation on `tabMemora Memory State` classifying stability into mature (>=21.0), learning (0<s<21.0), new (s=0) with COALESCE for null safety
- `update_player_avatar(player_id, avatar)`: Looks up profile by `user` field, validates avatar against DocType field options from meta, updates via `set_value`
- `get_avatar_options()`: Returns valid avatar strings from DocType metadata
- Shared `_get_avatar_options_from_meta()` helper for DRY validation
- Existing `get_profiles_batch()` unchanged

## Decisions Made

| ID | Decision | Rationale |
|----|----------|-----------|
| LVL-STATIC | 15-level static lookup table | Game design constant, no I/O needed, expandable |
| MASTERY-21 | 21.0 day FSRS maturity threshold | Standard convention, easily tunable |
| AVATAR-META | DocType meta-driven avatar validation | Prevents stale hardcoded lists |

## Deviations from Plan

None -- plan executed exactly as written.

## Verification Results

- `calculate_level(0)` -> `(1, 'Beginner', 0, 100)` -- correct
- `calculate_level(150)` -> `(2, 'Learner', 50, 150)` -- correct
- `calculate_level(11000)` -> `(15, 'Transcendent', 0, 0)` -- max level correct
- All 10 Pydantic models import without errors
- `get_memory_mastery("test@example.com")` returns `{"mature": 0, "learning": 0, "new_items": 0, "total": 0}`
- `get_avatar_options()` returns `["avatar 1", "avatar 2"]`
- Ruff lint and format checks pass on all modified files

## Commits

| Task | Commit | Description |
|------|--------|-------------|
| 1 | `1ba31c1` | Level system constants and profile page Pydantic models |
| 2 | `0795ef9` | Frappe whitelisted APIs for mastery, avatar update, and avatar options |

## Next Phase Readiness

Plan 26-02 depends on all artifacts from this plan:
- `calculate_level()` for ProfilePageService hero section computation
- All Pydantic models as endpoint response types
- Frappe APIs (`get_memory_mastery`, `update_player_avatar`, `get_avatar_options`) called via FrappeClient from ProfilePageService

No blockers identified.
