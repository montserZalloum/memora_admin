# Research: Review Item Table

**Feature**: 024-review-item-table
**Date**: 2026-02-22

## Research Questions & Findings

### RQ-1: What config_json formats exist per stage type?

**Decision**: The Review Item table must accommodate 4 non-skippable stage types with different data structures.

**Findings** (from live DB inspection):

| Stage Type | Skippable? | Item Structure | Has item_id? |
|-----------|------------|----------------|-------------|
| QUESTION | No | `{"question": "...", "answers": [{"text": "...", "is_correct": bool, "item_id": "UUID"}, ...]}` | Yes, per answer |
| FILL_BLANK | No | `{"text": "...", "blanks": [{"from": int, "to": int, "item_id": "UUID"}], "distractors": ["..."]}` | Yes, per blank |
| MATCHING | No | `{"pairs": [{"left": "...", "right": "...", "item_id": "UUID"}]}` | Yes, per pair |
| REVEAL | No | No content found in DB yet | Unknown |
| INFORMATION | Yes (global) | `{"text": "...", "highlights": [...]}` | No — skipped |
| MINDMAP | Yes (global) | N/A | N/A — skipped |
| SENTENCE_BUILDER | Yes (global) | N/A | N/A — skipped |

**Rationale**: Each stage type embeds `item_id` in different sub-structures. The extraction logic must dispatch on `stage_type` to locate items.

**Alternatives considered**:
- Store raw `config_json` per item — rejected: too large, defeats the purpose of fast retrieval
- Only support QUESTION stages — rejected: FILL_BLANK and MATCHING are non-skippable and create Memory State entries

### RQ-2: How should non-MCQ items be stored in the Review Item table?

**Decision**: Store raw item data as JSON in a `content_json` TEXT column alongside the flat MCQ fields. The `stage_type` field determines which format the client renders.

**Rationale**: The spec says "question generation logic is out of scope." The flat MCQ fields (`question_text`, `choice_1`–`choice_4`, `correct_choice`) work perfectly for QUESTION stages. For FILL_BLANK/MATCHING, the raw item data goes in `content_json` and the client renders based on `stage_type`. This avoids blocking the feature on question-generation logic while supporting all non-skippable stage types from day one.

**QUESTION extraction** (MCQ fields populated):
- `question_text` = stage's `question` field
- `choice_1..4` = `answers[0..3].text`
- `correct_choice` = 1-based index of answer where `is_correct=true`
- `content_json` = NULL (MCQ fields sufficient)

**FILL_BLANK extraction** (content_json only):
- `question_text` = stage's `text` field (the sentence with blanks)
- `choice_1..4` = NULL (not MCQ format)
- `correct_choice` = NULL
- `content_json` = `{"blank_from": int, "blank_to": int, "correct_word": "...", "distractors": [...]}`

**MATCHING extraction** (content_json only):
- `question_text` = stage's `instruction` field
- `choice_1..4` = NULL
- `correct_choice` = NULL
- `content_json` = `{"left": "...", "right": "..."}`

**Alternatives considered**:
- Force all items into MCQ format at extraction time — rejected per spec: "question generation logic is out of scope"
- Omit non-QUESTION items entirely — rejected: they exist in Memory State and would cause "missing item" log spam

### RQ-3: Should the Review Item table be RANGE-partitioned like Memory State?

**Decision**: No. Use a standard Frappe DocType with Frappe ORM.

**Rationale**:
- Memory State needs partitioning because it's per-player × per-item (10B+ rows). Review Item is per-item only (one row per item across all players).
- Expected scale: ~40M items per spec SC-002. Standard InnoDB with proper indexes handles this comfortably.
- Items are written on admin save (low frequency), read on student review (high frequency but by PK).
- Using Frappe ORM means standard DocType patterns, admin panel visibility, and no special setup.py migration.
- Primary key lookup on UUID index is O(1) regardless of table size.

**Alternatives considered**:
- RANGE partition by subject — rejected: unnecessary complexity for ~40M rows with PK lookups
- Raw SQL only (like Memory State) — rejected: no binary UUID columns, no partition pruning needed

### RQ-4: How to use item_id as primary key with Frappe's autoname?

**Decision**: Use `autoname = "field:item_id"` in the DocType definition so the `name` column IS the UUID string.

**Rationale**: Frappe's `name` column is the primary key. By setting `autoname = "field:item_id"`, the UUID string becomes the PK. This enables direct `frappe.get_doc("Memora Review Item", item_id)` lookups and `WHERE name IN (...)` queries for batch retrieval — no binary conversion needed.

**Alternatives considered**:
- BINARY(16) item_id like Memory State — rejected: Frappe ORM can't handle binary PKs; this table doesn't need partition pruning
- Separate `name` (autoincrement) + `item_id` (unique index) — rejected: adds an unnecessary join/index hop for the primary access pattern (batch get by item_id)

### RQ-5: How to extract items from config_json during lesson save?

**Decision**: Add a doc_event hook on `Memora Lesson` (`on_update`) that calls a new function `sync_review_items(lesson)`. This function:
1. Loads the lesson's stages via `doc.stages` (child table)
2. Skips globally-skippable stage types + per-stage `is_skippable` flag
3. Parses `config_json` per stage, dispatching on `stage_type`
4. Extracts items with their `item_id` values
5. Upserts Review Item records (insert-or-update on the UUID PK)
6. Deletes orphaned Review Items (items in DB but not in current config)

**Trigger point**: Added as a new event in `hooks.py` alongside the existing `on_content_updated` hook for `Memora Lesson`.

**Rationale**: The existing `on_content_updated` hook handles cache invalidation and build queueing. The review item sync is a separate concern (data denormalization) and should be a separate function for clarity.

### RQ-6: How to handle lesson/stage deletion cleanup?

**Decision**: Add an `on_trash` hook on `Memora Lesson` that deletes all Review Items where `lesson = doc.name`, and also cleans up corresponding Memory State records.

**Memory State cleanup**: Use raw SQL `DELETE FROM \`tabMemora Memory State\` WHERE lesson = %(lesson)s AND season_seq = %(season_seq)s` — must include `season_seq` for partition pruning. Get `season_seq` from the current active season.

**Rationale**: The spec requires (FR-005) that deleting content cascades to both Review Item and Memory State. Lesson-level deletion is the most common bulk cleanup.

### RQ-7: How to add `review_session_size` to Memora Settings?

**Decision**: Add a new field to the existing `memora_settings.json` DocType:
- Field: `review_session_size` (Int, default 10)
- Section: Under the FSRS section (logical grouping)
- Read via: `frappe.get_single("Memora Settings").review_session_size or 10`

**Rationale**: Memora Settings is already the global configuration singleton. Adding a field is trivial and follows existing patterns (`default_max_hearts`, `max_devices_per_player`).

### RQ-8: Where should the batch-fetch endpoint live?

**Decision**: Extend the existing `GET /reviews/{subject}` endpoint (or add a new Frappe API function) to JOIN Review Item data into the due items response.

**Current flow**: `get_due_items()` in `memora_admin/api/reviews.py` queries Memory State + Lesson Stage to get `item_id, stage_id, lesson_id, stage_type`. It returns metadata only — no question content.

**New flow**: Add a JOIN to `tabMemora Review Item` (by item_id UUID match) to include `question_text, choice_1..4, correct_choice, content_json` in the response.

**Rationale**: The existing endpoint already returns due items. Enriching it with question data (from Review Item table) is the minimal change to satisfy SC-001 (<5ms retrieval). No new endpoint needed.

**Alternative considered**: Separate `GET /reviews/{subject}/items` endpoint — rejected: creates an extra round-trip for the client.

### RQ-9: How to handle the QUESTION stage item_id-per-answer pattern?

**Decision**: Each answer choice in a QUESTION stage has its own `item_id`. However, for review purposes, the **question** is the reviewable unit, not each individual answer. The FSRS processor creates one Memory State entry per `item_id`.

**Observation**: In QUESTION stages, each answer has its own `item_id`. The FSRS processor records whichever `item_id` appears in the interaction log. The spec says "One review question per item." This means each `item_id` maps to one question.

**Approach**: For QUESTION stages, create one Review Item per `item_id` (per answer). Each Review Item stores the FULL question text and ALL choices, with `correct_choice` indicating which answer corresponds to this specific item_id.

**Rationale**: This matches how Memory State works (one row per item_id) and how the client will fetch questions (by item_id set). The question text is duplicated across answer-item Review Items, but this is a small cost vs. the complexity of a normalized schema.

**Alternative considered**: One Review Item per question with a composite key — rejected: breaks 1:1 mapping with Memory State's item_id.
