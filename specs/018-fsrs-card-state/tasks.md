# Tasks: FSRS Card State Persistence

**Input**: Design documents from `/specs/018-fsrs-card-state/`
**Prerequisites**: plan.md, spec.md, research.md, data-model.md, contracts/

**Tests**: Not explicitly requested in spec. Test tasks omitted.

**Organization**: All 4 user stories are satisfied by the same set of code changes (3 new columns + updated reconstruction/persistence). Tasks are organized by code path (the natural boundary), with story labels showing which stories each task delivers.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependencies)
- **[Story]**: Which user story this task belongs to (US1=intervals grow, US2=difficulty adjusts, US3=mastery classification, US4=backward compat)
- Exact file paths included in all descriptions

## Story-to-Code Mapping

| Story | Code Changes Required | Notes |
|-------|----------------------|-------|
| US1 (P1): Intervals grow | fsrs_processor.py + reviews.py (reconstruct + persist state/step/last_review) | Core fix |
| US2 (P2): Difficulty adjusts | Same as US1 (different rating input, same code path) | No separate code |
| US3 (P2): Mastery classification | No code changes | Natural outcome of correct stability values (research R5) |
| US4 (P1): Backward compat | fsrs_processor.py + reviews.py (NULL handling in reconstruction) | Included in US1 fix |

---

## Phase 1: Setup (Schema Migration)

**Purpose**: Add the 3 missing columns to the partitioned `tabMemora Memory State` table via the existing `setup.py` migration pattern.

- [X] T001 [US4] Add `_ensure_fsrs_state_columns()` function to `memora_admin/setup.py` that checks `INFORMATION_SCHEMA.COLUMNS` for existence of `state`, `step`, `last_review` columns on `tabMemora Memory State`, and adds each missing column via `frappe.db.sql_ddl()` — `state` TINYINT DEFAULT NULL, `step` TINYINT DEFAULT NULL, `last_review` DATETIME(6) DEFAULT NULL. Follow the `_ensure_item_id_binary_column()` idempotent pattern. Reference: `contracts/memory-state-sql.md` DDL section.
- [X] T002 [US4] Register `_ensure_fsrs_state_columns()` call in the `after_migrate()` function of `memora_admin/setup.py`, after existing column-ensuring calls.

**Checkpoint**: Run `bench migrate` and verify columns exist via `SELECT COLUMN_NAME FROM INFORMATION_SCHEMA.COLUMNS WHERE TABLE_NAME = 'tabMemora Memory State' AND COLUMN_NAME IN ('state','step','last_review')`.

---

## Phase 2: Foundational (DocType JSON)

**Purpose**: Add virtual field definitions so the new columns are visible in the Frappe admin panel without triggering schema drift guards.

- [X] T003 [P] [US4] Add three `is_virtual: 1` field definitions (`state` as Int, `step` as Int, `last_review` as Datetime) to `memora_admin/doctype/memora_memory_state/memora_memory_state.json`. These are display-only fields — `is_virtual=1` prevents Frappe from managing the DB column and bypasses `_verify_no_schema_drift()`.

**Checkpoint**: `bench migrate` completes without schema drift errors. Fields appear in Memory State admin form as read-only.

---

## Phase 3: US1+US4 — Background Processor (`fsrs_processor.py`)

**Goal**: Fix the FSRS background processor to reconstruct and persist full card state (all 6 fields), enabling intervals to grow with mastery. Existing records with NULL new fields are handled gracefully.

**Independent Test**: Process a student's interaction through the background processor 3+ times with correct answers. Verify `state` progresses Learning→Review and `next_review` grows beyond tomorrow.

### Implementation

- [X] T004 [US1] [US4] Update `_lookup_memory_state()` SELECT query in `memora_admin/tasks/fsrs_processor.py` to include `state, step, last_review` columns. Reference: `contracts/memory-state-sql.md` Lookup section.
- [X] T005 [US1] [US4] Update card reconstruction logic in `memora_admin/tasks/fsrs_processor.py` to restore `state`, `step`, and `last_review` from DB row onto the `fsrs.Card` object. NULL `state` → leave as Card() default (Learning). NULL `step` → leave as 0. NULL `last_review` → leave as None. Non-NULL `last_review` → add `tzinfo=timezone.utc`. Non-NULL `state` → `State(int(value))`. Reference: `contracts/card-reconstruction.md` Reconstruction section.
- [X] T006 [US1] Update card persistence in `_update_memory_state()` of `memora_admin/tasks/fsrs_processor.py` to write `state` (as `card.state.value`), `step` (as `card.step`, may be None), and `last_review` (as `card.last_review.replace(tzinfo=None)` if not None, else None) alongside existing stability/difficulty/next_review. Update SQL UPDATE statement per `contracts/memory-state-sql.md` Update section.
- [X] T007 [US1] Update card persistence in `_insert_memory_state()` of `memora_admin/tasks/fsrs_processor.py` to include `state`, `step`, and `last_review` in the INSERT statement and parameter dict. Reference: `contracts/memory-state-sql.md` Insert section.
- [X] T008 [US1] Update Redis cache write in `memora_admin/tasks/fsrs_processor.py` to include `state` (int), `step` (int or None), and `last_review` (ISO 8601 string or None) in the JSON payload written to `memora:fsrs:{player}:{item_id}`. Reference: `contracts/card-reconstruction.md` Redis Cache section.

**Checkpoint**: Process an interaction via the background processor. Query `tabMemora Memory State` and verify `state`, `step`, `last_review` are populated (not NULL) for the processed record.

---

## Phase 4: US1+US2 — Submit Reviews API (`reviews.py`)

**Goal**: Apply the same card reconstruction and persistence fix to the `submit_reviews()` endpoint, which is the synchronous review path (vs. the background processor). This ensures both review paths produce correct FSRS state and intervals.

**Independent Test**: Call `submit_reviews` API with a correct answer for a due item. Verify the Memory State record has `state`, `step`, `last_review` populated and `next_review` reflects proper FSRS output.

### Implementation

- [X] T009 [US1] [US4] Update the inline SELECT query in `submit_reviews()` of `memora_admin/api/reviews.py` to include `state, step, last_review` columns. Reference: `contracts/memory-state-sql.md` Lookup section.
- [X] T010 [US1] [US4] Update card reconstruction logic in `submit_reviews()` of `memora_admin/api/reviews.py` to restore `state`, `step`, and `last_review` from DB row onto the `fsrs.Card` object. Same NULL-handling rules as T005. Reference: `contracts/card-reconstruction.md` Reconstruction section.
- [X] T011 [US1] [US2] Update card persistence (inline UPDATE) in `submit_reviews()` of `memora_admin/api/reviews.py` to write `state`, `step`, and `last_review` alongside existing fields. Same extraction logic as T006. Reference: `contracts/memory-state-sql.md` Update section.

**Checkpoint**: Call `submit_reviews` API endpoint. Query the updated Memory State record and verify all 6 FSRS fields are persisted.

---

## Phase 5: Polish & Verification

**Purpose**: End-to-end validation and deployment prep.

- [X] T012 Verify that `_verify_no_schema_drift()` in `memora_admin/setup.py` does NOT flag the new columns (they must be `is_virtual=1` in JSON). Run `bench migrate` and confirm no drift errors.
- [X] T013 Run quickstart.md verification queries against the live database: confirm new columns exist, confirm a processed record has non-NULL state/step/last_review, confirm review state cards have future `next_review` dates.
- [X] T014 Restart Frappe workers (`bench restart`) and FastAPI server (`pkill -f "uvicorn fastapi_app.main:app"`) to activate all changes. Verify health check passes.

---

## Dependencies & Execution Order

### Phase Dependencies

- **Phase 1 (Setup)**: No dependencies — start immediately
- **Phase 2 (Foundational)**: No dependencies — can run in parallel with Phase 1
- **Phase 3 (Processor)**: Depends on Phase 1 (columns must exist for SQL to work)
- **Phase 4 (Reviews API)**: Depends on Phase 1. Can run in parallel with Phase 3 (different file)
- **Phase 5 (Polish)**: Depends on all previous phases

### User Story Dependencies

- **US1 (Intervals grow)**: Delivered by T004-T011 (Phases 3+4)
- **US2 (Difficulty adjusts)**: Delivered by T011 (same persistence, different rating input)
- **US3 (Mastery classification)**: No code changes — naturally correct once stability values are accurate
- **US4 (Backward compat)**: Delivered by T001-T003 (schema + JSON) + T004-T005, T009-T010 (NULL handling)

### Within Each Phase

- Phase 1: T001 → T002 (function must exist before registration)
- Phase 3: T004 → T005 → T006, T007, T008 (lookup before reconstruct before persist)
- Phase 4: T009 → T010 → T011 (same sequence)

### Parallel Opportunities

```
Phase 1 (T001→T002) ──────► Phase 3 (T004→T005→T006,T007,T008)
                      ├───► Phase 4 (T009→T010→T011)   [parallel with Phase 3]
Phase 2 (T003)  ──────┘
```

- **T003** can run in parallel with T001-T002 (different file: JSON vs setup.py)
- **Phase 3 and Phase 4** can run in parallel (different files: fsrs_processor.py vs reviews.py)
- **T006, T007, T008** can run in parallel within Phase 3 (update, insert, and cache are independent functions)

---

## Implementation Strategy

### MVP First (Phase 1 + Phase 3)

1. Complete Phase 1: Add columns to DB
2. Complete Phase 3: Fix background processor
3. **STOP and VALIDATE**: Process interactions and verify intervals grow
4. This alone fixes the core bug for the most common review path

### Full Delivery

1. Phase 1 + Phase 2 (parallel) → Schema ready
2. Phase 3 + Phase 4 (parallel) → Both review paths fixed
3. Phase 5 → Verified and deployed
4. All 4 user stories satisfied with 14 focused tasks across 4 production files (~100 lines changed)

---

## Notes

- All SQL MUST include `season_seq` for partition pruning
- All `item_id` operations MUST use `UUID_TO_BIN()`/`BIN_TO_UUID()`
- `last_review` written to MariaDB as naive datetime (strip tzinfo), read back with UTC timezone added
- `state` persisted as integer (1=Learning, 2=Review, 3=Relearning), NOT 0=New (FSRS v6 has no New state)
- No data migration for existing records — self-correcting over 1-2 review cycles
- Redis cache entries (24h TTL) naturally expire — no cache flush needed
