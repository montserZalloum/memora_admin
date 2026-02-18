# Tasks: Stats Cache Staleness Detection

**Input**: Design documents from `/specs/019-stats-content-hash/`
**Prerequisites**: plan.md (required), spec.md (required), research.md, data-model.md, contracts/progress-api.md, quickstart.md

**Tests**: Included — plan.md specifies test files (`test_content_hash.py`, `test_stats_staleness.py`).

**Organization**: Tasks grouped by user story. US1 contains the core implementation; US2–US4 are architectural properties verified primarily through tests.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependencies)
- **[Story]**: Which user story this task belongs to (e.g., US1, US2, US3)
- Include exact file paths in descriptions

---

## Phase 1: Foundational (Blocking Prerequisites)

**Purpose**: Hash computation function and model/service plumbing that ALL user stories depend on

**CRITICAL**: No user story work can begin until this phase is complete

- [x] T001 Add `_compute_content_hash(hierarchy: dict) -> str` pure function in `memora_admin/api/hierarchy.py` — incremental `hashlib.md5()` over structural fields (bit_range, excluded_bits sorted, track/unit/topic/lesson IDs, lesson bit_indices, topic lesson counts), truncated to 8 hex chars
- [x] T002 Call `_compute_content_hash()` at end of `get_subject_hierarchy()` to set `hierarchy["content_hash"]` before return in `memora_admin/api/hierarchy.py` (depends on T001, same file)
- [x] T003 [P] Add `content_hash: str = ""` field to `SubjectHierarchy` model in `fastapi_app/models/progress.py` (line ~69, after `free_topics`)
- [x] T004 Add `"_content_hash": hierarchy.content_hash` to output dict in `compute_stats_from_hierarchy()` in `fastapi_app/services/stats.py` (line ~219, before return)

**Checkpoint**: Hash is computed on hierarchy build, flows through model into stats output. Ready for staleness checks.

---

## Phase 2: User Story 1 — Accurate Progress After Content Changes (Priority: P1) MVP

**Goal**: When a content editor adds/removes lessons, students see correct completion percentages on their next progress request instead of stale cached values.

**Independent Test**: Add a lesson to a subject where a student has cached stats, then verify the progress endpoint returns updated totals on the next request.

### Implementation for User Story 1

- [x] T005 [US1] Extend staleness check in `get_subject_progress()` at line 680 of `fastapi_app/api/v1/endpoints/progress.py` — change `if stats is None or "total" not in stats:` to `if stats is None or "total" not in stats or stats.get("_content_hash") != hierarchy.content_hash:`
- [x] T006 [US1] Extend staleness check in `get_subject_tracks()` at line 282 of `fastapi_app/api/v1/endpoints/progress.py` — same condition pattern as T005
- [x] T007 [US1] Extend staleness check in `get_track_detail()` at line 372 of `fastapi_app/api/v1/endpoints/progress.py` — same condition pattern as T005
- [x] T008 [US1] Extend staleness check in `get_unit_detail()` at line 481 of `fastapi_app/api/v1/endpoints/progress.py` — same condition pattern as T005
- [x] T009 [US1] Verify cold-start path in `end_session()` at lines 328-353 of `fastapi_app/api/v1/endpoints/sessions.py` — confirm `compute_stats_from_hierarchy()` call already includes `_content_hash` via T004; document that HINCRBY warm path (lines 340-353) is intentionally unchanged per FR-008

### Tests for User Story 1

- [x] T010 [P] [US1] Create unit tests for `_compute_content_hash()` in `fastapi_app/tests/test_content_hash.py` — test determinism (same input → same hash), sensitivity (adding/removing/reordering lessons changes hash), stability (changing `is_linear`/`xp`/`is_free`/`max_hearts` does NOT change hash), empty hierarchy edge case
- [x] T011 [P] [US1] Create integration test for end-to-end staleness detection in `fastapi_app/tests/test_stats_staleness.py` — seed stats with old `_content_hash`, set hierarchy with new `content_hash`, call progress endpoint, verify recomputation returns correct totals; include timing assertion that recomputation completes within 5ms (SC-004)
- [x] T011b [P] [US1] Add negative test in `fastapi_app/tests/test_stats_staleness.py` verifying bitmap endpoints are NOT affected by staleness check (FR-011) — set hierarchy with new `content_hash` while stats have old `_content_hash`, call lesson-level endpoint (`/progress/{subject}/topics/{topic_id}/lessons`), verify it returns correct data from bitmap without triggering stats recompute

**Checkpoint**: Core feature works — stale stats detected and recomputed on next read. All 4 stats-reading endpoints protected. Bitmap endpoints confirmed unaffected.

---

## Phase 3: User Story 2 — Seamless Migration for Existing Users (Priority: P1)

**Goal**: Students with pre-existing cached stats (no `_content_hash` field) are automatically refreshed on next read — no manual migration or downtime.

**Independent Test**: Query progress for a user whose stats cache has no `_content_hash` field; verify stats are recomputed and include the field afterward.

### Implementation for User Story 2

No additional code changes required — the staleness check from Phase 2 inherently handles this: `stats.get("_content_hash")` returns `None` for pre-migration stats, and `None != hierarchy.content_hash` evaluates as stale, triggering recompute.

### Tests for User Story 2

- [x] T012 [US2] Add integration test for pre-migration self-healing in `fastapi_app/tests/test_stats_staleness.py` — seed stats hash in Redis WITHOUT `_content_hash` field (simulating pre-migration state), call progress endpoint, verify stats are recomputed with `_content_hash` now present and matching `hierarchy.content_hash`

**Checkpoint**: Pre-deployment stats self-heal transparently. Zero manual migration needed.

---

## Phase 4: User Story 3 — No Performance Degradation on Normal Operations (Priority: P1)

**Goal**: During normal operations (no content changes), progress requests complete within existing performance targets with no measurable overhead.

**Independent Test**: Verify that when stats are cached and up-to-date, the response returns cached data without recomputation; verify HINCRBY warm path is unmodified.

### Implementation for User Story 3

No additional code changes required — the O(1) string comparison (`stats.get("_content_hash") != hierarchy.content_hash`) adds negligible overhead. The HINCRBY warm path (FR-008) is intentionally unchanged.

### Tests for User Story 3

- [x] T013 [P] [US3] Add test verifying HINCRBY warm path preserves `_content_hash` field in `fastapi_app/tests/test_stats_staleness.py` — set stats with `_content_hash`, execute HINCRBY on `:completed` fields, verify `_content_hash` field still exists and unchanged
- [x] T014 [P] [US3] Add test verifying fresh stats skip recomputation in `fastapi_app/tests/test_stats_staleness.py` — seed stats with matching `_content_hash`, call progress endpoint, verify stats were served from cache (no recompute call)

**Checkpoint**: Performance characteristics verified — zero overhead on happy path, warm path unmodified.

---

## Phase 5: User Story 4 — Zero Write Storm on Content Changes (Priority: P2)

**Goal**: When content changes, zero bulk writes occur to user stats caches; validation is lazy on each user's next read.

**Independent Test**: Change content (rebuild hierarchy with new hash), verify no writes to stats keys until a user actually requests progress.

### Implementation for User Story 4

No additional code changes required — the architecture is lazy-validation by design. Content changes only affect the hierarchy cache (which has its own invalidation flow). Individual user stats are validated/recomputed on-demand per read.

### Tests for User Story 4

- [x] T015 [US4] Add integration test verifying zero writes to stats caches on content change in `fastapi_app/tests/test_stats_staleness.py` — seed stats for multiple simulated users, change hierarchy `content_hash`, verify all stats keys remain untouched until progress is requested for each user individually

**Checkpoint**: Scaling property verified — content changes trigger zero fan-out writes.

---

## Phase 6: Polish & Cross-Cutting Concerns

**Purpose**: Deployment verification and final validation

- [x] T016 Restart Frappe workers (`bench restart`) and FastAPI (`pkill -f "uvicorn fastapi_app.main:app"`) to activate changes
- [x] T017 Run quickstart.md verification steps — check hierarchy cache has `content_hash` field, check stats cache has `_content_hash` field after a progress request
- [x] T018 Run full test suite to verify zero regressions: `cd /home/corex/aurevia-bench && bench --site x.conanacademy.com run-tests --app memora_admin` and `pytest fastapi_app/tests/`

---

## Dependencies & Execution Order

### Phase Dependencies

- **Foundational (Phase 1)**: No dependencies — can start immediately. BLOCKS all user stories.
- **US1 (Phase 2)**: Depends on Phase 1 completion — core implementation
- **US2 (Phase 3)**: Depends on Phase 2 (uses same staleness check code)
- **US3 (Phase 4)**: Depends on Phase 2 (tests verify warm path behavior)
- **US4 (Phase 5)**: Depends on Phase 2 (tests verify lazy-validation property)
- **Polish (Phase 6)**: Depends on all phases complete

### Within-Phase Dependencies

**Phase 1 (Foundational)**:
- T001 → T002 (same file, T002 calls function from T001)
- T003 is parallel (different file)
- T004 depends on T003 (needs to reference `hierarchy.content_hash`)

**Phase 2 (US1)**:
- T005–T008 are sequential (same file: `progress.py`)
- T009 depends on T004 (verifies stats output)
- T010, T011 are parallel with each other and with T005–T009 (different files)

**Phase 3–5 (US2–US4)**:
- All test tasks (T012–T015) can run in parallel — same test file but independent test functions

### Parallel Opportunities

- T003 can run in parallel with T001+T002 (different files)
- T010, T011 can run in parallel with T005–T009 (tests vs implementation)
- T013, T014 can run in parallel with each other
- US2, US3, US4 test phases can run in parallel after US1 is complete

---

## Parallel Example: Phase 1 (Foundational)

```
# Sequential (same file):
Task T001: Add _compute_content_hash() in memora_admin/api/hierarchy.py
Task T002: Integrate into get_subject_hierarchy() in memora_admin/api/hierarchy.py

# Parallel with above (different file):
Task T003: Add content_hash field in fastapi_app/models/progress.py

# After T003:
Task T004: Add _content_hash to stats output in fastapi_app/services/stats.py
```

## Parallel Example: Phase 2 (US1)

```
# Sequential (same file):
Task T005-T008: Extend staleness checks in fastapi_app/api/v1/endpoints/progress.py

# Parallel with above (different files):
Task T010: Unit tests in fastapi_app/tests/test_content_hash.py
Task T011: Integration tests in fastapi_app/tests/test_stats_staleness.py
```

---

## Implementation Strategy

### MVP First (Phase 1 + Phase 2 = US1 Only)

1. Complete Phase 1: Foundational (T001–T004)
2. Complete Phase 2: US1 implementation (T005–T009) + tests (T010–T011)
3. **STOP and VALIDATE**: Run tests, verify staleness detection works end-to-end
4. Deploy if ready — this alone eliminates the stale stats problem

### Incremental Delivery

1. Phase 1 → Foundational ready (hash computation + plumbing)
2. Phase 2 → US1 working (MVP — stale stats detected and fixed)
3. Phase 3 → US2 tests confirm migration safety
4. Phase 4 → US3 tests confirm performance preservation
5. Phase 5 → US4 tests confirm scaling property
6. Phase 6 → Polish and deploy

### Notes

- This is a 5-file, additive-only feature — no schema migrations, no breaking changes
- US2–US4 require no additional code changes — they are architectural properties verified by tests
- The feature is backward-compatible: pre-existing stats self-heal on next read
- Total estimated tasks: 19 (4 foundational + 8 US1 + 1 US2 + 2 US3 + 1 US4 + 3 polish)
