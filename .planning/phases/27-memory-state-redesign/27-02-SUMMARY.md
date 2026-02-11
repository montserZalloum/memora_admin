---
phase: 27-memory-state-redesign
plan: 02
subsystem: api
tags: [uuid, item-id, fastapi, pydantic, frappe-js, game-session, interaction-log, content-pipeline]

# Dependency graph
requires:
  - phase: 27-memory-state-redesign-01
    provides: "item_id BINARY(16) column on Memory State and Interaction Log DocTypes"
provides:
  - "UUID item_id generation in all 4 stage config editor dialogs (MATCHING, REVEAL, SENTENCE_BUILDER, MINDMAP)"
  - "item_id preservation across re-saves (not regenerated)"
  - "ItemResult Pydantic model (item_id + fail_count)"
  - "Per-item interaction JSONs in end_session handler"
  - "item_id written to Interaction Log via flush_interaction_buffer"
  - "Backward compatibility: old clients without items field still work"
affects:
  - 27-03 (FSRS rewrite reads item_id from Interaction Log to create per-item Memory States)
  - 27-04 (review/profile reads Memory States with item-level granularity)

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "Hidden field pattern: Frappe dialog table hidden field preserves data across re-opens"
    - "Backward-compatible schema migration: SENTENCE_BUILDER words format string[] -> {item_id, text}[]"
    - "Per-item interaction fan-out: single StageResult with N items -> N interaction JSONs in buffer"

key-files:
  created: []
  modified:
    - "memora_admin/public/js/game_lesson.js"
    - "fastapi_app/models/game_session.py"
    - "fastapi_app/api/v1/endpoints/sessions.py"
    - "memora_admin/tasks/sync.py"

key-decisions:
  - "SENTENCE_BUILDER words format changed from string array to object array with item_id (backward compat preserved)"
  - "Per-item interactions share stage time_spent (each item gets same time_spent as its parent stage)"
  - "Hearts calculation unchanged (uses stage-level fail_count, not item-level)"
  - "Generator.py unchanged: _parse_stage_config() already passes through full config dict including item_id"

patterns-established:
  - "Hidden item_id field pattern: all dialog tables carry item_id via hidden Data field for UUID persistence"
  - "Per-item fan-out: when stage.items is populated, one interaction JSON per item; when empty, one per stage (backward compat)"

# Metrics
duration: 4min
completed: 2026-02-11
---

# Phase 27 Plan 02: Content Pipeline Summary

**UUID item_id generation in all stage config editor dialogs with per-item session results flowing through interaction buffer to Interaction Log**

## Performance

- **Duration:** 4 min
- **Started:** 2026-02-11T09:47:15Z
- **Completed:** 2026-02-11T09:50:47Z
- **Tasks:** 2
- **Files modified:** 4

## Accomplishments
- All 4 stage type dialogs (MATCHING, REVEAL, SENTENCE_BUILDER, MINDMAP) generate and preserve UUID item_id per sub-element
- SENTENCE_BUILDER backward compatibility: old string-array format loads correctly, converted to object format on save
- ItemResult model and per-item interaction JSONs in session end API
- Interaction buffer sync writes item_id to Interaction Log DocType
- Full backward compatibility: old clients without items field work unchanged

## Task Commits

Each task was committed atomically:

1. **Task 1: Add UUID item_id generation to stage config editor dialogs** - `9a45205` (feat)
2. **Task 2: Update session end API and interaction buffer sync** - `bd1e88c` (feat)

## Files Created/Modified
- `memora_admin/public/js/game_lesson.js` - generateItemUUID() helper, hidden item_id field in all 4 dialog tables, backward-compat SENTENCE_BUILDER word format migration
- `fastapi_app/models/game_session.py` - ItemResult model (item_id + fail_count), StageResult.items optional list
- `fastapi_app/api/v1/endpoints/sessions.py` - Per-item interaction JSON fan-out in end_session handler
- `memora_admin/tasks/sync.py` - item_id field in Interaction Log doc creation

## Decisions Made
- **SENTENCE_BUILDER words format migration:** Changed from `["word1", "word2"]` (string array) to `[{"item_id": "uuid", "text": "word1"}, ...]` (object array). Old format detected at load time via `typeof w === 'string'` and auto-migrated on save.
- **Per-item time_spent sharing:** Each item in a stage shares the same `time_spent` value as the parent stage. Per-item time breakdown is not tracked (would require client-side instrumentation).
- **Hearts calculation unchanged:** Still uses `stage.fail_count` (sum of stage-level fails), not item-level. Hearts are a game mechanic, not an FSRS input.
- **No generator.py changes needed:** `_parse_stage_config()` already returns the full JSON dict via `json.loads()`, so item_id keys pass through automatically.

## Deviations from Plan

None - plan executed exactly as written.

## Issues Encountered
None.

## User Setup Required
None - no external service configuration required.

## Next Phase Readiness
- Content pipeline complete: item_ids flow from admin editor through generated lesson JSON
- Session API ready: per-item results flow through request body to interaction buffer to Interaction Log
- Plan 27-03 (FSRS Rewrite) can proceed: Interaction Log records now include item_id for per-item Memory State creation
- Plan 27-04 (Review/Profile Update) can proceed: Memory States will have item-level granularity

---
*Phase: 27-memory-state-redesign*
*Completed: 2026-02-11*
