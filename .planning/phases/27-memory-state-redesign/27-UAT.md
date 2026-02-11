---
status: diagnosed
phase: 27-memory-state-redesign
source: 27-01-SUMMARY.md, 27-02-SUMMARY.md, 27-03-SUMMARY.md, 27-04-SUMMARY.md
started: 2026-02-11T12:00:00Z
updated: 2026-02-11T12:30:00Z
---

## Current Test

[testing complete]

## Tests

### 1. Memory State Schema (BIGINT PK + Partitioning)
expected: Run `bench --site x.conanacademy.com migrate` then verify: `name` column is BIGINT(20), RANGE partitions exist (p_season_1, p_future), UUID polyfill functions exist, composite and unique indexes present
result: pass

### 2. Season DocType has season_seq Field
expected: Open any Season record in Frappe Desk. A `season_seq` integer field should be visible and populated (e.g., 1 for the active season). This field drives partition routing.
result: pass

### 3. Stage Config Editor — Item UUID Generation (MATCHING)
expected: In Frappe Desk, open a Lesson with a MATCHING stage. Click edit on the stage config. Each matching pair row should have a hidden `item_id` field with a UUID value. Saving and re-opening preserves the same UUIDs (not regenerated).
result: pass

### 4. Stage Config Editor — Item UUID Generation (SENTENCE_BUILDER)
expected: Open a Lesson with a SENTENCE_BUILDER stage. Click edit on stage config. Each word should have an `item_id` UUID. If upgrading from old format (plain string array), words should auto-convert to objects with `item_id` + `text` fields on save.
result: issue
reported: "the SENTENCE_BUILDER is is_skippable:true, which mean it not part of the FSRS system, and that also must be applied on any stage_type that has is_skippable:true, we must not give them any item_id"
severity: major

### 5. Session End API — Per-Item Results
expected: Call `POST /api/v1/sessions/end` with a StageResult that includes `items: [{item_id: "uuid", fail_count: 0}, ...]`. The endpoint should accept it without error. Interaction Log records should include the `item_id` values.
result: skipped
reason: Frontend not ready yet

### 6. FSRS Creates Per-Item Memory States
expected: After completing a lesson containing interactive stages (MATCHING/REVEAL/SENTENCE_BUILDER), run FSRS processing. Check `tabMemora Memory State` — there should be one Memory State row per item (sub-element) rather than one per stage. Each row has a BINARY(16) `item_id` and the current `season_seq`.
result: skipped
reason: Frontend not ready yet

### 7. Review Overview Returns Item Counts
expected: Call `GET /api/v1/reviews` for a player who has due reviews. The response should show item-level due counts (not stage counts). Each subject entry shows how many items are due for review.
result: skipped
reason: Frontend not ready yet

### 8. Due Items Endpoint Returns item_id
expected: Call `GET /api/v1/reviews/{subject}`. Response should return `DueItem` objects with `item_id` (UUID string), `stage_id`, `lesson_id`, and `stage_type` — not the old `DueStage` format.
result: skipped
reason: Frontend not ready yet

### 9. Submit Reviews with Item-Level Results
expected: Call `POST /api/v1/reviews/{subject}/submit` with `items: [{item_id: "uuid", fail_count: 0}, ...]`. The endpoint should accept item-level review results, update Memory State per item, award 3 XP, and return `remaining_due` + `has_more`.
result: skipped
reason: Frontend not ready yet

### 10. Profile Mastery Counts Items
expected: Call the profile mastery endpoint for a player with Memory States. The mature/learning/new breakdown should count individual items (not stages). E.g., a stage with 5 matching pairs = 5 items counted.
result: skipped
reason: Frontend not ready yet

## Summary

total: 10
passed: 3
issues: 1
pending: 0
skipped: 6

## Gaps

- truth: "Skippable stage types (is_skippable:true) should NOT get item_id UUIDs since they are excluded from FSRS"
  status: failed
  reason: "User reported: the SENTENCE_BUILDER is is_skippable:true, which mean it not part of the FSRS system, and that also must be applied on any stage_type that has is_skippable:true, we must not give them any item_id"
  severity: major
  test: 4
  root_cause: "game_lesson.js generates item_id UUIDs unconditionally for ALL stage sub-elements with zero awareness of is_skippable. Secondary: build generators (generator.py, plan_generator.py) only check per-stage is_skippable override, missing the global Memora Lesson Stage Settings fallback."
  artifacts:
    - path: "memora_admin/public/js/game_lesson.js"
      issue: "Lines 105, 182, 260, 385, 398 — generateItemUUID() called without skippable check"
    - path: "memora_admin/memora_admin/services/build/generator.py"
      issue: "Line 274 — bool(stage.is_skippable) only reads per-stage override, misses global fallback"
    - path: "memora_admin/memora_admin/services/build/plan_generator.py"
      issue: "Line 542 — same as generator.py, only per-stage override"
  missing:
    - "Editor: check is_skippable (per-stage override + global fallback) before generating item_ids"
    - "Build generators: resolve effective is_skippable with two-tier logic (per-stage then global)"
  debug_session: ".planning/debug/skippable-item-ids.md"
