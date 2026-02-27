# Tasks: Large-Data Performance Fixes

**Input**: Design documents from `/specs/031-large-data-perf-fixes/`
**Prerequisites**: plan.md, spec.md, research.md, data-model.md, contracts/, quickstart.md

**Tests**: Included — spec explicitly requests new test assertions for local cache invalidation and lock coalescing.

**Organization**: Tasks grouped by user story. US1 (hierarchy local cache) and US2 (stats lock coalescing) are fully independent and can run in parallel.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependencies)
- **[Story]**: Which user story this task belongs to (e.g., US1, US2)
- Include exact file paths in descriptions

---

## Phase 1: Setup (Module-Level Dicts + Test Fixture Preparation)

**Purpose**: Declare the module-level data structures (so they can be imported) and update test fixtures to clear them between tests

- [x] T001 [P] Add module-level `_local_hierarchy_cache` dict in `fastapi_app/services/hierarchy.py` — add `import time` at top; add `_local_hierarchy_cache: dict[str, tuple[SubjectHierarchy, float]] = {}` at module level (after imports, before class). Add `LOCAL_TTL = 300` as class constant on `HierarchyService`. Dict maps `subject_id` → `(parsed_hierarchy, expires_at_monotonic)`.
- [x] T002 [P] Add module-level `_compute_locks` dict in `fastapi_app/services/stats.py` — add `_compute_locks: dict[str, asyncio.Lock] = {}` at module level, right after existing `_stats_recompute_semaphore` declaration. Add a helper function `_get_compute_lock(key: str) -> asyncio.Lock` that returns existing lock or creates one via `_compute_locks.setdefault(key, asyncio.Lock())`.
- [x] T003 Add test fixture cleanup for `_local_hierarchy_cache` in `fastapi_app/tests/test_hierarchy_service.py` — import the cache dict from `hierarchy.py` and clear it in the `hierarchy_svc` fixture (setup and teardown via yield)
- [x] T004 [P] Add test fixture cleanup for `_compute_locks` in `fastapi_app/tests/test_stats_service.py` — import the lock dict from `stats.py` and clear it in the `stats_svc` fixture (setup and teardown via yield)

**Checkpoint**: Module-level dicts exist (importable). Test fixtures clear them to prevent pollution.

---

## Phase 2: User Story 1 — Fast Hierarchy Lookups Under Load (Priority: P1) 🎯 MVP

**Goal**: Add module-level in-process TTL cache for parsed `SubjectHierarchy` objects in `hierarchy.py`. Eliminates 50-100ms JSON parse per request on cache hit. Second call for same subject returns instantly without touching Redis.

**Independent Test**: Call `get_hierarchy()` twice for the same subject — verify second call skips Redis. Call `invalidate()` — verify local cache entry removed. Mock `time.monotonic()` to exceed TTL — verify re-fetch from Redis.

### Tests for User Story 1

> **NOTE: Write these tests FIRST, ensure they FAIL before implementation**

- [x] T005 [P] [US1] Add test for local cache hit in `fastapi_app/tests/test_hierarchy_service.py` — new `TestLocalCache` class with test: pre-seed Redis, call `get_hierarchy()` twice, spy on `redis_client.get` to verify it is called only once (second call uses local cache). Clear `_local_hierarchy_cache` in fixture.
- [x] T006 [P] [US1] Add test for local cache TTL expiry in `fastapi_app/tests/test_hierarchy_service.py` — in `TestLocalCache`: mock `time.monotonic()` to simulate 5+ minutes elapsed, call `get_hierarchy()` again, verify Redis is called (local cache expired). Use `unittest.mock.patch("fastapi_app.services.hierarchy.time")`.
- [x] T007 [US1] Update `test_tc_hir_03_invalidate_deletes_key` in `fastapi_app/tests/test_hierarchy_service.py` — after existing assertions, add: verify `_local_hierarchy_cache` does not contain `TEST_SUBJECT` key after `invalidate()`. Pre-populate local cache before invalidation to test clearing.

### Implementation for User Story 1

- [x] T008 [US1] Update `get_hierarchy()` in `fastapi_app/services/hierarchy.py` to check local cache before Redis — at method start: look up `subject_id` in `_local_hierarchy_cache`; if present and `time.monotonic() < expires_at`, return the cached `SubjectHierarchy` immediately (skip Redis). After parsing from Redis or Frappe, store in `_local_hierarchy_cache` with `expires_at = time.monotonic() + self.LOCAL_TTL`.
- [x] T009 [US1] Update `invalidate()` in `fastapi_app/services/hierarchy.py` to clear local cache entry — after `await self.redis.delete(key)`, add `_local_hierarchy_cache.pop(subject_id, None)`.
- [x] T010 [US1] Update `invalidate_all()` in `fastapi_app/services/hierarchy.py` to clear all local cache entries — after the Redis SCAN loop, add `_local_hierarchy_cache.clear()`.

**Checkpoint**: `get_hierarchy()` returns in-process cached objects on repeat calls. `invalidate()` and `invalidate_all()` clear both Redis and local cache. All hierarchy tests pass.

---

## Phase 3: User Story 2 — Eliminate Redundant Stats Recomputation on Cold Start (Priority: P2)

**Goal**: Add per-key `asyncio.Lock` coalescing inside `get_or_recompute()` in `stats.py`. When 50 concurrent requests trigger a cold-start recompute for the same key, only one executes the compute function; the rest wait and read the cached result.

**Independent Test**: Launch 10+ concurrent `get_or_recompute()` calls for the same key with empty cache. Verify the compute function (`compute_stats_from_hierarchy`) executes exactly once. All 10 calls return valid stats.

### Tests for User Story 2

> **NOTE: Write these tests FIRST, ensure they FAIL before implementation**

- [x] T011 [US2] Add lock coalescing concurrency test in `fastapi_app/tests/test_stats_service.py` — new `TestLockCoalescing` class with test: patch `compute_stats_from_hierarchy` with a spy (wraps real function, adds `asyncio.sleep(0.1)` delay to simulate work). Launch 10 concurrent `get_or_recompute()` tasks via `asyncio.gather()` for the same `(user, subject, version)` key with empty cache. Assert spy was called exactly once. Assert all 10 results have correct stats. Clear `_compute_locks` in fixture.

### Implementation for User Story 2

- [x] T012 [US2] Wrap `get_or_recompute()` slow path with per-key lock in `fastapi_app/services/stats.py` — after the fast-path cache hit check (line ~214), get the per-key lock via `_get_compute_lock(key)`. Use `async with lock:` to wrap the slow path. Inside the lock: double-check cache (call `get_stats` again + verify content hash match); if still miss, proceed with existing semaphore acquire → recompute → set_stats. The flow becomes: fast path (no lock) → per-key lock → double-check → semaphore → recompute.

**Checkpoint**: Under concurrent cold-start requests for the same key, compute function runs exactly once. Existing semaphore still limits system-wide concurrency. All stats tests pass.

---

## Phase 4: User Story 3 — Regression Verification (Priority: P1)

**Goal**: Verify all existing test suites pass with zero regressions after both fixes.

**Independent Test**: Run all 4 test suites listed in quickstart.md.

- [x] T013 Run full hierarchy test suite: `python3 -m pytest fastapi_app/tests/test_hierarchy_service.py -v`
- [x] T014 [P] Run full stats test suite: `python3 -m pytest fastapi_app/tests/test_stats_service.py -v`
- [x] T015 [P] Run progress service tests: `python3 -m pytest fastapi_app/tests/test_progress_service.py -v`
- [x] T016 [P] Run progress endpoint tests: `python3 -m pytest fastapi_app/tests/test_progress_endpoints.py -v`

**Note**: SC-002 (p50 < 50ms under 1k concurrent users) is validated via external load testing (Locust, spec 030) — not covered by unit tasks.

**Checkpoint**: All 4 test suites pass. Zero regressions confirmed.

---

## Phase 5: Polish & Cross-Cutting Concerns

**Purpose**: Final validation and cleanup

- [x] T017 Run quickstart.md validation — execute all verification commands from `specs/031-large-data-perf-fixes/quickstart.md`
- [x] T018 Verify memory budget — confirm both caches stay under 50MB per worker: 5 subjects × ~2MB hierarchy = ~10MB; locks at ~100 bytes × estimated active keys. Log confirmation.

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: No dependencies — can start immediately. Creates module-level dicts (T001, T002) then test fixtures (T003, T004).
- **US1 (Phase 2)**: Depends on T001 (dict declaration) and T003 (fixture cleanup)
- **US2 (Phase 3)**: Depends on T002 (dict declaration) and T004 (fixture cleanup)
- **US1 and US2 are INDEPENDENT** — can run in parallel after their respective setup tasks
- **Regression (Phase 4)**: Depends on both US1 and US2 completion
- **Polish (Phase 5)**: Depends on Regression (Phase 4) passing

### User Story Dependencies

- **US1 (P1)**: Depends on T001, T003. Modifies `hierarchy.py` and `test_hierarchy_service.py`
- **US2 (P2)**: Depends on T002, T004. Modifies `stats.py` and `test_stats_service.py`
- **US3 (P1)**: Depends on both US1 and US2 completion
- **No cross-story file conflicts** — US1 and US2 touch completely different files

### Within Each User Story

- Tests written FIRST (T005-T007 for US1, T011 for US2)
- Tests should FAIL before implementation
- Implementation tasks in dependency order (methods → invalidation)
- Story complete when all tests pass

### Parallel Opportunities

- T001 and T002 can run in parallel (different source files)
- T003 and T004 can run in parallel (different test files)
- T005 and T006 can run in parallel (different test classes in same file, no conflicts)
- T008-T010 are sequential within hierarchy.py (each builds on prior)
- **US1 and US2 phases can run fully in parallel** (zero shared files)
- T014, T015, T016 can run in parallel (independent test suites)

---

## Parallel Example: US1 + US2

```bash
# After Phase 1 setup (T001-T004), launch both stories in parallel:

# Stream 1: US1 — Hierarchy Local Cache
Task: T005 "Add local cache hit test in test_hierarchy_service.py"
Task: T006 "Add local cache TTL expiry test in test_hierarchy_service.py"
Task: T007 "Update invalidation test in test_hierarchy_service.py"
Task: T008 "Update get_hierarchy() with local cache check"
Task: T009 "Update invalidate() to clear local cache"
Task: T010 "Update invalidate_all() to clear local cache"

# Stream 2: US2 — Stats Lock Coalescing (runs simultaneously)
Task: T011 "Add lock coalescing concurrency test in test_stats_service.py"
Task: T012 "Wrap get_or_recompute() slow path with per-key lock"
```

---

## Implementation Strategy

### MVP First (US1 Only)

1. Complete Phase 1: T001 + T003 (hierarchy dict + fixture)
2. Complete Phase 2: US1 (T005-T010)
3. **STOP and VALIDATE**: Run `pytest test_hierarchy_service.py -v`
4. This alone delivers the biggest performance win (50-100ms → <1ms on local cache hit)

### Incremental Delivery

1. T001-T004 → Dicts declared + test fixtures ready
2. US1 (T005-T010) → Hierarchy local cache → Test independently → **Biggest win deployed**
3. US2 (T011-T012) → Stats lock coalescing → Test independently → Cold-start protection added
4. Phase 4 → Full regression suite → Confidence in zero regressions
5. Phase 5 → Polish → Feature complete

### Single-Developer Strategy

Since US1 and US2 touch completely different files:
1. Complete Phase 1 (all 4 setup tasks)
2. Complete US1 entirely (tests + implementation)
3. Complete US2 entirely (tests + implementation)
4. Run full regression suite
5. Feature done

---

## Notes

- [P] tasks = different files, no dependencies
- [Story] label maps task to specific user story for traceability
- Both fixes are **module-level** dicts — match existing codebase patterns (`_session_fid_cache`, `_stats_recompute_semaphore`)
- No new files created — only modifications to 4 existing files
- No API contract changes — pure internal optimization
- No Redis key changes — no updates to `redis_keys.py`
- Test fixtures MUST clear module-level caches to prevent test pollution
