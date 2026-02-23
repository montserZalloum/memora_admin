# Tasks: Practice Arena (ساحة التدريب)

**Input**: Design documents from `/specs/025-practice-arena/`
**Prerequisites**: plan.md, spec.md, research.md, data-model.md, contracts/practice-api.md, quickstart.md

**Tests**: Constitution Principle VIII mandates test-first coverage. Test tasks included per phase.

**Organization**: Tasks grouped by user story to enable independent implementation and testing.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependencies)
- **[Story]**: Which user story this task belongs to (e.g., US1, US2, US3, US4)
- Exact file paths included in descriptions

---

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: Database migration, settings schema, Redis keys, config, and routing scaffolding that ALL user stories depend on.

- [X] T001 Create `tabMemora Practice Log` raw SQL table via migration function `_ensure_practice_log_table()` in `memora_admin/memora_admin/setup.py` — DDL from data-model.md (BIGINT PK, `uq_player_item` unique, `idx_item_id` index); call from `setup_memora()` or `after_install`
- [X] T002 Add `practice_session_key(player_id)` builder function to `fastapi_app/core/redis_keys.py` — returns `memora:practice:{player_id}`, docstring per contracts/practice-api.md (HASH type, producers/consumers, TTL)
- [X] T003 [P] Add practice rate limit settings to `fastapi_app/core/config.py` — 4 fields: `practice_hierarchy_rate_limit: int = 30`, `practice_start_rate_limit: int = 10`, `practice_submit_rate_limit: int = 30`, `practice_continue_rate_limit: int = 30`
- [X] T004 [P] Add practice session settings to `fastapi_app/core/config.py` — 2 fields: `practice_session_size: int = 20`, `practice_session_ttl: int = 3600`
- [X] T005 [P] Add `practice_session_size` (Int, default 20) and `practice_session_ttl` (Int, default 3600) fields to `memora_admin/memora_admin/doctype/memora_settings/memora_settings.json` — new "Practice Arena" section, following existing field pattern
- [X] T006 [P] Add 4 rate limit scopes to `_SCOPE_SETTINGS` dict in `fastapi_app/api/deps.py` — `practice_hierarchy`, `practice_start`, `practice_submit`, `practice_continue` mapped to config field names
- [X] T007 Create Pydantic request/response models in `fastapi_app/models/practice.py` — all 11 models from contracts/practice-api.md: `PracticeHierarchyParams`, `StartPracticeRequest`, `PracticeResult`, `SubmitPracticeRequest`, `PracticeTopicInfo`, `PracticeUnitInfo`, `PracticeTrackInfo`, `PracticeHierarchyResponse`, `PracticeQuestion`, `PracticeBatchResponse`, `PracticeSubmitResponse`
- [X] T008 Create empty practice endpoint router file `fastapi_app/api/v1/endpoints/practice.py` with `router = APIRouter()` and register it in `fastapi_app/api/v1/router.py` with prefix `/practice` and tag `practice`

**Checkpoint**: Infrastructure ready — all foundational pieces (DB table, Redis key, config, models, routing) in place. User story implementation can begin.

---

## Phase 2: User Story 1 — Review Item Gap-Filling (Priority: P0) 🎯 MVP

**Goal**: Close extraction gaps from phase 024: `is_reviewable` filtering, content_hash debounce, MINDMAP recursive extraction, and Practice Log cascade deletion. After this phase, the Review Item table is fully reliable.

**Independent Test**: Save/modify lessons in admin panel → verify Review Item table correctness, debounce behavior, cascade deletion, and `is_reviewable` filtering.

### Implementation for User Story 1

- [X] T009 [US1] Add `is_reviewable` check at top of `sync_review_items()` in `memora_admin/api/review_items.py` — if `lesson_doc.is_reviewable == 0`, delete all existing Review Items for that lesson (call `_delete_review_items_and_memory_state()`) and return early with `{"created": 0, "updated": 0, "deleted": count}`
- [X] T010 [US1] Add content_hash debounce to `sync_review_items()` in `memora_admin/api/review_items.py` — compute `_compute_lesson_content_hash(stages)` using MD5 of sorted stage names + types + is_skippable + config_json (per research.md R-004), compare with `lesson_doc.content_hash`, skip if unchanged, update field after successful extraction
- [X] T011 [US1] Add `_extract_mindmap()` extractor function in `memora_admin/api/review_items.py` — recursive traversal of `children[]` nodes extracting `item_id` at each level (per research.md R-007), register in stage type dispatch
- [X] T012 [US1] Add Practice Log cascade deletion to `_delete_review_items_and_memory_state()` in `memora_admin/api/review_items.py` — before deleting Review Items, execute `DELETE FROM tabMemora Practice Log WHERE item_id IN (...)` for the item_ids being removed (per research.md R-005)
- [X] T013 [US1] Add `is_reviewable` guard to `on_lesson_save()` hook in `memora_admin/events/review_item_sync.py` — if lesson has `is_reviewable=0`, trigger deletion of existing Review Items for that lesson instead of extraction

**Checkpoint**: Review Item extraction is fully reliable — `is_reviewable` respected, saves debounced, MINDMAP extracted recursively, cascade deletion covers Practice Log. US1 acceptance scenarios 1-8 verifiable.

---

## Phase 3: User Story 2 — Hierarchy Selection (Priority: P1)

**Goal**: Students can browse subject → track → unit → topic hierarchy with access flags and item counts via `GET /practice/hierarchy`.

**Independent Test**: Call hierarchy endpoint with various subjects and filters → verify correct tree structure, access flags, item counts, and completed-only filtering.

### Implementation for User Story 2

- [X] T014 [US2] Add `PracticeService` class skeleton in `fastapi_app/services/practice.py` — constructor takes `redis: Redis`, `frappe_client: FrappeClient | None`, `config: Settings`; add `get_practice_hierarchy()` method stub
- [X] T015 [US2] Implement `get_practice_hierarchy(player_id, subject_id, filter)` in `fastapi_app/services/practice.py` — load hierarchy from cache (`hierarchy_key(subject_id)`), query Review Item counts per track/unit/topic via raw SQL (`frappe.db.sql` through `FrappeClient` only on cache miss, following `ensure_hydrated()` pattern — NOT in the hot path per Constitution II), check access per track via `AccessService` pattern, return `PracticeHierarchyResponse`. Item counts SHOULD be cached alongside hierarchy data with 1h TTL to avoid SQL on every request.
- [X] T016 [US2] Implement "completed" filter logic in `get_practice_hierarchy()` in `fastapi_app/services/practice.py` — when `filter="completed"`, decode player's progress bitmap via `ProgressService.get_completed_bits()`, map bit_indices to lesson IDs via hierarchy, filter to only tracks/units/topics with completed lessons (per research.md R-009)
- [X] T017 [US2] Add `get_practice_service` dependency in `fastapi_app/api/deps.py` — inject Redis, FrappeClient (optional), and Settings into PracticeService constructor
- [X] T018 [US2] Implement `GET /practice/hierarchy` endpoint in `fastapi_app/api/v1/endpoints/practice.py` — query params `subject_id` (required) + `filter` (default "all"), rate limit scope `practice_hierarchy`, return `PracticeHierarchyResponse`, handle 404 `SUBJECT_NOT_FOUND`

**Checkpoint**: Hierarchy browsing works end-to-end — students see tracks/units/topics with correct access flags and item counts. Completed-only filter hides empty branches.

---

## Phase 4: User Story 3 + 4 — Practice Session Flow & Access Control (Priority: P1)

**Goal**: Students can start practice sessions, receive batches of questions, submit results, and continue with more batches. Full session lifecycle with Redis session management, Practice Log persistence, and access control enforcement at session start (US4 merged here — access control is inseparable from session start per FR-008/FR-009).

**Independent Test**: Start session → receive questions → submit results → continue → verify Practice Log entries, session state, idempotency, access enforcement, and free content bypass.

### Implementation for User Story 3 + 4

- [X] T019 [US3+US4] Implement `_get_accessible_lessons()` helper in `fastapi_app/services/practice.py` — given player_id, subject_id, tracks, units, topics, filter: for each selected track, check access via `AccessService.check_access_with_plan()` (per research.md R-010). For fully accessible tracks, include all lessons. For inaccessible tracks, still include lessons from free topics/units within that track (using hierarchy's `is_lesson_free()` check, consistent with existing access model per FR-008). If an inaccessible track has zero free content, add it to a `denied_tracks` list. Apply completed-only filter if needed. Return tuple of (lesson_ids, denied_tracks).
- [X] T020 [US3] Implement `_select_questions()` method in `fastapi_app/services/practice.py` — SQL JOIN of `tabMemora Review Item` LEFT JOIN `tabMemora Practice Log` with 3-tier priority (0=unseen, 1=seen-before, 2=seen-this-session), proportional distribution across topics (compute per-topic quota as `round(topic_item_count / total_item_count * batch_size)`, ensure each topic gets at least 1 question if available, round-robin distribute remainder), LIMIT by `practice_session_size` (per research.md R-002 query pattern)
- [X] T021 [US3+US4] Implement `start_session()` method in `fastapi_app/services/practice.py` — validate filters, call `_get_accessible_lessons()` to resolve lessons and denied tracks. If `denied_tracks` is non-empty, raise 403 `NO_ACCESS` with the denied track list. Delete any existing session for player (`practice_session_key`), create Redis HASH with all session fields (per data-model.md session schema) including `accessible_lessons` JSON array (subsequent batches reuse this stored list without re-checking access per FR-009), set TTL from config, call `_select_questions()` for first batch, return `PracticeBatchResponse`
- [X] T022 [US3] Implement `continue_session()` method in `fastapi_app/services/practice.py` — load session from Redis HASH, verify previous batch was submitted (check if HGET `submitted_{batch_seq-1}` field exists in the session hash, or batch_seq is 0 for the first batch), increment `batch_seq`, call `_select_questions()` with updated `served_item_ids` dedup list, reset EXPIRE, return `PracticeBatchResponse` with `all_seen_warning` if all items exhausted. Note: `submitted_{N}` fields are dynamic keys in the Redis HASH set by `submit_batch()` (T023)
- [X] T023 [US3] Implement `submit_batch()` method in `fastapi_app/services/practice.py` — load session, check `batch_seq` matches (409 `BATCH_SEQ_MISMATCH` if skipped), detect duplicate via marker field `submitted_{batch_seq}`, execute Practice Log UPSERT for each result (per data-model.md UPSERT pattern), set submitted marker in session hash, return `PracticeSubmitResponse` with accuracy stats
- [X] T024 [US3] Implement `POST /practice/start` endpoint in `fastapi_app/api/v1/endpoints/practice.py` — request body `StartPracticeRequest`, rate limit scope `practice_start`, validate `tracks` non-empty + multi-track constraints (units/topics empty if >1 track), call `PracticeService.start_session()`, handle 403 `NO_ACCESS` and 422 `NO_ITEMS`
- [X] T025 [US3] Implement `POST /practice/submit` endpoint in `fastapi_app/api/v1/endpoints/practice.py` — request body `SubmitPracticeRequest`, rate limit scope `practice_submit`, call `PracticeService.submit_batch()`, handle 404 `NO_ACTIVE_SESSION` and 409 `BATCH_SEQ_MISMATCH`
- [X] T026 [US3] Implement `POST /practice/continue` endpoint in `fastapi_app/api/v1/endpoints/practice.py` — empty request body, rate limit scope `practice_continue`, call `PracticeService.continue_session()`, handle 404 `NO_ACTIVE_SESSION` and 422 `PREVIOUS_BATCH_NOT_SUBMITTED`

**Checkpoint**: Full practice session lifecycle works — start → questions → submit → continue → more questions. Practice Log persists results. Idempotent submissions. TTL auto-expiry for abandoned sessions. Access control enforced at session start only (US4). Paid tracks with zero free content rejected. Free content accessible regardless. No re-check on continue/submit.

---

## Phase 5: Polish & Cross-Cutting Concerns

**Purpose**: Logging, error handling refinements, and validation across all user stories.

- [X] T027 [P] Add structured logging to `PracticeService` methods in `fastapi_app/services/practice.py` — use `structlog` for session_start, question_selection, batch_submit, session_expired events with player_id, subject_id, batch_seq, item_count context
- [X] T028 [P] Handle edge case: item deleted during active session in `submit_batch()` in `fastapi_app/services/practice.py` — when Practice Log UPSERT references a deleted Review Item, silently skip that item (per spec edge case), log warning, include only valid items in response counts
- [X] T029 Restart FastAPI server and run quickstart.md validation — kill uvicorn (`pkill -f "uvicorn fastapi_app.main:app"`), wait for supervisor restart, verify health check, test all 4 endpoints per quickstart.md curl examples
- [X] T030 Verify Practice Log table exists and UPSERT works — run DDL migration if not done, insert test row, verify ON DUPLICATE KEY UPDATE behavior, check indexes with `SHOW INDEX FROM tabMemora Practice Log`

---

## Phase 6: Tests — Review Item Gap-Filling (Constitution VIII)

**Purpose**: Test-first coverage for Phase 2 (US1) gap-filling changes. Uses `FrappeTestCase` for Frappe-side tests.

- [X] T031 [US1] Test `is_reviewable` filtering in `memora_admin/memora_admin/doctype/memora_review_item/test_memora_review_item.py` — save lesson with `is_reviewable=0`, verify zero Review Items created; save with `is_reviewable=1`, verify items created; toggle to 0, verify items deleted
- [X] T032 [US1] Test content_hash debounce in `memora_admin/memora_admin/doctype/memora_review_item/test_memora_review_item.py` — save lesson twice with same content, verify `sync_review_items()` skips second run (returns `created: 0`); modify content, verify re-extraction runs
- [X] T033 [US1] Test MINDMAP recursive extraction in `memora_admin/memora_admin/doctype/memora_review_item/test_memora_review_item.py` — create MINDMAP stage with nested `children[]`, verify all leaf node `item_id`s are extracted into Review Items
- [X] T034 [US1] Test Practice Log cascade deletion in `memora_admin/memora_admin/doctype/memora_review_item/test_memora_review_item.py` — insert Practice Log rows for a Review Item, delete the Review Item, verify Practice Log rows are also deleted

**Checkpoint**: All US1 gap-filling behaviors verified: is_reviewable, debounce, MINDMAP extraction, cascade deletion.

---

## Phase 7: Tests — FastAPI Practice Endpoints (Constitution VIII)

**Purpose**: Test-first coverage for Phases 3-4 (US2+US3+US4). Uses pytest + httpx + real Redis. File: `fastapi_app/tests/test_practice.py`.

- [X] T035 [US2] Test `GET /practice/hierarchy` — verify correct tree structure with item counts, access flags, and 404 for invalid subject. Test `filter=completed` returns only nodes with completed lessons.
- [X] T036 [US3] Test `POST /practice/start` — verify session creation, first batch returned with correct question count, Redis session hash populated with all fields (including `accessible_lessons`), proper 422 `NO_ITEMS` when filters match nothing
- [X] T037 [US3] Test `POST /practice/submit` — verify Practice Log UPSERT (first attempt creates, second updates `attempt_count`), idempotency via duplicate `batch_seq` (returns `is_duplicate: true`, no double-counting), 409 `BATCH_SEQ_MISMATCH` for skipped seq
- [X] T038 [US3] Test `POST /practice/continue` — verify next batch served with dedup (no repeated items from current session), 422 `PREVIOUS_BATCH_NOT_SUBMITTED` when prior batch unsubmitted, `all_seen_warning` when all items exhausted
- [X] T039 [US4] Test access control enforcement — verify 403 `NO_ACCESS` for paid track without subscription, verify free content bypass (inaccessible track with free topics still serves free items), verify no re-check on `/continue` after session start
- [X] T040 Test session TTL expiry — create session, simulate expiry (DEL the key), verify 404 `NO_ACTIVE_SESSION` on subsequent continue/submit
- [X] T041 Test edge case: item deleted during active session — start session, delete a served Review Item, submit results referencing deleted item, verify silently skipped with warning log

**Checkpoint**: All 4 endpoints tested end-to-end. Access control, idempotency, session lifecycle, and edge cases verified.

---

## Phase 8: Validation — Success Criteria (SC-001..SC-006)

**Purpose**: Verify measurable success criteria from spec.

- [X] T042 Validate SC-003: question selection returns results in under 100ms for a player with 5,000+ Practice Log entries — insert 5K practice log rows, run `_select_questions()`, measure wall time
- [X] T043 Validate SC-005 + SC-006: duplicate batch submission does not corrupt Practice Log (covered by T037), and access control rejects unauthorized content (covered by T039) — cross-reference test results, document in quickstart.md validation section

**Checkpoint**: Key success criteria validated. SC-001 (P95 <2s) and SC-002 (<30s sync) and SC-004 (100K concurrency) are load-test targets verified in production, not in unit tests.

---

## Dependencies & Execution Order

### Phase Dependencies

- **Phase 1 (Setup)**: No dependencies — start immediately
- **Phase 2 (US1 — Gap-Filling)**: Depends on T001 (Practice Log table for cascade deletion). Can start T009-T011 in parallel with T001 since they don't touch Practice Log.
- **Phase 3 (US2 — Hierarchy)**: Depends on Phase 1 completion (T002, T003-T008 for redis key, config, models, routing)
- **Phase 4 (US3+US4 — Session Flow + Access Control)**: Depends on Phase 1 + Phase 3 (needs hierarchy service, models, redis key, Practice Log table). US4 access control is integrated into `start_session()` and `_get_accessible_lessons()`.
- **Phase 5 (Polish)**: Depends on Phases 2-4 being complete
- **Phase 6 (US1 Tests)**: Depends on Phase 2 being complete (tests verify gap-filling code)
- **Phase 7 (FastAPI Tests)**: Depends on Phases 3-5 being complete (tests verify all endpoints)
- **Phase 8 (Validation)**: Depends on Phase 7 (cross-references test results)

### User Story Dependencies

- **US1 (P0 — Gap-Filling)**: Independent — only touches Frappe-side extraction code + Practice Log DDL
- **US2 (P1 — Hierarchy)**: Independent after Phase 1 — read-only endpoint, no session state
- **US3+US4 (P1 — Session Flow + Access Control)**: Depends on US2 (reuses hierarchy data for lesson resolution). US4 access control is integrated into `_get_accessible_lessons()` and `start_session()` — not independently separable.

### Within Each User Story

- Models (T007) before services (T014, T019)
- Services before endpoints (T018, T024-T026)
- Core logic before edge case handling (T028)
- Implementation before tests (Phases 2-5 before Phases 6-7)

### Parallel Opportunities

**Phase 1** (all [P] tasks can run in parallel):
```
T003 (config rate limits) || T004 (config session settings) || T005 (settings DocType) || T006 (deps scopes)
```

**Phase 2** (US1 tasks are mostly sequential due to same file):
```
T009 → T010 → T011 → T012 (all in review_items.py, sequential)
T013 (review_item_sync.py, parallel with T009-T012)
```

**Phase 3 + Phase 2** (US2 can start once Phase 1 is done, parallel with US1):
```
US1 (T009-T013) || US2 (T014-T018)
```

**Phase 4** (sequential within, but parallel endpoint work):
```
T019 → T020 → T021 → T022 → T023 (service methods, sequential)
T024 || T025 || T026 (endpoints can be written in parallel after services)
```

**Phase 6 + Phase 7** (test phases can overlap — different test frameworks/files):
```
Phase 6 (T031-T034, FrappeTestCase) || Phase 7 (T035-T041, pytest+httpx)
```

---

## Implementation Strategy

### MVP First (User Story 1 + 2 + 3 + 4)

1. **Phase 1**: Setup all infrastructure (T001-T008)
2. **Phase 2**: US1 gap-filling (T009-T013) — ensures Review Item data is reliable
3. **Phase 3**: US2 hierarchy endpoint (T014-T018) — students can browse
4. **Phase 4**: US3+US4 session flow + access control (T019-T026) — core practice functionality
5. **Phase 5**: Polish (T027-T030)
6. **Phases 6-8**: Tests and validation (T031-T043)

### Incremental Delivery

1. Phase 1 (Setup) → infrastructure ready
2. Phase 2 (US1) → Review Item extraction reliable (admin can verify)
3. Phase 3 (US2) → Hierarchy browsable (frontend can integrate)
4. Phase 4 (US3+US4) → Full practice sessions with access control
5. Phase 5 (Polish) → Logging, edge cases, final validation
6. Phases 6-7 (Tests) → Constitution VIII compliance
7. Phase 8 (Validation) → Success criteria verified

### Recommended Build Order (Single Developer)

```
T001 → T002 → T003+T004+T005+T006 (parallel) → T007 → T008
→ T009 → T010 → T011 → T012 → T013
→ T014 → T015 → T016 → T017 → T018
→ T019 → T020 → T021 → T022 → T023 → T024+T025+T026 (parallel)
→ T027+T028 (parallel) → T029 → T030
→ T031+T032+T033+T034 (parallel, FrappeTestCase) || T035+T036+T037+T038+T039+T040+T041 (sequential, pytest)
→ T042 → T043
```

---

## Notes

- [P] tasks = different files, no dependencies on each other
- [US#] label maps task to specific user story for traceability
- All Redis keys MUST go through `redis_keys.py` — never inline `f"memora:..."` strings
- Redis pool uses `decode_responses=True` — all values are strings, never use `.encode()`
- Practice Log uses raw SQL (not Frappe DocType) — follow Memory State precedent
- Practice sessions are ephemeral (Redis HASH + TTL) — loss = student restarts
- No impact on FSRS, streaks, leaderboards, or XP
- Access checked at session start ONLY (FR-008/FR-009)
