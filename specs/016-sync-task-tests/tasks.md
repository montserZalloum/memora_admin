# Tasks: Sync Task Tests

**Input**: Design documents from `/specs/016-sync-task-tests/`
**Prerequisites**: plan.md, spec.md, research.md, data-model.md, contracts/test-contracts.md, quickstart.md

**Tests**: This feature IS a test suite. All tasks produce test code. No separate "test tasks" needed — the implementation IS the tests.

**Organization**: Tasks are grouped by user story (sync task under test) to enable independent implementation and testing.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependencies)
- **[Story]**: Which user story this task belongs to (US1=Wallet, US2=Progress, US3=Interactions)
- Include exact file paths in descriptions

## Path Conventions

- **Test files**: `memora_admin/memora_admin/tests/`
- **Code under test**: `memora_admin/tasks/sync.py` (read-only)
- **Constants**: `fastapi_app/core/constants.py` (read-only)
- **Existing fixtures**: `memora_admin/memora_admin/tests/voucher_fixtures.py` (read-only, reuse `make_player()`)

---

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: Create base class with Redis helpers shared by all 3 test files

- [x] T001 Create `SyncTestCase` base class in `memora_admin/memora_admin/tests/sync_test_base.py` with: (a) `setUp()` that connects to Redis via `frappe.conf.redis_cache` (synchronous `redis.from_url()`), generates a unique test ID prefix (`uuid.uuid4().hex[:8]`), and initializes a `_cleanup_keys` list; (b) `tearDown()` that deletes all keys in `_cleanup_keys` from Redis; (c) helper `_redis_cleanup(keys: list)` to delete specific Redis keys on demand; (d) helper `_make_wallet_record(player_name)` to create a `Memora Player Wallet` doc via `frappe.get_doc().insert(ignore_permissions=True)` with `player=player_name, total_xp=0, current_streak=0, dirty_flag=0, status="Active"`; (e) helper `_seed_redis_wallet(player_id, xp, streak)` to `HSET memora:wallet:{player_id}` with `xp` and `streak` fields AND `SADD memora:dirty:wallets` with `player_id`, tracking both keys in `_cleanup_keys`; (f) helper `_seed_redis_progress(user_id, subject_id, version, bit_positions: list[int])` to `SETBIT memora:progress:{user_id}:{subject_id}:v{version}` for each position AND `SADD memora:dirty:progress` with `"{user_id}:{subject_id}:v{version}"`, tracking keys in `_cleanup_keys`; (g) helper `_push_interaction(data: dict)` to `RPUSH memora:buffer:interactions` with `json.dumps(data)`, tracking the buffer key in `_cleanup_keys`

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Verify base class works and existing fixtures are importable

**CRITICAL**: No user story work can begin until this phase is complete

- [x] T002 Verify `sync_test_base.py` imports correctly by running `cd /home/corex/aurevia-bench && bench --site x.conanacademy.com run-tests --app memora_admin --module memora_admin.tests.sync_test_base` — ensure no import errors. Also verify `from memora_admin.memora_admin.tests.voucher_fixtures import make_player` works from within the test base module. Fix any import path issues.

**Checkpoint**: Base infrastructure ready — user story test files can now be implemented in parallel

---

## Phase 3: User Story 1 — Wallet Sync Task Verification (Priority: P1)

**Goal**: 8 tests verifying `sync_dirty_wallets()` correctly syncs Redis wallet state to MariaDB and handles all edge cases.

**Independent Test**: Run `bench --site x.conanacademy.com run-tests --app memora_admin --module memora_admin.tests.test_sync_wallets`

### Implementation for User Story 1

- [x] T003 [US1] Create `TestSyncDirtyWallets(SyncTestCase)` class scaffold in `memora_admin/memora_admin/tests/test_sync_wallets.py` with imports: `frappe`, `unittest.mock.patch`, `memora_admin.tasks.sync.sync_dirty_wallets`, `SyncTestCase` from `sync_test_base`, `make_player` from `voucher_fixtures`. Add `setUp()` that calls `super().setUp()`, creates a unique player via `make_player(season="SEAS-00027")`, and creates a wallet record via `self._make_wallet_record(player_name)`.

- [x] T004 [US1] Implement `test_happy_path` in `memora_admin/memora_admin/tests/test_sync_wallets.py`: Seed Redis wallet hash with xp=500, streak=3 via `_seed_redis_wallet()`. Call `sync_dirty_wallets()`. Assert: (a) `frappe.db.get_value("Memora Player Wallet", {"player": player_id}, "total_xp")` == 500; (b) `frappe.db.get_value(..., "current_streak")` == 3; (c) `self.r.sismember("memora:dirty:wallets", player_id)` is False.

- [x] T005 [US1] Implement `test_multiple_dirty` in `memora_admin/memora_admin/tests/test_sync_wallets.py`: Create 3 players (each via `make_player(season="SEAS-00027")` + `_make_wallet_record()`), seed each with different xp/streak values via `_seed_redis_wallet()`. Call `sync_dirty_wallets()`. Assert all 3 DB records updated correctly and dirty set is empty (`self.r.scard("memora:dirty:wallets")` == 0 for test players).

- [x] T006 [US1] Implement `test_empty_dirty_set` in `memora_admin/memora_admin/tests/test_sync_wallets.py`: Do NOT seed any Redis data. Call `sync_dirty_wallets()`. Assert no errors raised (implicit — test passes if no exception).

- [x] T007 [US1] Implement `test_missing_wallet_record` in `memora_admin/memora_admin/tests/test_sync_wallets.py`: Create a player via `make_player()` but do NOT create a wallet record. Seed Redis wallet hash + dirty set. Call `sync_dirty_wallets()`. Assert: (a) player removed from dirty set; (b) no `Memora Player Wallet` record exists for player.

- [x] T008 [US1] Implement `test_redis_wallet_missing` in `memora_admin/memora_admin/tests/test_sync_wallets.py`: Create player + wallet record. Add player to dirty set via `self.r.sadd("memora:dirty:wallets", player_id)` but do NOT create Redis wallet hash. Call `sync_dirty_wallets()`. Assert: (a) player removed from dirty set; (b) wallet DB record unchanged (total_xp still 0). Track dirty set key in `_cleanup_keys`.

- [x] T009 [US1] Implement `test_partial_failure` in `memora_admin/memora_admin/tests/test_sync_wallets.py`: Create 3 players + wallets + seed Redis for all 3. Use `unittest.mock.patch("frappe.db.set_value")` with a `side_effect` that raises `Exception("DB error")` for the 2nd player's wallet name but calls the real `set_value` for others. Call `sync_dirty_wallets()`. Assert: (a) players 1 and 3 removed from dirty set; (b) player 2 remains in dirty set (or was re-added by error handling); (c) players 1 and 3 have updated xp values in DB.

- [x] T010 [US1] Implement `test_dirty_flag_cleared` in `memora_admin/memora_admin/tests/test_sync_wallets.py`: Create player + wallet with `dirty_flag=1` (set via `frappe.db.set_value`). Seed Redis wallet. Call `sync_dirty_wallets()`. Assert: `frappe.db.get_value("Memora Player Wallet", {"player": player_id}, "dirty_flag")` == 0.

- [x] T011 [US1] Implement `test_sync_log_created` in `memora_admin/memora_admin/tests/test_sync_wallets.py`: Seed one player dirty + wallet hash + wallet record. Call `sync_dirty_wallets()`. Assert: `frappe.db.exists("Memora Sync Log", {"sync_type": "Wallet", "records_processed": 1, "status": "Success"})` is truthy.

**Checkpoint**: All 8 wallet sync tests pass via `bench --site x.conanacademy.com run-tests --app memora_admin --module memora_admin.tests.test_sync_wallets`

---

## Phase 4: User Story 2 — Progress Sync Task Verification (Priority: P1)

**Goal**: 5 tests verifying `sync_dirty_progress()` correctly converts Redis bitmaps to hex and upserts Structure Progress records.

**Independent Test**: Run `bench --site x.conanacademy.com run-tests --app memora_admin --module memora_admin.tests.test_sync_progress`

### Implementation for User Story 2

- [x] T012 [US2] Create `TestSyncDirtyProgress(SyncTestCase)` class scaffold in `memora_admin/memora_admin/tests/test_sync_progress.py` with imports: `frappe`, `unittest.mock.patch`, `memora_admin.tasks.sync.sync_dirty_progress`, `SyncTestCase` from `sync_test_base`, `make_player` from `voucher_fixtures`. Add `setUp()` that calls `super().setUp()` and creates a unique player via `make_player(season="SEAS-00027")`. Note: progress tests need a subject — either use an existing subject from the DB or create one via `frappe.get_doc({"doctype": "Memora Subject", ...}).insert()`. Store `self.subject_id` and `self.player_id` for use in tests.

- [x] T013 [US2] Implement `test_bitmap_to_hex_upsert` in `memora_admin/memora_admin/tests/test_sync_progress.py`: Seed Redis bitmap with bits 0 and 7 set (positions that produce known hex, e.g., bit 0 set → byte 0x80 → hex "80") via `_seed_redis_progress(player_id, subject_id, 1, [0, 7])`. Mock `memora_admin.tasks.sync._get_subject_lesson_count` to return 10. Call `sync_dirty_progress()`. Assert: `frappe.db.get_value("Memora Structure Progress", {"player": player_id, "subject": subject_id}, "passed_lessons_bitset")` contains the expected hex string (e.g., "81" for bits 0 and 7). Assert: `completion_percentage` == 20.0 (2 bits / 10 lessons * 100).

- [x] T014 [US2] Implement `test_new_record_created` in `memora_admin/memora_admin/tests/test_sync_progress.py`: Ensure no existing `Memora Structure Progress` for this player/subject. Seed Redis bitmap with bit 0. Mock `_get_subject_lesson_count` to return 5. Call `sync_dirty_progress()`. Assert: a new `Memora Structure Progress` record exists with `player=player_id`, `subject=subject_id`, non-empty `passed_lessons_bitset`.

- [x] T015 [US2] Implement `test_existing_record_updated` in `memora_admin/memora_admin/tests/test_sync_progress.py`: Create an existing `Memora Structure Progress` record with `passed_lessons_bitset="00"` via `frappe.get_doc().insert()`. Seed Redis bitmap with bits 0, 1, 2. Mock `_get_subject_lesson_count` to return 10. Call `sync_dirty_progress()`. Assert: the existing record's `passed_lessons_bitset` is updated to a new hex value reflecting the 3 set bits.

- [x] T016 [US2] Implement `test_invalid_dirty_member_format` in `memora_admin/memora_admin/tests/test_sync_progress.py`: Manually `SADD memora:dirty:progress` with a malformed member string (e.g., `"PLAY-001:SUBJ-001"` — missing `:v{version}`). Also seed one valid member. Mock `_get_subject_lesson_count`. Call `sync_dirty_progress()`. Assert: (a) the valid member was processed (Structure Progress record exists); (b) the invalid member remains or was skipped (no crash). Track dirty key in `_cleanup_keys`.

- [x] T017 [US2] Implement `test_empty_bitmap` in `memora_admin/memora_admin/tests/test_sync_progress.py`: Add player to dirty progress set but do NOT set any bitmap bits (no SETBIT calls — just SADD the dirty member). Mock `_get_subject_lesson_count` to return 10. Call `sync_dirty_progress()`. Assert: Structure Progress record has `passed_lessons_bitset=""` (empty string) and `completion_percentage` == 0.

**Checkpoint**: All 5 progress sync tests pass via `bench --site x.conanacademy.com run-tests --app memora_admin --module memora_admin.tests.test_sync_progress`

---

## Phase 5: User Story 3 — Interaction Buffer Flush Verification (Priority: P2)

**Goal**: 6 tests verifying `flush_interaction_buffer()` correctly flushes Redis buffer items to MariaDB Interaction Log docs.

**Independent Test**: Run `bench --site x.conanacademy.com run-tests --app memora_admin --module memora_admin.tests.test_flush_interactions`

### Implementation for User Story 3

- [x] T018 [US3] Create `TestFlushInteractionBuffer(SyncTestCase)` class scaffold in `memora_admin/memora_admin/tests/test_flush_interactions.py` with imports: `frappe`, `json`, `unittest.mock.patch`, `memora_admin.tasks.sync.flush_interaction_buffer`, `SyncTestCase` from `sync_test_base`, `make_player` from `voucher_fixtures`. Add `setUp()` that calls `super().setUp()`, creates a player via `make_player(season="SEAS-00027")`, and finds or creates a valid `Memora Lesson` record (needed for FK constraint). Store `self.player_id` and `self.lesson_id`. Add a helper `_make_interaction(player=None, lesson=None, **overrides)` that returns a dict with all required fields: `{"player": player or self.player_id, "lesson": lesson or self.lesson_id, "stage_id": "STG-1", "event_type": "Completed", "time_spent": 30, "timestamp": "2026-02-17T10:00:00Z", **overrides}`.

- [x] T019 [US3] Implement `test_happy_path` in `memora_admin/memora_admin/tests/test_flush_interactions.py`: Push 3 valid interaction items via `_push_interaction(_make_interaction())` (3 times). Call `flush_interaction_buffer()`. Assert: (a) `frappe.db.count("Memora Interaction Log", {"player": self.player_id})` == 3; (b) `self.r.llen("memora:buffer:interactions")` == 0 (buffer empty).

- [x] T020 [US3] Implement `test_empty_buffer` in `memora_admin/memora_admin/tests/test_flush_interactions.py`: Do NOT push any items. Call `flush_interaction_buffer()`. Assert no errors raised (implicit pass).

- [x] T021 [US3] Implement `test_invalid_json_skipped` in `memora_admin/memora_admin/tests/test_flush_interactions.py`: Push to buffer: one valid item via `_push_interaction()`, one invalid JSON string via `self.r.rpush("memora:buffer:interactions", "NOT-VALID-JSON")`, one more valid item. Call `flush_interaction_buffer()`. Assert: (a) 2 Interaction Log docs created for the player; (b) buffer is empty (LTRIM removed all 3 fetched items — the `inserted` count is 2 but LTRIM uses `inserted` which trims from head). Note: verify actual LTRIM behavior — per code line 349, `r.ltrim(key, inserted, -1)` where `inserted=2` means elements at index 0 and 1 are removed, keeping from index 2. Since 3 items were fetched, 1 remains. Adjust assertion based on actual code: `self.r.llen("memora:buffer:interactions")` == 1.

- [x] T022 [US3] Implement `test_missing_fields_skipped` in `memora_admin/memora_admin/tests/test_flush_interactions.py`: Push to buffer: one valid item, one item missing `player` field (`{"lesson": "LES-001", "stage_id": "STG-1"}`), one valid item. Call `flush_interaction_buffer()`. Assert: (a) 2 Interaction Log docs created; (b) `self.r.llen("memora:buffer:interactions")` == 1 (LTRIM(2, -1) keeps 1 item from the 3 fetched).

- [x] T023 [US3] Implement `test_batch_size_cap` in `memora_admin/memora_admin/tests/test_flush_interactions.py`: Push 1500 valid items to buffer via a loop calling `_push_interaction(_make_interaction())`. Call `flush_interaction_buffer()`. Assert: (a) `frappe.db.count("Memora Interaction Log", {"player": self.player_id})` == 1000 (batch cap); (b) `self.r.llen("memora:buffer:interactions")` == 500 (remaining items).

- [x] T024 [US3] Implement `test_partial_failure_retry` in `memora_admin/memora_admin/tests/test_flush_interactions.py`: Push 3 valid items. Use `unittest.mock.patch.object` on the `frappe.get_doc` return value's `insert` method (or patch `frappe.get_doc` itself) with a `side_effect` that raises `Exception("Insert failed")` on the 2nd call but succeeds on calls 1 and 3. Call `flush_interaction_buffer()`. Assert: (a) 2 Interaction Log docs created (calls 1 and 3 succeeded); (b) `self.r.llen("memora:buffer:interactions")` == 1 (LTRIM(2, -1) keeps 1 item from 3 fetched).

**Checkpoint**: All 6 interaction flush tests pass via `bench --site x.conanacademy.com run-tests --app memora_admin --module memora_admin.tests.test_flush_interactions`

---

## Phase 6: Polish & Cross-Cutting Concerns

**Purpose**: Full suite verification and regression check

- [x] T025 Run all 3 sync test files together: `bench --site x.conanacademy.com run-tests --app memora_admin --module memora_admin.tests.test_sync_wallets && bench --site x.conanacademy.com run-tests --app memora_admin --module memora_admin.tests.test_sync_progress && bench --site x.conanacademy.com run-tests --app memora_admin --module memora_admin.tests.test_flush_interactions`. Verify all 19 tests pass in <30 seconds total.

- [x] T026 Run full Frappe test suite: `bench --site x.conanacademy.com run-tests --app memora_admin`. Verify no regressions in existing voucher and other tests.

- [x] T027 Verify Redis cleanup: After running all tests, check that no residual test keys exist in Redis by running `redis-cli -p 13000 KEYS "memora:wallet:PLAY-*"` and similar patterns. Confirm clean state.

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: No dependencies — T001 can start immediately
- **Foundational (Phase 2)**: Depends on Phase 1 — T002 verifies T001 works
- **User Stories (Phases 3–5)**: All depend on Phase 2 completion
  - US1, US2, US3 can proceed **in parallel** (different test files, no shared state)
  - Or sequentially: US1 → US2 → US3 (priority order)
- **Polish (Phase 6)**: Depends on all user stories being complete

### User Story Dependencies

- **User Story 1 — Wallet (P1)**: Can start after Phase 2. No dependencies on US2/US3.
- **User Story 2 — Progress (P1)**: Can start after Phase 2. No dependencies on US1/US3.
- **User Story 3 — Interactions (P2)**: Can start after Phase 2. No dependencies on US1/US2.

### Within Each User Story

- Class scaffold task MUST complete before individual test methods
- Test methods within a user story are sequential (same file, same class)
- Each user story produces one complete, runnable test file

### Parallel Opportunities

- **T003, T012, T018** can run in parallel after Phase 2 (class scaffolds for all 3 test files)
- Within each story, test methods are sequential (same file)
- All 3 test files can be developed simultaneously by different agents

---

## Parallel Example: All User Stories After Phase 2

```bash
# After Phase 2 is complete, launch all 3 test file implementations in parallel:

# Agent 1: Wallet tests (US1)
Task: "Create TestSyncDirtyWallets scaffold in test_sync_wallets.py" (T003)
Task: "Implement test_happy_path" (T004)
# ... through T011

# Agent 2: Progress tests (US2)
Task: "Create TestSyncDirtyProgress scaffold in test_sync_progress.py" (T012)
Task: "Implement test_bitmap_to_hex_upsert" (T013)
# ... through T017

# Agent 3: Interaction tests (US3)
Task: "Create TestFlushInteractionBuffer scaffold in test_flush_interactions.py" (T018)
Task: "Implement test_happy_path" (T019)
# ... through T024
```

---

## Implementation Strategy

### MVP First (User Story 1 Only)

1. Complete Phase 1: `sync_test_base.py`
2. Complete Phase 2: Verify imports
3. Complete Phase 3: All 8 wallet tests
4. **STOP and VALIDATE**: `bench run-tests --module memora_admin.tests.test_sync_wallets`
5. 8/19 tests passing — wallet sync coverage complete

### Incremental Delivery

1. Setup + Foundational → Base ready
2. Add US1 (Wallet) → 8 tests → Validate (MVP!)
3. Add US2 (Progress) → 5 more tests → Validate (13/19)
4. Add US3 (Interactions) → 6 more tests → Validate (19/19)
5. Polish → Full regression check

---

## Notes

- All test files follow the `FrappeTestCase` pattern (not pytest)
- Redis at `redis://127.0.0.1:13000` — same instance as Frappe, unique IDs for isolation
- Season `SEAS-00027` for all `make_player()` calls
- Code under test (`memora_admin/tasks/sync.py`) is READ-ONLY — no modifications
- LTRIM behavior (line 349): `r.ltrim(key, inserted, -1)` trims by `inserted` count, not `fetched` count
- Sync Log `sync_type` values: `"Wallet"`, `"Progress"`, `"Memory"` (not "Interaction")
- Mock `_get_subject_lesson_count` in progress tests for controlled percentage calculations
