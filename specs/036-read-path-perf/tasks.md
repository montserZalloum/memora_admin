# Tasks: Progress & Practice Read-Path Performance

**Input**: Design documents from `/specs/036-read-path-perf/`
**Prerequisites**: plan.md (required), spec.md (required for user stories), research.md, data-model.md, contracts/

**Tests**: Included per constitution principle VIII (Test-First Coverage). Stats-derived unlock helpers and partial reads get unit tests. Existing endpoint tests must pass unchanged.

**Organization**: Tasks are grouped by user story to enable independent implementation and testing of each story.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependencies)
- **[Story]**: Which user story this task belongs to (e.g., US1, US2, US3)
- Include exact file paths in descriptions

## Path Conventions

All paths relative to repository root (`/home/corex/aurevia-bench/apps/memora_admin/`):
- **Services**: `fastapi_app/services/`
- **Endpoints**: `fastapi_app/api/v1/endpoints/`
- **Config**: `fastapi_app/core/`
- **Tests**: `fastapi_app/tests/`

---

## Phase 1: Foundational (Stats-Derived Unlock Helpers)

**Purpose**: Add pure functions that derive unlock state from stats hash instead of bitmap iteration. Required by both US1 and US2 before their endpoint refactors can proceed.

**⚠️ CRITICAL**: US1 and US2 cannot begin until these helpers exist and are tested.

- [x] T001 Add `_is_entity_complete_from_stats(entity_id, stats)` helper function in `fastapi_app/api/v1/endpoints/progress.py`. Takes an entity ID (track/unit/topic) and stats dict, returns `True` if `int(stats.get(f"{entity_id}:completed", "0")) >= int(stats.get(f"{entity_id}:total", "0"))` and total > 0. Place alongside existing `_is_track_complete` helper (around line 75).
- [x] T002 Add `_is_unit_unlocked_from_stats(track_idx, unit_idx, hierarchy, stats)` helper in `fastapi_app/api/v1/endpoints/progress.py`. Mirror exact logic of existing `_is_unit_unlocked` (line 102) but use `_is_entity_complete_from_stats` instead of `_is_track_complete`/`_is_unit_complete` with `completed_bits`. Check: if track_idx > 0 and hierarchy.is_linear, previous track must be complete from stats. If unit_idx > 0 and track.is_linear, previous unit must be complete from stats.
- [x] T003 Add `_is_topic_unlocked_from_stats(track_idx, unit_idx, topic_idx, hierarchy, stats)` helper in `fastapi_app/api/v1/endpoints/progress.py`. Mirror exact logic of existing `_is_topic_unlocked` (line 128) but use stats-derived checks. Check: unit must be unlocked from stats, and if topic_idx > 0 and unit.is_linear, previous topic must be complete from stats.
- [x] T004 Add `_stats_are_valid(stats, content_hash)` validation helper in `fastapi_app/api/v1/endpoints/progress.py`. Returns `True` only if stats is not None, has `"total"` key, and `stats.get("_content_hash") == content_hash`. This is the guard for using stats-first path vs fallback to bitmap.
- [x] T005 Add unit tests for stats-derived unlock helpers in `fastapi_app/tests/test_stats_derived_helpers.py`. Test: (a) `_is_entity_complete_from_stats` with complete/incomplete/empty/zero-total cases, (b) `_is_unit_unlocked_from_stats` with linear/non-linear tracks, (c) `_is_topic_unlocked_from_stats` with linear/non-linear units, (d) `_stats_are_valid` with matching/mismatching/missing hash. Use mock hierarchy and stats dicts — no Redis needed.

**Checkpoint**: Unlock helpers exist and are tested. Foundation ready for US1 and US2.

---

## Phase 2: User Story 1 - Warm Progress Reads Skip Redundant Computation (Priority: P1) 🎯 MVP

**Goal**: Progress detail endpoints use cached stats first, skipping bitmap decode when valid stats exist. Fallback to current bitmap path on miss/stale.

**Independent Test**: Issue a progress detail request when valid stats exist in Redis and confirm the response is returned without `get_completed_bits()` being called. Verify response is identical to current behavior.

### Implementation for User Story 1

- [x] T006 [US1] Refactor `get_subject_progress` endpoint (line 620) in `fastapi_app/api/v1/endpoints/progress.py`. Add stats-first fast path: call `stats_service.get_stats()` first, check with `_stats_are_valid(stats, hierarchy.content_hash)`. If valid, build response using stats dict and stats-derived unlock helpers — skip `progress_service.get_completed_bits()` entirely. If stats miss/stale, fall back to current path: `get_completed_bits()` → `get_or_recompute()`. Preserve identical response shape. Add structlog event `stats_cache_hit` or `stats_cache_miss`.
- [x] T007 [US1] Refactor `get_progress_summary` endpoint (line 177) in `fastapi_app/api/v1/endpoints/progress.py`. Inside `_fetch_subject_summary`, try stats-first: call `stats_service.get_partial_stats(user.sub, subject_id, hierarchy.version, ["completed", "total", "_content_hash"])`. If valid (hash matches and both fields present), use `completed` and `total` from stats — skip `progress_service.get_completed_count()` (BITCOUNT). If miss, fall back to current BITCOUNT path. This is a partial stats read with only 3 fields.
- [x] T008 [US1] Add stats-first activation tests in `fastapi_app/tests/test_progress_endpoints.py`. Test: (a) when valid stats exist in Redis (with matching `_content_hash`), assert `get_subject_progress` does NOT call `progress_service.get_completed_bits()` — use `unittest.mock.AsyncMock` to spy on the method, (b) when stats are missing or `_content_hash` mismatches, assert `get_completed_bits()` IS called (fallback path). This validates the optimization is actually activated, not just that responses are correct.
- [x] T008b [US1] Run existing tests to verify zero behavioral regression: `python -m pytest fastapi_app/tests/test_progress_endpoints.py -v` and `python -m pytest fastapi_app/tests/test_stats_service.py -v`. All tests must pass without modification. Fix any failures before proceeding.

**Checkpoint**: Full subject progress and summary endpoints use stats-first path. Bitmap decode skipped on warm cache. Responses identical to before.

---

## Phase 3: User Story 2 - Partial Progress Routes Fetch Only Required Data (Priority: P1)

**Goal**: Tracks list, track detail, and unit detail endpoints read only the stats fields they need via HMGET instead of fetching the entire hash via HGETALL.

**Independent Test**: Request track-level progress and confirm the backend uses HMGET with ~21 fields instead of HGETALL with ~500 fields.

### Implementation for User Story 2

- [x] T009 [US2] Add `get_partial_stats(user_id, subject_id, version, fields)` method to `StatsService` in `fastapi_app/services/stats.py`. Use `self.redis.hmget(key, fields)` to read only requested fields. Return `dict[str, str] | None` — None if all values are None (key doesn't exist). Handle bytes decoding same as `get_stats()`. Place after `get_stats` method (around line 175).
- [x] T010 [US2] Add unit test for `get_partial_stats` in `fastapi_app/tests/test_stats_service.py`. Test: (a) partial read returns only requested fields, (b) returns None when key doesn't exist, (c) handles mixed present/absent fields, (d) bytes decoding works correctly. Use real Redis (per constitution — no mocking Redis).
- [x] T011 [US2] Refactor `get_subject_tracks` endpoint (line 253) in `fastapi_app/api/v1/endpoints/progress.py`. Build field list: `["_content_hash"]` + `[f"{t.track_id}:completed", f"{t.track_id}:total" for t in hierarchy.tracks]`. Call `stats_service.get_partial_stats(fields=...)`. If valid (hash matches + all fields present), use stats for counts and `_is_entity_complete_from_stats` for unlock — skip `get_completed_bits()` and `get_or_recompute()`. On miss/stale, fall back to current path.
- [x] T012 [US2] Refactor `get_track_detail` endpoint (line 325) in `fastapi_app/api/v1/endpoints/progress.py`. Build field list: `["_content_hash"]` + track fields + prev track fields (for unlock) + all unit fields in the track. Call `get_partial_stats`. If valid, use stats-derived unlock helpers. On miss, fall back to current path.
- [x] T013 [US2] Refactor `get_unit_detail` endpoint (line 424) in `fastapi_app/api/v1/endpoints/progress.py`. Build field list: `["_content_hash"]` + unit fields + prev unit fields + prev track fields (for unlock chain) + all topic fields in the unit. Call `get_partial_stats`. If valid, use stats-derived unlock helpers. On miss, fall back to current path.
- [x] T014 [US2] Add partial stats activation tests in `fastapi_app/tests/test_progress_endpoints.py`. Test: (a) when valid partial stats exist, assert `get_subject_tracks` uses `get_partial_stats()` and does NOT call `get_completed_bits()`, (b) when partial stats miss, assert fallback to `get_completed_bits()`. Validates the HMGET optimization is actually activated.
- [x] T014b [US2] Run existing tests to verify zero behavioral regression: `python -m pytest fastapi_app/tests/test_progress_endpoints.py -v`. All must pass without modification. Fix any failures before proceeding.

**Checkpoint**: Partial progress endpoints use HMGET with targeted field lists (~20 fields vs ~500). Bitmap decode skipped on warm cache for all partial routes.

---

## Phase 4: User Story 3 - Cache Misses Coalesce for Shared Data (Priority: P2)

**Goal**: Concurrent cache misses for the same hierarchy or practice metadata key trigger at most one upstream Frappe fetch per worker process. Other requests wait briefly or degrade gracefully on timeout.

**Independent Test**: Simulate multiple concurrent requests for the same uncached hierarchy key and confirm only one Frappe API call occurs.

### Implementation for User Story 3

- [x] T015 [P] [US3] Add per-key cache-fill coalescing to `HierarchyService.get_hierarchy()` in `fastapi_app/services/hierarchy.py`. Add module-level `_hierarchy_fill_locks: dict[str, asyncio.Lock] = {}` and `_MAX_FILL_LOCKS = 5_000`. Add `_get_hierarchy_fill_lock(key)` using same `setdefault` + prune pattern as `stats.py:30-51`. In `get_hierarchy()`, after Redis miss and before Frappe call: acquire per-key lock with `asyncio.wait_for(lock.acquire(), timeout=5.0)`. On lock acquired, double-check Redis before Frappe call (another request may have filled). On timeout, proceed without lock (bounded duplicate work). Log `hierarchy_fill_coalesced` on double-check hit, `hierarchy_fill_timeout` on timeout. Add `import asyncio` at top if missing.
- [x] T016 [P] [US3] Add per-key cache-fill coalescing to `PracticeService._load_hierarchy_meta()` in `fastapi_app/services/practice.py`. Add module-level `_meta_fill_locks: dict[str, asyncio.Lock] = {}` and `_MAX_META_FILL_LOCKS = 5_000`. Add `_get_meta_fill_lock(key)` with same pattern. In `_load_hierarchy_meta()`, after Redis miss and before Frappe call: acquire lock with 5s timeout. On lock acquired, double-check Redis. On timeout, proceed without lock. Log `meta_fill_coalesced` or `meta_fill_timeout`. Add `import asyncio` at top if missing.
- [x] T017 [US3] Add coalescing unit test in `fastapi_app/tests/test_hierarchy_service.py` (or `test_progress_endpoints.py` if no hierarchy test file exists). Test: delete hierarchy cache key, fire 5 concurrent `get_hierarchy()` calls via `asyncio.gather()` for the same subject, mock or spy on the Frappe client call, assert it was called exactly once (validates SC-003). Repeat for `_load_hierarchy_meta()` to validate SC-004. Include a timeout test: acquire the fill lock manually, fire a request, assert it proceeds after timeout without deadlock.
- [x] T017b [US3] Verify coalescing doesn't break existing functionality: restart FastAPI (`pkill -f "uvicorn fastapi_app.main:app"`), wait 3s, verify health check (`curl http://127.0.0.1:8002/api/v1/health/live`), then run `python -m pytest fastapi_app/tests/test_progress_endpoints.py -v` and any practice endpoint tests.

**Checkpoint**: Cache misses for hierarchy and practice metadata are coalesced per-key. Burst traffic no longer causes N duplicate Frappe calls.

---

## Phase 5: User Story 4 - Practice Hierarchy Evaluates Subject Access Once (Priority: P2)

**Goal**: Subject-level access is computed once per request in `get_practice_hierarchy()`, not re-evaluated for every track in the loop. Per-track grants still checked individually.

**Independent Test**: Request practice hierarchy and confirm subject-level access evaluated once (1 Redis check), while track-level checks still run per track. Response identical to current behavior.

### Implementation for User Story 4

- [x] T018 [US4] Hoist subject-level access out of the track loop in `PracticeService.get_practice_hierarchy()` at `fastapi_app/services/practice.py:199-208`. Before the `for track in hier.tracks` loop (line 202), add: `subject_key = f"SUB-{subject_id}"` and `has_subject_access = await self.access.check_access_with_plan(player_id, subject_key, plan_id)`. Inside the loop, replace `has_full_access = await self._check_track_access(...)` with: if `has_subject_access` then `has_full_access = True`, else `has_full_access = await self.access.check_access(player_id, f"TRK-{track.track_id}")`. Keep `has_free = self._track_has_free_content(hier, track_id)` and `has_access = has_full_access or has_free` unchanged.
- [x] T019 [US4] Verify practice hierarchy response is unchanged: restart FastAPI, then test with a known player/subject. Compare response before and after the change. Run any practice endpoint tests if they exist. The `_check_track_access` method can be left in place (other callers like session start may use it) or marked as unused if no other callers exist.

**Checkpoint**: Subject-level access checked once per request. Redis calls reduced from N*(1 subject + 1 track) to 1 subject + N*track (or just 1 if subject access granted).

---

## Phase 6: User Story 5 - Progress Summary Uses Bounded Concurrency (Priority: P2)

**Goal**: Progress summary endpoint processes per-subject lookups with a concurrency cap (6) instead of unbounded `asyncio.gather()` fan-out.

**Independent Test**: Request progress summary for a player with many accessible subjects and confirm lookups are processed in bounded batches rather than all simultaneously.

### Implementation for User Story 5

- [x] T020 [US5] Add `PROGRESS_SUMMARY_CONCURRENCY = 6` module-level constant in `fastapi_app/api/v1/endpoints/progress.py` near the top (after imports, around line 35). This bounds the maximum number of concurrent per-subject fetches in the progress summary endpoint.
- [x] T021 [US5] Wrap the `asyncio.gather()` fan-out in `get_progress_summary` (line 233) with a semaphore in `fastapi_app/api/v1/endpoints/progress.py`. Create `sem = asyncio.Semaphore(PROGRESS_SUMMARY_CONCURRENCY)` inside the endpoint. Define `async def _bounded_fetch(subject_id): async with sem: return await _fetch_subject_summary(subject_id)`. Replace `results = await asyncio.gather(*(_fetch_subject_summary(sid) ...))` with `results = await asyncio.gather(*(_bounded_fetch(sid) for sid in all_accessible), return_exceptions=True)`. Log `progress_summary_bounded` with subject count and concurrency limit.
- [x] T022 [US5] Verify progress summary still returns correct results: run `python -m pytest fastapi_app/tests/test_progress_endpoints.py -v`. All existing tests must pass. The semaphore is transparent to callers — it only limits concurrency, not correctness.

**Checkpoint**: Progress summary no longer creates unbounded fan-out. Redis pressure is smoother under load.

---

## Phase 7: User Story 6 - Production Tuning Without Code Changes (Priority: P3)

**Goal**: Recommended production environment values are documented and ready to apply. No code changes needed — all settings already configurable.

**Independent Test**: Verify service starts correctly with production-tuned `.env` values.

### Implementation for User Story 6

- [x] T023 [US6] Verify production tuning documentation is complete in `specs/036-read-path-perf/quickstart.md`. Confirm it includes recommended values for `REDIS_MAX_CONNECTIONS=50`, `FRAPPE_TIMEOUT=10.0`, `FRAPPE_MAX_KEEPALIVE=50`. Verify these settings are already defined and configurable in `fastapi_app/core/config.py` (lines 70, 79, 81). No code changes needed — only documentation verification.

**Checkpoint**: Production tuning guide is ready. Operators can apply settings via `.env` without code deployment.

---

## Phase 8: Polish & Cross-Cutting Concerns

**Purpose**: Final verification that all optimizations preserve existing behavior.

- [x] T024 Run full test suite to verify zero behavioral regression: `python -m pytest fastapi_app/tests/ -v --tb=short`. All existing tests must pass without modification. This validates SC-007 (API contracts unchanged), SC-008 (existing tests pass), and FR-016 (existing in-process caching and per-key coordination mechanisms remain intact — verified by existing stats coalescing and hierarchy cache tests passing).
- [x] T025 Restart FastAPI and verify health check: `pkill -f "uvicorn fastapi_app.main:app"` then `curl http://127.0.0.1:8002/api/v1/health/live`. Confirm service starts cleanly with all optimizations active.
- [x] T026 Smoke test all optimized endpoints per `specs/036-read-path-perf/quickstart.md` section "Testing the Optimizations". Verify: (a) progress tracks returns correct data, (b) track detail returns correct data, (c) unit detail returns correct data, (d) full subject progress returns correct data, (e) practice hierarchy returns correct data, (f) progress summary returns correct data.

---

## Dependencies & Execution Order

### Phase Dependencies

- **Foundational (Phase 1)**: No dependencies — start immediately. BLOCKS Phase 2 and Phase 3.
- **US1 (Phase 2)**: Depends on Phase 1 (unlock helpers). Can start as soon as Phase 1 completes.
- **US2 (Phase 3)**: Depends on Phase 1 (unlock helpers). Can start in parallel with Phase 2 since they modify different parts of `progress.py` (US1 = full subject/summary endpoints, US2 = partial endpoints + stats.py).
- **US3 (Phase 4)**: No dependency on Phase 1. Can start immediately in parallel with everything. Modifies `hierarchy.py` and `practice.py` (no conflicts).
- **US4 (Phase 5)**: No dependency on Phase 1. Can start immediately. Modifies `practice.py` — potential conflict with T016 (US3 meta coalescing). Schedule T018 after T016 or coordinate edits.
- **US5 (Phase 6)**: No dependency on Phase 1. Can start immediately. Modifies `progress.py` — potential conflict with US1/US2. Schedule after Phase 2 or coordinate edits to different functions.
- **US6 (Phase 7)**: No dependencies. Can run anytime.
- **Polish (Phase 8)**: Depends on all other phases being complete.

### User Story Dependencies

- **US1 (P1)**: Depends on Foundational (Phase 1) — needs unlock helpers
- **US2 (P1)**: Depends on Foundational (Phase 1) — needs unlock helpers
- **US3 (P2)**: Independent — different files entirely (`hierarchy.py`, `practice.py`)
- **US4 (P2)**: Independent — but shares `practice.py` with US3 (coordinate)
- **US5 (P2)**: Independent — but shares `progress.py` with US1/US2 (coordinate)
- **US6 (P3)**: Fully independent — documentation only

### Within Each User Story

- Service-level changes before endpoint-level changes
- Implementation before verification tests
- All existing tests must pass before checkpoint

### Parallel Opportunities

**Immediately parallelizable (different files)**:
- T015 (US3 hierarchy coalescing in `hierarchy.py`) and T016 (US3 meta coalescing in `practice.py`)
- T009 (US2 get_partial_stats in `stats.py`) and T001-T004 (Phase 1 unlock helpers in `progress.py`)

**Parallelizable after Phase 1**:
- US1 (T006-T007 in `progress.py` full subject/summary) and US2 (T011-T013 in `progress.py` partial endpoints) — different functions in same file, low conflict risk

**Fully independent streams**:
- Stream A: Phase 1 → US1 → US2 → US5 (all in `progress.py` + `stats.py`)
- Stream B: US3 → US4 (both in `hierarchy.py` + `practice.py`)
- Stream C: US6 (documentation only)

---

## Parallel Example: Maximum Parallelism

```text
# Wave 1 (no dependencies):
T001-T004 (Phase 1: unlock helpers in progress.py)
T009      (US2: get_partial_stats in stats.py) [P]
T015      (US3: hierarchy coalescing in hierarchy.py) [P]
T016      (US3: practice meta coalescing in practice.py) [P]
T020      (US5: add concurrency constant in progress.py) [P — top of file only]
T023      (US6: verify production tuning docs) [P]

# Wave 2 (after Phase 1 completes):
T005      (Phase 1: test unlock helpers)
T006      (US1: refactor full subject endpoint)
T010      (US2: test get_partial_stats) [P]
T017      (US3: verify coalescing)
T018      (US4: hoist subject access in practice.py)
T021      (US5: wrap gather with semaphore)

# Wave 3 (after US1 and US2 foundations):
T007      (US1: refactor summary endpoint)
T008      (US1: stats-first activation tests)
T008b     (US1: verify regression)
T011-T013 (US2: refactor partial endpoints)
T019      (US4: verify practice hierarchy)
T022      (US5: verify summary)

# Wave 4 (after all stories):
T014      (US2: partial stats activation tests)
T014b     (US2: verify regression)
T024-T026 (Polish: full test suite + smoke test)
```

---

## Implementation Strategy

### MVP First (User Story 1 Only)

1. Complete Phase 1: Foundational (unlock helpers)
2. Complete Phase 2: User Story 1 (stats-first for full subject + summary)
3. **STOP and VALIDATE**: Test endpoints, verify identical responses
4. This alone eliminates bitmap decode on the highest-traffic read path

### Incremental Delivery

1. Phase 1 (Foundational) → unlock helpers ready
2. US1 → stats-first for full subject + summary → **Test** → largest perf win
3. US2 → HMGET for partial routes → **Test** → significant Redis payload reduction
4. US3 → cache-fill coalescing → **Test** → burst traffic protection
5. US4 → subject access hoisting → **Test** → practice hierarchy speedup
6. US5 → bounded concurrency → **Test** → smoother load under fan-out
7. US6 → production tuning → ready for deployment
8. Polish → full verification

### Key Risks to Monitor

- **Stats-first correctness**: After T006/T011-T013, carefully compare responses with stats-first path vs bitmap fallback. Unlock states must be identical.
- **File conflicts**: `progress.py` is modified by US1, US2, and US5. Coordinate sequential edits or use different functions.
- **Coalescing deadlocks**: After T015/T016, verify no requests hang under concurrent load. The 5s timeout prevents indefinite blocking.

---

## Notes

- [P] tasks = different files, no dependencies
- [Story] label maps task to specific user story for traceability
- Each user story is independently testable at its checkpoint
- No new Redis keys or API contract changes — pure internal optimization
- Existing tests are the primary regression safety net
- Commit after each task or logical group
- Stop at any checkpoint to validate independently
