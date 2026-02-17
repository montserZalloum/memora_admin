# Tasks: Characterization Tests for Known Bugs

**Input**: Design documents from `/specs/015-characterization-tests/`
**Prerequisites**: plan.md (required), spec.md (required), research.md, data-model.md, contracts/test-contract.md

**Tests**: This feature IS tests — all tasks produce test code. Tests assert current buggy behavior (characterization pattern).

**Organization**: Tasks grouped by user story (one per finding). Each story is independently implementable and verifiable.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependencies)
- **[Story]**: Which user story this task belongs to (US1=FINDING-01, US2=FINDING-02, US3=FINDING-03)
- Include exact file paths in descriptions

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: Register the characterization marker and create test file scaffold

- [x] T001 Add `characterization` pytest marker to `pyproject.toml` markers list at line 61-64
- [x] T002 Create `fastapi_app/tests/test_findings.py` with module docstring, imports, and `pytestmark = [pytest.mark.asyncio, pytest.mark.characterization]`

**Details**:

**T001**: In `pyproject.toml`, add to the existing markers array:
```toml
markers = [
	"slow: marks tests as slow (deselect with '-m \"not slow\"')",
	"integration: marks tests as integration tests",
	"characterization: documents known bugs with current behavior assertions",
]
```

**T002**: Create file with imports needed by all 3 test classes:
- `pytest`, `redis.asyncio`, `unittest.mock.AsyncMock`
- `from fastapi_app.services.wallet import WalletService`
- `from fastapi_app.services.stats import StatsService, compute_stats_from_hierarchy`
- `from fastapi_app.tests.conftest import seed_wallet, make_hierarchy_json`
- Module-level `pytestmark` for both `asyncio` and `characterization` markers

---

## Phase 2: User Story 1 — Document XP Hydration Failure (Priority: P1, FINDING-01)

**Goal**: Prove that XP resets to 0 when FrappeClient is unreachable during wallet hydration, documenting FINDING-01 (CRITICAL severity).

**Independent Test**: Run `python3 -m pytest fastapi_app/tests/test_findings.py::TestXPHydrationFailure -v` — both tests pass, proving the bug exists.

### Implementation

- [x] T003 [US1] Write `TestXPHydrationFailure` class docstring and `test_xp_resets_on_hydration_failure` in `fastapi_app/tests/test_findings.py`
- [x] T004 [US1] Write `test_xp_correct_when_cache_populated` in `fastapi_app/tests/test_findings.py`
- [x] T005 [US1] Run `python3 -m pytest fastapi_app/tests/test_findings.py::TestXPHydrationFailure -v` and verify both tests pass

**Details**:

**T003**: Create `TestXPHydrationFailure` class with detailed docstring (FR-004) including:
- Severity: CRITICAL
- Location: `fastapi_app/services/wallet.py:205-213`
- Caller: `fastapi_app/api/v1/endpoints/sessions.py:301-310`
- Current behavior: `ensure_hydrated()` catches all exceptions, HINCRBY starts from 0
- Expected behavior after fix: Error should propagate or completion should be queued

Test `test_xp_resets_on_hydration_failure`:
1. Create `AsyncMock` for FrappeClient where `.call` raises `ConnectionError("Frappe unreachable")`
2. Create `WalletService(redis_client, key_prefix=test_prefix, frappe_client=failing_mock)`
3. Call `await service.ensure_hydrated(player_id)` — should swallow the error
4. Call `await service.award_xp(player_id, 50)` — returns new total
5. Assert: `new_xp == 50` with `# BUG: should be old_xp + 50 if hydration had succeeded`
6. Assert: `await redis_client.hget(f"{test_prefix}wallet:{player_id}", "xp") == "50"`
7. Add `# FIX: When bug is fixed, assert new_xp == old_xp + 50 (e.g., 550 if player had 500)`

**T004**: Test `test_xp_correct_when_cache_populated` (control test proving bug is hydration-specific):
1. Pre-seed wallet hash: `await redis_client.hset(f"{test_prefix}wallet:{player_id}", mapping={"xp": "500", "streak": "0"})`
2. Create `WalletService(redis_client, key_prefix=test_prefix)` — no frappe_client needed
3. Call `await service.award_xp(player_id, 50)`
4. Assert: `new_xp == 550` — correct because cache was already populated

**T005**: Verification — run tests, confirm both pass. Expected output:
```
test_xp_resets_on_hydration_failure PASSED
test_xp_correct_when_cache_populated PASSED
```

**Checkpoint**: FINDING-01 documented. Run `python3 -m pytest fastapi_app/tests/test_findings.py::TestXPHydrationFailure -v` to verify.

---

## Phase 3: User Story 2 — Document Interaction Buffer LTRIM Boundary (Priority: P2, FINDING-02)

**Goal**: Prove that the LTRIM boundary math in `flush_interaction_buffer()` drops failed items and retains already-processed items when partial failures occur, documenting FINDING-02 (MEDIUM severity).

**Independent Test**: Run `python3 -m pytest fastapi_app/tests/test_findings.py::TestInteractionBufferLtrimRisk -v` — both tests pass, proving the bug exists.

### Implementation

- [x] T006 [US2] Write `TestInteractionBufferLtrimRisk` class docstring and `test_partial_failure_drops_failed_item` in `fastapi_app/tests/test_findings.py`
- [x] T007 [US2] Write `test_all_succeed_correct_trim` in `fastapi_app/tests/test_findings.py`
- [x] T008 [US2] Run `python3 -m pytest fastapi_app/tests/test_findings.py::TestInteractionBufferLtrimRisk -v` and verify both tests pass

**Details**:

**T006**: Create `TestInteractionBufferLtrimRisk` class with detailed docstring (FR-004) including:
- Severity: MEDIUM
- Location: `memora_admin/tasks/sync.py:340-349`
- Current behavior: `LTRIM(buffer, inserted, -1)` uses `inserted` count as position index
- Expected behavior after fix: Track actual positions and only trim consecutive head items

Test `test_partial_failure_drops_failed_item`:
1. Create a unique buffer key: `buffer_key = f"{test_prefix}buffer:interactions"`
2. Push 5 JSON items via RPUSH: `[item_0, item_1, item_2, item_3, item_4]`
   - Items are simple JSON: `json.dumps({"player": f"P{i}", "lesson": f"L{i}"})`
3. Simulate the sync.py flush loop logic:
   - Items at positions 0, 2, 4 succeed → `inserted = 3`
   - Items at positions 1, 3 fail (we just track the count, don't actually insert)
4. Execute `await redis_client.ltrim(buffer_key, inserted, -1)` — replicates sync.py:349
5. Get remaining items: `remaining = await redis_client.lrange(buffer_key, 0, -1)`
6. Assert: `len(remaining) == 2` and remaining contains item_3 and item_4
7. Add comments:
   - `# BUG: item_1 (failed) was trimmed and lost — should have been retained for retry`
   - `# BUG: item_2 (succeeded) remains in buffer — will be re-processed next flush`
   - `# FIX: When fixed, remaining should contain only [item_1, item_3] (failed items for retry)`

**T007**: Test `test_all_succeed_correct_trim` (control test proving LTRIM works when all succeed):
1. Create buffer key, push 5 items via RPUSH
2. Simulate: all 5 succeed → `inserted = 5`
3. Execute `await redis_client.ltrim(buffer_key, inserted, -1)`
4. Assert: `remaining == []` — buffer is empty (correct behavior)
5. Comment: `# No bug when all items succeed — count == position in this case`

**T008**: Verification — run tests, confirm both pass.

**Checkpoint**: FINDING-02 documented. Run `python3 -m pytest fastapi_app/tests/test_findings.py::TestInteractionBufferLtrimRisk -v` to verify.

---

## Phase 4: User Story 3 — Document Stats Double-Counting Race (Priority: P3, FINDING-03)

**Goal**: Prove that the check-then-act race between EXISTS and HSET/HINCRBY can cause stats double-counting on cold start, documenting FINDING-03 (LOW severity).

**Independent Test**: Run `python3 -m pytest fastapi_app/tests/test_findings.py::TestStatsDoubleCounting -v` — both tests pass, proving the bug exists.

### Implementation

- [x] T009 [US3] Write `TestStatsDoubleCounting` class docstring and `test_concurrent_cold_start_race` in `fastapi_app/tests/test_findings.py`
- [x] T010 [US3] Write `test_warm_path_increments_correctly` in `fastapi_app/tests/test_findings.py`
- [x] T011 [US3] Run `python3 -m pytest fastapi_app/tests/test_findings.py::TestStatsDoubleCounting -v` and verify both tests pass

**Details**:

**T009**: Create `TestStatsDoubleCounting` class with detailed docstring (FR-004) including:
- Severity: LOW
- Location: `fastapi_app/api/v1/endpoints/sessions.py:316-354`
- Current behavior: Non-atomic EXISTS + HSET/HINCRBY allows double-counting
- Expected behavior after fix: Use SETNX or Lua for atomic stats initialization

Test `test_concurrent_cold_start_race`:
1. Define stats key: `stats_key = f"{test_prefix}stats:PLAYER-TEST:SUB-TEST:v1"`
2. Simulate Request 1 (cold start path from sessions.py:329-345):
   - Check: `exists = await redis_client.exists(stats_key)` → 0
   - Compute stats: `stats = {"completed": "1", "total": "10"}` (bitmap shows 1 completed)
   - Set: `await redis_client.hset(stats_key, mapping=stats)`
3. Simulate Request 2 arriving after Request 1's HSET (sessions.py:346-352):
   - Check: `exists = await redis_client.exists(stats_key)` → 1 (Request 1 set it)
   - Takes warm path: `await redis_client.hincrby(stats_key, "completed", 1)`
4. Read final value: `completed = await redis_client.hget(stats_key, "completed")`
5. Assert: `int(completed) == 2`
6. Comments:
   - `# BUG: completed is 2, but only 1 new lesson was completed`
   - `# Request 1's bitmap computation already counted this lesson, then Request 2's HINCRBY added +1`
   - `# FIX: When fixed with SETNX/Lua, assert completed == 1`

**T010**: Test `test_warm_path_increments_correctly` (control test proving warm path is correct):
1. Pre-seed stats hash: `await redis_client.hset(stats_key, mapping={"completed": "5", "total": "10"})`
2. Single HINCRBY: `await redis_client.hincrby(stats_key, "completed", 1)`
3. Assert: `int(await redis_client.hget(stats_key, "completed")) == 6`
4. Comment: `# No race on warm path — EXISTS returns 1, HINCRBY is atomic`

**T011**: Verification — run tests, confirm both pass.

**Checkpoint**: FINDING-03 documented. All 3 findings now have characterization tests.

---

## Phase 5: Polish & Cross-Cutting Verification

**Purpose**: Validate the complete test suite, marker selection, and no regressions

- [x] T012 Run `python3 -m pytest fastapi_app/tests/test_findings.py -v` and verify all 6 tests pass
- [x] T013 Run `python3 -m pytest -m characterization -v` and verify marker selects exactly 6 tests
- [x] T014 Run `python3 -m pytest fastapi_app/tests/ -v --tb=short` and verify no regressions in existing tests

**Details**:

**T012**: Full file verification. Expected: 6 tests, all PASSED, execution time <10 seconds (SC-004).

**T013**: Marker verification. The `-m characterization` flag should select ONLY the 6 tests in `test_findings.py` and no others. This proves FR-008 (distinguishable from standard tests) and SC-005 (selectively runnable).

**T014**: Regression check. Run the full FastAPI test suite. All existing tests should still pass. The new `test_findings.py` should appear in the output alongside existing test files.

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: No dependencies — start immediately
- **US1 FINDING-01 (Phase 2)**: Depends on Phase 1 (file scaffold exists)
- **US2 FINDING-02 (Phase 3)**: Depends on Phase 1 (file scaffold exists); independent of Phase 2
- **US3 FINDING-03 (Phase 4)**: Depends on Phase 1 (file scaffold exists); independent of Phases 2-3
- **Polish (Phase 5)**: Depends on Phases 2, 3, 4 (all findings implemented)

### User Story Dependencies

- **US1 (FINDING-01)**: Can start after Phase 1 — no dependencies on US2 or US3
- **US2 (FINDING-02)**: Can start after Phase 1 — no dependencies on US1 or US3
- **US3 (FINDING-03)**: Can start after Phase 1 — no dependencies on US1 or US2

### Within Each User Story

1. Write test class docstring + first test (documents bug)
2. Write control test (proves bug is specific, not systemic)
3. Run and verify both pass

### Parallel Opportunities

After Phase 1 completes, all three user stories (US1, US2, US3) can be implemented in parallel since they write to separate classes within the same file but touch independent Redis key patterns:
- US1: `{prefix}wallet:*` keys
- US2: `{prefix}buffer:interactions` key
- US3: `{prefix}stats:*` keys

However, since all tasks write to the same file (`test_findings.py`), parallel execution requires care to avoid merge conflicts. Recommended approach: sequential by priority (T003-T005, then T006-T008, then T009-T011).

---

## Parallel Example: All User Stories After Setup

```bash
# After Phase 1 (T001-T002) is complete:

# Sequential (recommended for single-file feature):
# 1. US1: T003 → T004 → T005 (verify)
# 2. US2: T006 → T007 → T008 (verify)
# 3. US3: T009 → T010 → T011 (verify)
# 4. Polish: T012 → T013 → T014
```

---

## Implementation Strategy

### MVP First (US1 Only — FINDING-01)

1. Complete Phase 1: Setup (T001-T002)
2. Complete Phase 2: US1 FINDING-01 (T003-T005)
3. **STOP and VALIDATE**: `python3 -m pytest fastapi_app/tests/test_findings.py -v` — 2 tests pass
4. The most critical bug (CRITICAL severity) is now documented

### Incremental Delivery

1. Setup (T001-T002) → File scaffold ready
2. US1 FINDING-01 (T003-T005) → CRITICAL bug documented, 2 tests passing
3. US2 FINDING-02 (T006-T008) → MEDIUM bug documented, 4 tests passing
4. US3 FINDING-03 (T009-T011) → LOW bug documented, 6 tests passing
5. Polish (T012-T014) → Full verification, no regressions

---

## Notes

- All tasks write to `fastapi_app/tests/test_findings.py` (except T001 which edits `pyproject.toml`)
- Tests use `# BUG:` / `# FIX:` comment pairs per FR-005 for easy assertion flipping
- Each test class has a detailed docstring per FR-004 (severity, location, current/expected behavior)
- FINDING-04 (OTP "1111") is intentionally excluded per FR-007
- Total: 14 tasks, 6 test cases, 2 files modified
