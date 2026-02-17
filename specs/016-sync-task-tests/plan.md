# Implementation Plan: Sync Task Tests

**Branch**: `016-sync-task-tests` | **Date**: 2026-02-17 | **Spec**: [spec.md](spec.md)
**Input**: Feature specification from `/specs/016-sync-task-tests/spec.md`

## Summary

Create 19 integration tests across 3 test files for the Frappe background sync tasks: `sync_dirty_wallets()`, `sync_dirty_progress()`, and `flush_interaction_buffer()`. Tests run under `bench run-tests` using `FrappeTestCase`, use real Redis at `redis://127.0.0.1:13000` with unique-ID isolation, and verify the full Redis-to-MariaDB sync pipeline including happy path, empty input, partial failure, input validation, and audit logging.

## Technical Context

**Language/Version**: Python 3.11+ (Frappe v15 bench environment)
**Primary Dependencies**: Frappe Framework (`frappe.tests.utils.FrappeTestCase`), `redis` (synchronous), `unittest.mock`
**Storage**: MariaDB via Frappe ORM (Player Wallet, Structure Progress, Interaction Log, Sync Log); Redis at `redis://127.0.0.1:13000` (dirty sets, wallet hashes, progress bitmaps, interaction buffer)
**Testing**: `bench run-tests` (unittest-based FrappeTestCase with auto-rollback)
**Target Platform**: Linux server (Frappe bench)
**Project Type**: Single project (test files added to existing test directory)
**Performance Goals**: All 19 tests complete in <30 seconds
**Constraints**: Must not pollute production Redis keys; must not break existing Frappe tests; must use existing season `SEAS-00027` for fixture creation
**Scale/Scope**: 4 new files (3 test files + 1 base class), ~19 tests, ~600 lines

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

### Pre-Research Gate (Pass)

| Principle | Status | Evidence |
|-----------|--------|----------|
| I. Source-of-Truth Awareness | **PASS** | Tests verify BOTH Redis state (dirty set cleanup) AND MariaDB state (record updates) for every sync operation |
| II. Atomic Operation Integrity | **PASS** | Sync tasks don't use Lua scripts — they're sequential Python with per-item error handling. Tests verify the per-item transactional behavior |
| III. Edge-Case-First Design | **PASS** | 11 edge case tests out of 19 total (58%) — exceeds 1:2 ratio requirement. Covers: empty input, missing records, malformed data, partial failures, batch cap |
| IV. Test Isolation | **PASS** | Unique player/subject IDs per test, Redis key cleanup in tearDown, FrappeTestCase auto-rollback for MariaDB |
| V. Business Flow Completeness | **PASS** | Tests cover the complete sync flow: dirty flag → Redis read → MariaDB write → dirty cleanup → audit log |

### Post-Design Gate (Pass)

| Principle | Status | Evidence |
|-----------|--------|----------|
| I. Source-of-Truth Awareness | **PASS** | data-model.md documents both Redis inputs and MariaDB outputs for each sync task |
| II. Atomic Operation Integrity | **PASS** | No atomic Lua scripts involved; per-item error handling is tested via partial failure scenarios |
| III. Edge-Case-First Design | **PASS** | test-contracts.md shows edge cases for every test file |
| IV. Test Isolation | **PASS** | sync_test_base.py provides Redis cleanup helper; unique ID generation prevents cross-test interference |
| V. Business Flow Completeness | **PASS** | All 3 sync flows (wallet, progress, interaction) are tested end-to-end including audit logging |

### Risk Coverage

| Risk ID | Covered By | Notes |
|---------|-----------|-------|
| RISK-03 | `test_partial_failure`, `test_redis_wallet_missing` | Wallet sync failure scenarios |
| RISK-09 | `test_partial_failure_retry`, `test_invalid_json_skipped` | Interaction buffer partial flush |

## Project Structure

### Documentation (this feature)

```text
specs/016-sync-task-tests/
├── plan.md              # This file
├── spec.md              # Feature specification
├── research.md          # Phase 0: Research findings
├── data-model.md        # Phase 1: Entity schemas
├── quickstart.md        # Phase 1: How to run tests
├── contracts/
│   └── test-contracts.md  # Phase 1: Test method contracts
├── checklists/
│   └── requirements.md    # Spec quality checklist
└── tasks.md             # Phase 2 output (created by /speckit.tasks)
```

### Source Code (repository root)

```text
memora_admin/memora_admin/tests/
├── __init__.py                    # EXISTING - no changes
├── sync_test_base.py              # NEW - Base class with Redis helper
├── test_sync_wallets.py           # NEW - 8 tests for sync_dirty_wallets()
├── test_sync_progress.py          # NEW - 5 tests for sync_dirty_progress()
├── test_flush_interactions.py     # NEW - 6 tests for flush_interaction_buffer()
├── voucher_fixtures.py            # EXISTING - reuse make_player()
├── voucher_test_base.py           # EXISTING - reference for pattern
├── voucher_helpers.py             # EXISTING - reference for pattern
└── test_voucher_quickstart.py     # EXISTING - reference for pattern
```

**Code under test** (read-only, no modifications):

```text
memora_admin/tasks/sync.py          # sync_dirty_wallets, sync_dirty_progress, flush_interaction_buffer
fastapi_app/core/constants.py       # DIRTY_WALLETS_KEY, DIRTY_PROGRESS_KEY, INTERACTION_BUFFER_KEY
```

**Structure Decision**: Tests are added to the existing `memora_admin/memora_admin/tests/` directory following the established pattern (FrappeTestCase classes in `test_*.py` files). A new `sync_test_base.py` provides shared Redis setup/teardown logic, mirroring the `voucher_test_base.py` pattern.

## Implementation Phases

### Phase A: Base Infrastructure (`sync_test_base.py`)

Create `SyncTestCase(FrappeTestCase)` base class with:
- `setUp()`: Connect to Redis via `frappe.conf.redis_cache`, generate unique test prefix
- `tearDown()`: SCAN+DEL all Redis keys matching test player/subject IDs
- Helper: `_redis_cleanup(keys: list)` — delete specific Redis keys
- Helper: `_make_wallet_record(player_name)` — create Memora Player Wallet doc
- Helper: `_seed_redis_wallet(player_id, xp, streak)` — HSET wallet hash + SADD dirty
- Helper: `_seed_redis_progress(user_id, subject_id, version, bits)` — SETBIT + SADD dirty
- Helper: `_push_interaction(data: dict)` — RPUSH JSON to interaction buffer

### Phase B: Wallet Sync Tests (`test_sync_wallets.py`)

8 tests in `TestSyncDirtyWallets(SyncTestCase)`:
1. `test_happy_path` — single dirty player → DB updated, dirty cleared
2. `test_multiple_dirty` — 3 players → all synced
3. `test_empty_dirty_set` — no-op
4. `test_missing_wallet_record` — player in dirty but no DB record → warning, removed from dirty
5. `test_redis_wallet_missing` — player in dirty but no Redis hash → removed from dirty
6. `test_partial_failure` — mock `frappe.db.set_value` to fail on 1 of 3 → 2 synced, 1 remains
7. `test_dirty_flag_cleared` — verify `dirty_flag=0` after sync
8. `test_sync_log_created` — verify Memora Sync Log doc with `sync_type="Wallet"`

### Phase C: Progress Sync Tests (`test_sync_progress.py`)

5 tests in `TestSyncDirtyProgress(SyncTestCase)`:
1. `test_bitmap_to_hex_upsert` — SETBIT bits → verify hex string in Structure Progress
2. `test_new_record_created` — no existing doc → INSERT new record
3. `test_existing_record_updated` — existing doc → UPDATE with new bitmap
4. `test_invalid_dirty_member_format` — malformed member → skipped with warning
5. `test_empty_bitmap` — no bitmap data → empty hex, 0% completion

Mock `_get_subject_lesson_count` to return controlled values for percentage calculation.

### Phase D: Interaction Flush Tests (`test_flush_interactions.py`)

6 tests in `TestFlushInteractionBuffer(SyncTestCase)`:
1. `test_happy_path` — 3 valid items → 3 Interaction Log docs, buffer empty
2. `test_empty_buffer` — no-op
3. `test_invalid_json_skipped` — invalid JSON skipped, valid items processed
4. `test_missing_fields_skipped` — item without player/lesson skipped
5. `test_batch_size_cap` — 1500 items → only 1000 processed, 500 remain
6. `test_partial_failure_retry` — 1 insert fails → LTRIM by inserted count

### Phase E: Verification

- Run all 3 test files individually via `bench run-tests`
- Run all Frappe tests to verify no regressions
- Verify Redis has no residual test keys after test completion

## Complexity Tracking

No constitution violations — no complexity tracking needed.
