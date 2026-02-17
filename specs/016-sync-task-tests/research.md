# Research: Sync Task Tests

**Feature**: 016-sync-task-tests
**Date**: 2026-02-17

## R1: Test Framework for Sync Tasks

**Decision**: Use `FrappeTestCase` (unittest) via `bench run-tests`, NOT pytest.

**Rationale**: The sync tasks in `memora_admin/tasks/sync.py` directly import `frappe` and use `frappe.db`, `frappe.get_doc()`, `frappe.log_error()`. They require a fully bootstrapped Frappe environment. The `bench run-tests` runner handles site initialization, database connections, and test discovery.

**Alternatives considered**:
- pytest with mocked frappe: Would require mocking 15+ frappe APIs (db.get_value, db.set_value, get_doc, db.commit, log_error, db.count, conf.redis_cache). Too fragile — the tests would test mocks, not sync logic.
- pytest with frappe bootstrap: Possible but non-standard. FrappeTestCase provides rollback-on-teardown for free.

## R2: Redis Connection in Tests

**Decision**: Use real Redis at `redis://127.0.0.1:13000` (same as production Frappe cache). Use unique test-prefixed keys and clean up in `tearDown()`.

**Rationale**: The sync tasks use `redis.from_url(frappe.conf.redis_cache)` which resolves to `redis://127.0.0.1:13000`. Tests must use the same Redis instance. However, sync tasks use hardcoded `memora:` prefixed keys (not prefix-parameterized), so tests must:
1. Write to the actual `memora:dirty:wallets` / `memora:dirty:progress` / `memora:buffer:interactions` keys
2. Use uniquely-generated player/subject IDs to avoid collisions with real data
3. Clean up ALL written keys in `tearDown()`

**Key constraint**: The sync functions' `get_redis()` returns a plain `redis.Redis` client (synchronous, not async). Tests use the same synchronous client.

**Alternatives considered**:
- Prefix isolation (like FastAPI tests): Not possible — sync.py hardcodes key names via constants.
- Separate test Redis instance: Unnecessary complexity; unique IDs provide sufficient isolation.

## R3: Database Record Cleanup Strategy

**Decision**: Use `frappe.db.rollback()` in `tearDown()` for MariaDB cleanup. FrappeTestCase auto-rolls back, but explicit cleanup of Redis keys is needed.

**Rationale**: FrappeTestCase wraps each test in a transaction that rolls back on teardown. This handles:
- Memora Player Wallet records (created by fixtures)
- Memora Structure Progress records (created/updated by sync)
- Memora Interaction Log records (created by flush)
- Memora Sync Log records (created by audit logging)

Redis keys must be explicitly deleted since they are outside the DB transaction.

## R4: Mocking Partial Failures

**Decision**: Use `unittest.mock.patch` on `frappe.db.set_value` or `frappe.get_doc().insert` to simulate individual item failures while allowing others to succeed.

**Rationale**: Testing partial failure (e.g., 3 dirty wallets, 1 fails) requires the sync function to process items sequentially with per-item error handling. The sync functions already have try/except per item. Mocking `frappe.db.set_value` with `side_effect` that raises on specific calls simulates this.

**Alternatives considered**:
- Creating invalid MariaDB records: Less controllable, hard to target specific items.
- Monkeypatching Redis: Wrong layer — failures are at the DB write layer.

## R5: DocType Schema Requirements for Test Fixtures

**Decision**: Create minimal fixture factories for test data. Reuse `make_player()` from voucher_fixtures.py for Memora Player Profile creation (which creates all dependencies).

### Memora Player Wallet
- Required fields: `player` (Link, unique)
- Defaults: `total_xp=0`, `current_streak=0`, `dirty_flag=0`, `status="Active"`
- Created manually in tests — no existing factory

### Memora Structure Progress
- Required fields: `player` (Link), `subject` (Link)
- Other fields: `passed_lessons_bitset` (Long Text), `completion_percentage` (Float, read-only)
- Autoname: `PROG-.#####.`

### Memora Interaction Log
- Required fields: `player` (Link), `lesson` (Link), `stage_id` (Data), `event_type` (Select), `timestamp` (Datetime)
- Autoname: `LOG-.#####.`
- **Note**: `lesson` is a Link to `Memora Lesson` — tests need a real lesson record or must use a valid lesson name

### Memora Sync Log
- Required fields: `job_id` (Data), `sync_type` (Select: Wallet/Progress/Memory), `status` (Select: Success/Failed)
- Other: `records_processed` (Int)
- Autoname: `SYNC-.#####.`

### Memora Subject
- Required for progress tests (foreign key in Structure Progress)
- Tests can use any existing subject or create a minimal one

### Memora Lesson
- Required for interaction tests (foreign key in Interaction Log)
- Has read-only `subject` field auto-populated from topic hierarchy
- Tests need a real lesson record for FK constraints

## R6: Handling the `_get_subject_lesson_count()` Dependency

**Decision**: Mock `_get_subject_lesson_count()` in progress sync tests to return a controlled lesson count.

**Rationale**: The function queries `frappe.db.count("Memora Lesson", {"subject": subject_id})` and caches in Redis. Creating real lesson records for each test subject adds unnecessary fixture complexity. Mocking the function returns a predictable total for percentage calculations.

**Alternative**: Use `unittest.mock.patch` on the module-level function `memora_admin.tasks.sync._get_subject_lesson_count`.

## R7: Sync Task Redis Key Patterns

| Task | Redis Input Keys | Redis Cleanup Keys |
|------|-----------------|-------------------|
| `sync_dirty_wallets` | `memora:dirty:wallets` (set), `memora:wallet:{player_id}` (hash) | Both keys |
| `sync_dirty_progress` | `memora:dirty:progress` (set), `memora:progress:{user}:{subject}:v{ver}` (bitmap) | Both keys + `memora:subject:total_lessons:{subject}` |
| `flush_interaction_buffer` | `memora:buffer:interactions` (list) | List key |

## R8: Existing Season for Test Fixtures

**Decision**: Use existing season `SEAS-00027` when creating player profiles to avoid MySQL partitioning constraints.

**Rationale**: Per CLAUDE.md and existing test infrastructure, creating new seasons triggers MySQL partition errors. All tests that create Memora Player Profile records should pass `season="SEAS-00027"`.

## R9: LTRIM Behavior in flush_interaction_buffer

**Current code behavior** (line 349):
```python
r.ltrim(INTERACTION_BUFFER_KEY, inserted, -1)
```

This trims based on `inserted` count, NOT total `count`. This means:
- If 5 items fetched and 3 inserted (items 0, 1 succeeded; item 2 failed; items 3, 4 succeeded): LTRIM removes first 3 positions (0, 1, 2), keeping failed item 2 at position 0... **wait, no**: LTRIM(key, 3, -1) keeps items from index 3 onward. But the failures could be at any position. The code treats failures as "gaps" — it trims `inserted` items from the head regardless of which items failed.

This is actually FINDING-02 from the test plan (LTRIM off-by-one risk). The characterization tests in Phase 7 document this. Our sync task tests should verify the **current behavior** (trim by `inserted` count).

## R10: Sync Log `sync_type` Values

| Task | sync_type Value |
|------|----------------|
| `sync_dirty_wallets` | `"Wallet"` |
| `sync_dirty_progress` | `"Progress"` |
| `flush_interaction_buffer` | `"Memory"` |

Note: "Memory" (not "Interaction") is used for interaction flush per the code at line 367.
