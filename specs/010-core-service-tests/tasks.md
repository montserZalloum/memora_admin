# Tasks: Core Service Tests (Phase 2)

**Input**: Design documents from `/specs/010-core-service-tests/`
**Prerequisites**: plan.md, spec.md, research.md, data-model.md, contracts/

**Tests**: This feature IS tests — all tasks produce test code.

**Organization**: Tasks grouped by user story (one service per story). All three stories are P1 and write to separate files, so they CAN execute in parallel across stories.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependencies on incomplete tasks)
- **[Story]**: US1=AccessService, US2=ProgressService, US3=WalletService
- All file paths relative to repository root

---

## Phase 1: Setup (Verify Infrastructure)

**Purpose**: Confirm Phase 1 infrastructure (conftest.py, fixtures) is operational before writing new tests

- [X] T001 Verify existing test infrastructure by running `python -m pytest fastapi_app/tests/test_xp_calculation.py -v` and confirming all Phase 1 tests pass

**Checkpoint**: Phase 1 fixtures (redis_client, test_prefix, mock_frappe, cleanup_keys) confirmed working

---

## Phase 2: User Story 1 - Verify Access Control Integrity (Priority: P1)

**Goal**: 11 tests covering AccessService grant/revoke, check_access, plan-based fallback, and hydration

**Independent Test**: `python -m pytest fastapi_app/tests/test_access_service.py -v` — 11 passed

**Contract Reference**: `specs/010-core-service-tests/contracts/test_access_service.md`

### Implementation

- [X] T002 [P] [US1] Create test_access_service.py with imports, service fixtures (access_service, access_service_no_frappe), test constants (TEST_PLAYER, TEST_SUBJECT_KEY, TEST_TRACK_KEY, TEST_PLAN_ID), and test class structure (TestGrantRevoke, TestCheckAccess, TestPlanAccess, TestHydration) in fastapi_app/tests/test_access_service.py
- [X] T003 [US1] Implement TestGrantRevoke: test_grant_access_sadd (grant 2 keys → returns 2, SMEMBERS verifies), test_revoke_access_srem (grant 2, revoke 1 → returns 1, SMEMBERS verifies remaining) in fastapi_app/tests/test_access_service.py
- [X] T004 [US1] Implement TestCheckAccess: test_check_access_granted_true (grant key → check returns True), test_check_access_ungranted_false (no grant → check returns False, hydration attempted), test_grant_idempotent (grant same key twice → second returns 0) in fastapi_app/tests/test_access_service.py
- [X] T005 [US1] Implement TestPlanAccess: test_check_with_plan_explicit_first (explicit grant → True, plan not checked), test_check_with_plan_fallback (no grant, SADD plan:free_subjects → True via plan), test_check_with_plan_track_key_no_plan (TRK- key → False even with plan) in fastapi_app/tests/test_access_service.py
- [X] T006 [US1] Implement TestHydration: test_hydration_skips_when_exists (pre-seed access set → mock_frappe.call NOT called), test_hydration_calls_frappe (empty cache → frappe.call with get_player_access_keys → SMEMBERS populated), test_hydration_no_client_logs_warning (no frappe_client → no crash, empty set) in fastapi_app/tests/test_access_service.py
- [X] T007 [US1] Run AccessService tests and verify 11 pass: `python -m pytest fastapi_app/tests/test_access_service.py -v`

**Checkpoint**: AccessService fully tested — grant, revoke, check, plan fallback, hydration all verified

---

## Phase 3: User Story 2 - Verify Progress Tracking Accuracy (Priority: P1)

**Goal**: 8 tests covering ProgressService lesson completion, replay detection, bitmap operations, and hydration

**Independent Test**: `python -m pytest fastapi_app/tests/test_progress_service.py -v` — 8 passed

**Contract Reference**: `specs/010-core-service-tests/contracts/test_progress_service.md`

### Implementation

- [X] T008 [P] [US2] Create test_progress_service.py with imports (ProgressService, DIRTY_PROGRESS_KEY), fixtures (progress_service, progress_service_no_frappe), test constants (TEST_USER, TEST_SUBJECT, TEST_VERSION), autouse dirty cleanup fixture (SREM on teardown), and test class structure (TestLessonCompletion, TestReadOperations, TestHydration) in fastapi_app/tests/test_progress_service.py
- [X] T009 [US2] Implement TestLessonCompletion: test_complete_first_time (complete bit 5 → returns False, GETBIT 5 == 1), test_complete_replay (pre-SETBIT 5 → complete again → returns True), test_complete_marks_dirty (complete → SISMEMBER dirty:progress "USER:SUBJ:v1" == True) in fastapi_app/tests/test_progress_service.py
- [X] T010 [US2] Implement TestReadOperations: test_is_complete_true (complete bit 5 → is_complete returns True), test_is_complete_false (empty bitmap → returns False), test_get_completed_count (complete bits 0,5,10 → get_completed_count returns 3) in fastapi_app/tests/test_progress_service.py
- [X] T011 [US2] Implement TestHydration: test_hydration_from_hex (mock returns {"passed_lessons_bitset": "8001"} → GETBIT 0==1, GETBIT 15==1, BITCOUNT==2, verify frappe.call args), test_hydration_no_client_skips (no frappe_client → no crash, GETBIT 0==0) in fastapi_app/tests/test_progress_service.py
- [X] T012 [US2] Run ProgressService tests and verify 8 pass: `python -m pytest fastapi_app/tests/test_progress_service.py -v`

**Checkpoint**: ProgressService fully tested — completion, replay, bitmap ops, hex hydration all verified

---

## Phase 4: User Story 3 - Verify Wallet/XP Management Correctness (Priority: P1)

**Goal**: 12 tests covering WalletService XP awards, Lua streak script (5 branches), dirty tracking, and hydration

**Independent Test**: `python -m pytest fastapi_app/tests/test_wallet_service.py -v` — 12 passed

**Contract Reference**: `specs/010-core-service-tests/contracts/test_wallet_service.md`

### Implementation

- [X] T013 [P] [US3] Create test_wallet_service.py with imports (WalletService, get_amman_today, get_amman_yesterday, DIRTY_WALLETS_KEY, ZoneInfo, timedelta), fixtures (wallet_service, wallet_service_no_frappe), test constant (TEST_PLAYER), autouse dirty cleanup fixture (SREM on teardown), and test class structure (TestXPOperations, TestStreakLua, TestWalletHydration) in fastapi_app/tests/test_wallet_service.py
- [X] T014 [US3] Implement TestXPOperations: test_award_xp_increment (award 100 then 50 → returns 100 then 150, HGET xp=="150"), test_award_xp_marks_dirty (award → SISMEMBER dirty:wallets == True), test_get_wallet_defaults (empty hash, mock returns None → {"xp": 0, "streak": 0}) in fastapi_app/tests/test_wallet_service.py
- [X] T015 [US3] Implement TestStreakLua (6 tests): test_streak_first_completion (empty hash → streak=1, was_updated=True), test_streak_consecutive (pre-seed streak=5/date=yesterday → streak=6), test_streak_missed_day (pre-seed streak=5/date=2_days_ago → streak=1), test_streak_same_day (pre-seed streak=3/date=today → streak=3, was_updated=False), test_streak_replay_no_change (pre-seed streak=5 + is_replay=True → streak=5, was_updated=False), test_streak_marks_dirty (was_updated=True → dirty set, then replay → no additional dirty) in fastapi_app/tests/test_wallet_service.py
- [X] T016 [US3] Implement TestWalletHydration: test_get_wallet_hydrates (mock returns {total_xp:1500, current_streak:7} → get_wallet returns {xp:1500, streak:7}, verify frappe.call args, HGETALL populated), test_hydration_seeds_redis (ensure_hydrated → HGET xp=="500", streak=="3"), test_hydration_skips_existing (pre-seed HSET xp 100 → ensure_hydrated → mock NOT called, xp unchanged) in fastapi_app/tests/test_wallet_service.py
- [X] T017 [US3] Run WalletService tests and verify 12 pass: `python -m pytest fastapi_app/tests/test_wallet_service.py -v`

**Checkpoint**: WalletService fully tested — XP, streaks (all 5 Lua branches), dirty tracking, hydration all verified

---

## Phase 5: Polish & Cross-Cutting Concerns

**Purpose**: Validate all tests run together and match spec targets

- [X] T018 Run full Phase 2 test suite and verify 31 tests pass: `python -m pytest fastapi_app/tests/test_access_service.py fastapi_app/tests/test_progress_service.py fastapi_app/tests/test_wallet_service.py -v`
- [X] T019 Run all tests (Phase 1 + Phase 2) together and verify no regressions: `python -m pytest fastapi_app/tests/ -v`
- [X] T020 Validate quickstart.md commands work end-to-end per specs/010-core-service-tests/quickstart.md

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: No dependencies — verifies existing infrastructure
- **US1, US2, US3 (Phases 2-4)**: All depend on Setup (Phase 1) completion
  - All three stories can proceed **in parallel** (different files, independent services)
  - Or sequentially: US1 → US2 → US3
- **Polish (Phase 5)**: Depends on ALL three stories being complete

### Within Each User Story

Task execution is **sequential** (all tasks modify the same file):
1. Scaffolding (create file, imports, fixtures, constants)
2. Test groups (add tests to file, one group at a time)
3. Validation (run tests, verify count)

### Parallel Opportunities

```text
After T001 (Setup) completes, three parallel streams can start:

Stream A (US1):  T002 → T003 → T004 → T005 → T006 → T007
Stream B (US2):  T008 → T009 → T010 → T011 → T012
Stream C (US3):  T013 → T014 → T015 → T016 → T017

After all streams: T018 → T019 → T020
```

---

## Parallel Example: All Three Stories

```bash
# These three tasks can launch simultaneously (different files):
Task T002: "Create test_access_service.py scaffolding"
Task T008: "Create test_progress_service.py scaffolding"
Task T013: "Create test_wallet_service.py scaffolding"
```

---

## Implementation Strategy

### MVP First (User Story 1 Only)

1. Complete Phase 1: Setup verification (T001)
2. Complete Phase 2: US1 — AccessService tests (T002-T007)
3. **STOP and VALIDATE**: `python -m pytest fastapi_app/tests/test_access_service.py -v` → 11 passed
4. This alone validates the most security-critical service

### Incremental Delivery

1. Setup → Verify infrastructure ✓
2. Add US1 (AccessService) → 11 tests pass → Security boundary validated
3. Add US2 (ProgressService) → 8 tests pass → Learning journey validated
4. Add US3 (WalletService) → 12 tests pass → Gamification validated
5. Run all 31 tests → Full Phase 2 complete

### Parallel Strategy

With 3 parallel agents:
1. All complete Setup together
2. Agent A: US1 (AccessService) — 6 tasks
3. Agent B: US2 (ProgressService) — 5 tasks
4. Agent C: US3 (WalletService) — 5 tasks
5. Merge and run T018-T020 together

---

## Notes

- All tests use **real Redis** (`redis://127.0.0.1:13000`) with prefix isolation — no mocking Redis
- **FrappeClient is mocked** at the `.call()` boundary (only HTTP integration point)
- Dirty keys use **hardcoded** `memora:dirty:*` prefix (NOT test_prefix) — each file needs its own cleanup fixture
- Lua streak script tests must use `get_amman_today()`/`get_amman_yesterday()` from `wallet.py` for timezone consistency
- Hex bitmap hydration: `"8001"` → bits 0 and 15 set (MSB-first ordering)
- Never use FLUSHDB — shared Redis with production Frappe
- Each test file references its contract in `specs/010-core-service-tests/contracts/` for exact setup/assertion details
