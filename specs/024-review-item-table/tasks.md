# Tasks: Review Item Table

**Input**: Design documents from `/specs/024-review-item-table/`
**Prerequisites**: plan.md, spec.md, research.md, data-model.md, contracts/review-items-api.md

**Tests**: Included per Constitution VIII (Test-First Coverage). Each phase includes test tasks alongside implementation.

**Organization**: Tasks grouped by user story. US2 (populate) before US1 (retrieve) because US2 creates the data US1 reads.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependencies)
- **[Story]**: Which user story this task belongs to (e.g., US1, US2, US3)

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: Create the Memora Review Item DocType and add the review_session_size setting.

- [X] T001 Create Memora Review Item DocType directory with `__init__.py`, JSON schema (`autoname: "field:item_id"`, 13 fields: item_id, subject, track, unit, topic, lesson, stage_id, stage_type, question_text, choice_1–4, correct_choice, content_json), and indexes on lesson, subject, stage_id in `memora_admin/memora_admin/doctype/memora_review_item/memora_review_item.json`
- [X] T002 [P] Create Memora Review Item document class with validation (item_id UUID format, correct_choice 1–4 range, at least one of choice_1 or content_json non-null) in `memora_admin/memora_admin/doctype/memora_review_item/memora_review_item.py`
- [X] T003 [P] Add `review_session_size` Int field (default 10, label "Review Session Size") to the FSRS section after `fsrs_weights` in `memora_admin/memora_admin/doctype/memora_settings/memora_settings.json`

**Checkpoint**: DocType exists, `bench migrate` creates the table, Settings has new field.

---

## Phase 2: User Story 2 — Teacher Saves Lesson → Auto-Populate (Priority: P1) 🎯 MVP

**Goal**: When a teacher saves a lesson, automatically extract items from non-skippable stages and create/update Review Item records.

**Independent Test**: Save a lesson with known stages, then query `tabMemora Review Item` to verify records exist with correct hierarchy refs and question data.

### Implementation

- [X] T004 [US2] Create item extraction functions dispatching by stage_type (QUESTION → MCQ fields, FILL_BLANK → content_json with blank data, MATCHING → content_json with pair data) in `memora_admin/api/review_items.py`
- [X] T005 [US2] Create `sync_review_items(lesson_doc)` orchestrator in `memora_admin/api/review_items.py` — collect item_ids from non-skippable stages, fetch existing Review Items for lesson, upsert new/changed items, delete orphans (items in DB but not in current config), delete orphaned Memory State records (raw SQL with season_seq for partition pruning)
- [X] T006 [US2] Create `on_lesson_save()` in `memora_admin/events/review_item_sync.py` that calls `sync_review_items(doc)`, and register `Memora Lesson` `on_update` doc_event in `hooks.py`

### Tests

- [X] T014 [US2] Write unit tests for item extraction functions (T004): QUESTION stage → MCQ fields, FILL_BLANK → content_json with blank data, MATCHING → content_json with pair data, unknown stage type → content_json fallback, empty config_json → no items in `memora_admin/memora_admin/doctype/memora_review_item/test_memora_review_item.py`
- [X] T015 [US2] Write integration test for sync_review_items: save a lesson with 3 non-skippable stages (2 items each) → verify 6 Review Items created with correct hierarchy refs; re-save with 1 item removed → verify orphan deleted; re-save with stage switched to skippable → verify stage items and their Memory State records deleted in `memora_admin/memora_admin/doctype/memora_review_item/test_memora_review_item.py`

**Checkpoint**: Saving a lesson in admin panel creates Review Item records. Re-saving updates them. Removing items from stages deletes orphans. All Phase 2 tests pass.

---

## Phase 3: User Story 1 — Student Retrieves Review Questions Instantly (Priority: P1)

**Goal**: Enrich the existing `GET /reviews/{subject}` response with question data from Review Item table via a single SQL JOIN.

**Independent Test**: Insert Review Items into the table, call the endpoint, verify response contains question_text, choices, correct_choice, and content_json.

### Implementation

- [X] T007 [P] [US1] Update `DueItem` Pydantic model — add `question_text: str | None`, `choices: list[str]`, `correct_choice: int | None`, `content_json: dict | None`; remove `stability` and `difficulty` fields in `fastapi_app/models/review.py`
- [X] T008 [P] [US1] Modify `get_due_items()` SQL query to LEFT JOIN `tabMemora Review Item ri ON ri.name = BIN_TO_UUID(ms.item_id)` and SELECT `ri.question_text, ri.choice_1, ri.choice_2, ri.choice_3, ri.choice_4, ri.correct_choice, ri.content_json`; assemble non-empty choices into a list in the response in `memora_admin/api/reviews.py`
- [X] T009 [US1] Update FastAPI reviews endpoint and service to pass enriched fields (question_text, choices, correct_choice, content_json) from Frappe API response through to the updated DueItem model in `fastapi_app/api/v1/endpoints/reviews.py` and `fastapi_app/services/review.py`

### Tests

- [X] T016 [US1] Write endpoint test for enriched review response: insert Review Items into DB, call `GET /api/v1/reviews/{subject}`, verify response contains question_text, choices (non-empty only), correct_choice, content_json, stage_type; verify items without Review Item records return gracefully with null question fields; verify `stability` and `difficulty` fields are NOT present in response (FR-011) in `fastapi_app/tests/test_review_items.py`

**Checkpoint**: `GET /reviews/{subject}` returns items with question data. Items without Review Item records return gracefully with null question fields. Phase 3 tests pass.

---

## Phase 4: User Story 3 — Teacher Deletes Content → Orphaned Data Cleanup (Priority: P2)

**Goal**: When a teacher deletes a lesson, all associated Review Items and Memory State records are cleaned up.

**Independent Test**: Create Review Items for a lesson, delete the lesson, verify all Review Items and their Memory State records are gone.

### Implementation

- [X] T010 [US3] Create `delete_review_items_for_lesson(lesson_name)` in `memora_admin/api/review_items.py` — DELETE FROM `tabMemora Review Item` WHERE lesson = lesson_name, then DELETE corresponding Memory State records (raw SQL with season_seq for partition pruning)
- [X] T011 [US3] Add `on_lesson_trash()` hook in `memora_admin/events/review_item_sync.py` that calls `delete_review_items_for_lesson(doc.name)`, and register `Memora Lesson` `on_trash` doc_event in `hooks.py`

### Tests

- [X] T017 [US3] Write integration test for cascade deletion: create Review Items for a lesson, delete the lesson via `on_trash`, verify all Review Items deleted and corresponding Memory State records cleaned up; also test stage-level deletion (stage removed from lesson, saved → orphan cleanup via sync) in `memora_admin/memora_admin/doctype/memora_review_item/test_memora_review_item.py`

**Checkpoint**: Deleting a lesson in admin panel removes all Review Items and Memory State records. Skippable stage toggling (handled by sync in Phase 2) also cleans up correctly. Phase 4 tests pass.

---

## Phase 5: User Story 4 — Review Session Size is Configurable (Priority: P3)

**Goal**: The `review_session_size` Memora Settings field controls how many items are fetched per review session.

**Independent Test**: Change the setting value, request a review session, verify the response respects the new limit.

### Implementation

- [X] T012 [US4] Wire `review_session_size` from Memora Settings into `get_due_items()` LIMIT clause (replacing hardcoded 10) in `memora_admin/api/reviews.py`, and pass the configured limit from the FastAPI endpoint in `fastapi_app/api/v1/endpoints/reviews.py`

**Checkpoint**: Changing `review_session_size` in Memora Settings changes the number of items returned by the review endpoint.

---

## Phase 6: Polish & Cross-Cutting Concerns

**Purpose**: Validation and end-to-end verification.

- [X] T013 Run quickstart.md validation — `bench migrate`, restart Frappe workers and FastAPI, bulk sync existing lessons via bench console, verify Review Item count and sample data, test end-to-end review session flow

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: No dependencies — start immediately
- **US2 (Phase 2)**: Depends on Phase 1 (DocType must exist) — BLOCKS Phase 3 for end-to-end testing
- **US1 (Phase 3)**: Depends on Phase 1 (DocType for JOIN). Can start in parallel with Phase 2 (T007, T008 only need schema, not data). Full testing needs Phase 2 data.
- **US3 (Phase 4)**: Depends on Phase 2 (shares `review_items.py` module)
- **US4 (Phase 5)**: Depends on Phase 1 (settings field) and Phase 3 (endpoint modification)
- **Polish (Phase 6)**: Depends on all phases complete

### User Story Dependencies

- **US2 (P1)**: Can start after Phase 1 — No dependencies on other stories
- **US1 (P1)**: Can start after Phase 1 — T007 and T008 are parallelizable with Phase 2 tasks (different files)
- **US3 (P2)**: Can start after Phase 2 — Shares `review_items.py` with US2
- **US4 (P3)**: Can start after Phase 3 — Modifies same files as US1

### Within Each User Story

- Extraction logic before sync orchestrator (US2)
- Model and SQL changes before endpoint wiring (US1)
- Delete function before hook registration (US3)
- Implementation tasks before their corresponding test tasks (tests validate implementation)

### Parallel Opportunities

Within Phase 1: T002 and T003 can run in parallel (different files)
Within Phase 3: T007 and T008 can run in parallel (different files — Pydantic model vs Frappe SQL)
Across Phases: Phase 2 (T004–T006) and Phase 3 (T007–T008) can overlap since they touch different files
Test tasks: T014/T015 after T004–T006; T016 after T007–T009; T017 after T010–T011

---

## Parallel Example: Phase 3 (US1)

```bash
# Launch model + SQL changes in parallel:
Task: "T007 [P] [US1] Update DueItem Pydantic model in fastapi_app/models/review.py"
Task: "T008 [P] [US1] Modify get_due_items() SQL with LEFT JOIN in memora_admin/api/reviews.py"

# Then sequentially:
Task: "T009 [US1] Update FastAPI endpoint in fastapi_app/api/v1/endpoints/reviews.py"
```

---

## Implementation Strategy

### MVP First (US2 Only)

1. Complete Phase 1: Setup (DocType + Settings)
2. Complete Phase 2: US2 — Auto-populate on lesson save
3. **STOP and VALIDATE**: Save a lesson, verify Review Items created in DB
4. This alone provides the data foundation — even without endpoint enrichment, the table is populated for future use

### Incremental Delivery

1. Phase 1 → DocType exists
2. Phase 2 (US2) → Data populates on save → Validate
3. Phase 3 (US1) → Students see question data in reviews → Validate
4. Phase 4 (US3) → Deletion cleanup works → Validate
5. Phase 5 (US4) → Session size configurable → Validate
6. Phase 6 → End-to-end quickstart validation

---

## Notes

- Raw SQL required for Memory State operations (RANGE-partitioned table — always include `season_seq`)
- `BIN_TO_UUID()` / `UUID_TO_BIN()` for Memory State item_id conversions
- Review Item table uses string UUIDs (Frappe `name` column) — no binary conversion needed
- LEFT JOIN ensures graceful degradation for items without Review Item records
- Existing `get_due_items()` already uses raw SQL — extend, don't replace
- The `on_update` hook fires on every lesson save — extraction must be idempotent (upsert pattern)
