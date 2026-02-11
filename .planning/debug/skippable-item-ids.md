---
status: diagnosed
trigger: "Stage config editor generates UUID item_id for ALL sub-elements, but skippable stages should NOT get item_ids"
created: 2026-02-11T00:00:00Z
updated: 2026-02-11T00:00:00Z
---

## Current Focus

hypothesis: CONFIRMED - game_lesson.js generates item_ids unconditionally; no awareness of is_skippable at all
test: Read game_lesson.js, cross-referenced with stage settings and FSRS processor
expecting: Confirmed
next_action: Return diagnosis

## Symptoms

expected: Skippable stages should NOT have item_ids on their sub-elements (items not tracked by FSRS)
actual: All stages get item_ids regardless of is_skippable status
errors: N/A - functional logic bug, not crash
reproduction: Open any skippable stage config editor (SENTENCE_BUILDER, MINDMAP), observe item_ids generated
started: Since item_id generation was added (Phase 27)

## Eliminated

## Evidence

- timestamp: 2026-02-11T00:01
  checked: Memora Lesson Stage Settings table (MariaDB)
  found: |
    FILL_BLANK: is_skippable=0
    MATCHING: is_skippable=0
    MINDMAP: is_skippable=1
    REVEAL: is_skippable=0
    SENTENCE_BUILDER: is_skippable=1
  implication: MINDMAP and SENTENCE_BUILDER are globally skippable stage types

- timestamp: 2026-02-11T00:02
  checked: game_lesson.js lines 1-438 (full file)
  found: |
    generateItemUUID() called unconditionally in all 4 dialog save handlers:
    - MATCHING: line 105 (p.item_id || generateItemUUID())
    - REVEAL: line 182 (h.item_id || generateItemUUID())
    - SENTENCE_BUILDER: line 260 (row.item_id || generateItemUUID())
    - MINDMAP: lines 385, 398 (node.item_id || generateItemUUID())
    No is_skippable check anywhere in the file. The editor has ZERO awareness of skippable status.
  implication: Root cause confirmed - editor generates UUIDs for all types without exception

- timestamp: 2026-02-11T00:03
  checked: FSRS processor (fsrs_processor.py lines 268-285)
  found: |
    Two-tier skippable check correctly implemented:
    1. Per-stage override: stage_row.is_skippable (line 279)
    2. Global fallback: stage_row.stage_type in skippable_types (line 283)
    Skippable stages are correctly SKIPPED during FSRS processing.
  implication: FSRS processor is safe - it already filters out skippable stages. But item_ids still get stored in config_json and sent to player app unnecessarily.

- timestamp: 2026-02-11T00:04
  checked: Build generators (generator.py:274, plan_generator.py:542)
  found: |
    Both use: "is_skippable": bool(stage.is_skippable)
    This reads ONLY the per-stage override field on child table row.
    It does NOT check the global Memora Lesson Stage Settings.is_skippable.
  implication: SECONDARY BUG - build output marks stages as is_skippable=false when the per-stage override is 0, even if the global stage type is skippable. Player app gets wrong is_skippable info.

- timestamp: 2026-02-11T00:05
  checked: Per-stage override data in MariaDB
  found: |
    All existing Memora Lesson Stage rows have is_skippable=0 (default).
    This means SENTENCE_BUILDER and MINDMAP stages in lessons show is_skippable=false
    in the build JSON, even though their types are globally skippable.
  implication: Build generators are broken - they must resolve effective is_skippable by checking global fallback

## Resolution

root_cause: |
  PRIMARY: game_lesson.js generates item_id UUIDs unconditionally for ALL stage sub-elements.
  The file has zero awareness of is_skippable - no import, no check, no conditional.

  SECONDARY: Build generators (generator.py, plan_generator.py) only read per-stage
  is_skippable override (always 0 for existing data) and NEVER check the global
  Memora Lesson Stage Settings.is_skippable fallback. This means the lesson JSON
  delivered to the player app has is_skippable=false for SENTENCE_BUILDER and MINDMAP
  stages even though they ARE globally skippable.

fix:
verification:
files_changed: []
