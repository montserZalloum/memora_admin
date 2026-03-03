# Tasks: Practice Arena (Phase 035 — Gap Fix)

**Input**: Design documents from `/specs/035-practice-arena/`
**Prerequisites**: plan.md, spec.md, research.md, data-model.md, contracts/practice-api.md, quickstart.md

**Tests**: Included — constitution gate VIII requires test coverage for all gap-fill changes.

**Organization**: Tasks grouped by user story. US2 (Hierarchy Selection) and US4 (Access Control) are already fully implemented — no tasks needed. Only US1 and US3 have implementation gaps.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependencies)
- **[Story]**: Which user story this task belongs to (US1, US3)
- Exact file paths included in all descriptions

---

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: Add the shared Redis key builder that both US1 (dirty-set producer/consumer) and tests depend on.

- [X] T001 Add `dirty_review_items_key()` builder function to `fastapi_app/core/redis_keys.py` — returns `"memora:dirty:review_items"`, docstring documents SET type, producers (review_item_sync), consumers (sync.py), TTL: None (protected)

**Checkpoint**: Redis key builder available for import by both Frappe-side and FastAPI-side code.

---

## Phase 2: User Story 1 — Review Item Extraction (Priority: P0) MVP

**Goal**: Switch Review Item extraction from synchronous `on_lesson_save` to a dirty-set pattern with scheduled consumer and automatic retry on failure (FR-002, FR-005, FR-007).

**Independent Test**: Save a lesson, verify its name appears in `memora:dirty:review_items` Redis SET. Run sync consumer, verify items extracted and SET entry removed. Simulate failure, verify entry remains for retry.

**Gaps addressed**: G-001 (dirty-set pattern), G-004 (content hash dedup — inherits from existing `sync_review_items()`, no code change needed)

### Tests for User Story 1

> **NOTE: Write these tests FIRST, ensure they FAIL before implementation**

- [X] T002 [P] [US1] Add test verifying `on_lesson_save()` enqueues lesson name to `memora:dirty:review_items` Redis SET (and does NOT call `sync_review_items()` synchronously) in `memora_admin/memora_admin/doctype/memora_review_item/test_memora_review_item.py`
- [X] T003 [P] [US1] Add test verifying `on_lesson_save()` with `is_reviewable=0` still adds to dirty set AND performs immediate deletion of existing Review Items in `memora_admin/memora_admin/doctype/memora_review_item/test_memora_review_item.py`
- [X] T004 [P] [US1] Add tests for `sync_dirty_review_items()`: (a) processes dirty set members and SREMs on success, (b) retains entry on processing failure for retry, (c) SREMs deleted lessons (DoesNotExistError) in `memora_admin/memora_admin/doctype/memora_review_item/test_memora_review_item.py`

### Implementation for User Story 1

- [X] T005 [US1] Modify `on_lesson_save()` to replace synchronous `sync_review_items()` call with `r.sadd(dirty_review_items_key(), doc.name)` — keep immediate delete path for `is_reviewable=0` lessons in `memora_admin/events/review_item_sync.py`
- [X] T006 [US1] Modify `on_lesson_trash()` to add `r.srem(dirty_review_items_key(), doc.name)` before deletion to prevent processing a trashed lesson in `memora_admin/events/review_item_sync.py`
- [X] T007 [US1] Add `sync_dirty_review_items()` consumer function following `sync_dirty_progress()` pattern — SMEMBERS, process each via existing `sync_review_items()`, SREM on success, leave on failure, handle DoesNotExistError, commit at end — in `memora_admin/tasks/sync.py`
- [X] T008 [US1] Add `*/2 * * * *` (every 2 minutes) scheduler entry for `memora_admin.tasks.sync.sync_dirty_review_items` in `memora_admin/hooks.py`

**Checkpoint**: Lesson saves enqueue to dirty set. Scheduled job processes queue every 2 minutes with retry semantics. Content hash dedup inherited from `sync_review_items()`.

---

## Phase 3: User Story 3 — Practice Session Flow (Priority: P1)

**Goal**: Distribute questions proportionally across topics by content volume (FR-014) and fix `all_seen_warning` to trigger when ANY question is a repeat, not just when all items exhausted (FR-016).

**Independent Test**: Start a session with 3 topics (100, 50, 10 items). Verify batch contains ~12, ~6, ~2 items respectively. Start a session where 1 of 20 questions is a repeat — verify `all_seen_warning=true`. Start a session with all-new questions — verify `all_seen_warning=false`.

**Gaps addressed**: G-002 (proportional distribution), G-003 (`all_seen_warning` semantics), G-005 (consistent detection on start + continue)

### Tests for User Story 3

> **NOTE: Write these tests FIRST, ensure they FAIL before implementation**

- [X] T009 [P] [US3] Add test class `TestProportionalDistribution` verifying: (a) multi-topic batch distributes proportionally by item count, (b) single-topic bypasses proportional logic, (c) topics with fewer items than quota get capped, (d) remainder distributed to largest topics — in `fastapi_app/tests/test_practice.py`
- [X] T010 [P] [US3] Add test class `TestAllSeenWarning` verifying: (a) batch with all-new questions returns `all_seen_warning=false`, (b) batch with ANY repeat returns `all_seen_warning=true`, (c) wrap-around (all items exhausted) returns `all_seen_warning=true`, (d) `continue_session()` uses same semantics as `start_session()` — in `fastapi_app/tests/test_practice.py`

### Implementation for User Story 3

- [X] T011 [P] [US3] Implement `_count_items_per_topic()` async method returning `dict[str, int]` of topic_id → available item count via SQL COUNT grouped by topic — in `fastapi_app/services/practice.py`
- [X] T012 [P] [US3] Implement `_compute_topic_quotas()` pure function distributing `batch_size` across topics proportionally (min 1 each, remainder to largest) — in `fastapi_app/services/practice.py`
- [X] T013 [US3] Refactor `_select_questions()` to: (a) call `_count_items_per_topic()` + `_compute_topic_quotas()` for per-topic limits, (b) query per topic with priority ordering, (c) detect `any_repeat` from SQL priority column (priority > 0), (d) return `(questions, total_available, any_repeat)` instead of current `(questions, total_available)` — in `fastapi_app/services/practice.py`
- [X] T014 [US3] Update `start_session()` to unpack 3-tuple from `_select_questions()` and set `all_seen_warning = any_repeat` instead of `total_available > 0 and len(questions) == 0` — in `fastapi_app/services/practice.py`
- [X] T015 [US3] Update `continue_session()` to unpack 3-tuple from `_select_questions()`, set `all_seen_warning = any_repeat`, and force `all_seen_warning = True` on wrap-around (when clearing `served_item_ids`) — in `fastapi_app/services/practice.py`

**Checkpoint**: Questions distributed proportionally across topics. `all_seen_warning` fires on any repeat, not just total exhaustion. Both `start_session()` and `continue_session()` use consistent detection.

---

## Phase 4: Polish & Cross-Cutting Concerns

**Purpose**: Validation, verification, and cleanup across both user stories.

- [X] T016 Restart FastAPI server (`pkill -f "uvicorn fastapi_app.main:app"`) and verify health check at `http://127.0.0.1:8002/api/v1/health/live`
- [X] T017 Run full practice test suite: `python -m pytest fastapi_app/tests/test_practice.py -v` and `bench --site x.conanacademy.com run-tests --app memora_admin --module "memora_admin.doctype.memora_review_item"`
- [X] T018 Run quickstart.md verification steps from `specs/035-practice-arena/quickstart.md` — verify dirty set population, consumer processing, and API response `all_seen_warning` field

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: No dependencies — start immediately
- **US1 (Phase 2)**: Depends on Phase 1 (T001 provides Redis key builder)
- **US3 (Phase 3)**: Depends on Phase 1 only — **independent of US1** (different files, no shared logic)
- **Polish (Phase 4)**: Depends on Phase 2 + Phase 3 completion

### User Story Dependencies

- **US1 (P0)**: Can start after Setup. Modifies Frappe-side files only (`events/`, `tasks/`, `hooks.py`). No dependency on US3.
- **US3 (P1)**: Can start after Setup. Modifies FastAPI-side files only (`services/practice.py`, `tests/test_practice.py`). No dependency on US1.
- **US2 & US4**: Already fully implemented — zero tasks.

### Within Each User Story

- Tests written FIRST (should FAIL before implementation)
- Implementation tasks follow dependency order:
  - US1: Producer (T005–T006) → Consumer (T007) → Scheduler (T008)
  - US3: Helpers (T011–T012) → Core refactor (T013) → Callers (T014–T015)

### Parallel Opportunities

**Between stories** (after Phase 1):
- US1 and US3 can execute **fully in parallel** — they touch completely different files

**Within US1**:
- T002, T003, T004 can run in parallel (all tests, same file but independent test cases)
- T005 + T006 are sequential (same file, related functions)
- T007 depends on T005–T006 pattern (uses same key builder)

**Within US3**:
- T009, T010 can run in parallel (independent test classes)
- T011, T012 can run in parallel (independent helper functions)
- T013 depends on T011 + T012 (uses both helpers)
- T014, T015 depend on T013 (use new return signature)

---

## Parallel Example: US1 + US3 Concurrent

```bash
# After T001 (Setup) completes:

# Stream A: US1 (Frappe-side)
Task T002: Test dirty-set producer
Task T003: Test non-reviewable immediate delete
Task T004: Test dirty-set consumer
Task T005: Modify on_lesson_save()
Task T006: Modify on_lesson_trash()
Task T007: Add sync_dirty_review_items()
Task T008: Add scheduler entry

# Stream B: US3 (FastAPI-side) — runs concurrently with Stream A
Task T009: Test proportional distribution
Task T010: Test all_seen_warning semantics
Task T011: Implement _count_items_per_topic()
Task T012: Implement _compute_topic_quotas()
Task T013: Refactor _select_questions()
Task T014: Update start_session()
Task T015: Update continue_session()
```

---

## Implementation Strategy

### MVP First (US1 Only)

1. Complete Phase 1: Setup (T001)
2. Complete Phase 2: US1 — Review Item Extraction (T002–T008)
3. **STOP and VALIDATE**: Verify dirty set populates on lesson save, consumer processes and clears, retry works
4. This alone satisfies FR-002, FR-005, FR-007

### Incremental Delivery

1. Setup → US1 (dirty-set extraction) → Validate (MVP)
2. Add US3 (proportional distribution + all_seen_warning) → Validate
3. Polish → Full test suite green → Deploy

### Full Parallel (Fastest)

1. Complete Setup (T001)
2. US1 + US3 in parallel (different files, zero overlap)
3. Polish + full validation
4. Estimated: ~2 hours total

---

## Notes

- All changes are **modifications** to existing files — no new files created
- G-004 (content hash dedup) requires zero code changes — existing `sync_review_items()` hash check inherits automatically when called by dirty-set consumer
- US2 (Hierarchy Selection) and US4 (Access Control) are fully implemented from Phase 025 — verified in research.md
- 20 FRs verified as already implemented; only FR-002, FR-005, FR-007, FR-014, FR-016 have gaps
- Performance impact of proportional distribution: ~2ms additional COUNT query, well within 100ms target (SC-003)
