# Quickstart: Sync Task Tests

**Feature**: 016-sync-task-tests
**Date**: 2026-02-17

## Prerequisites

1. Frappe bench environment running with site `x.conanacademy.com`
2. Redis running at `redis://127.0.0.1:13000`
3. Existing test infrastructure in `memora_admin/memora_admin/tests/`

## File Locations

```
memora_admin/memora_admin/tests/
├── test_sync_wallets.py        # NEW - 8 tests
├── test_sync_progress.py       # NEW - 5 tests
├── test_flush_interactions.py  # NEW - 6 tests
├── sync_test_base.py           # NEW - Base class + Redis helper
├── voucher_fixtures.py         # EXISTING - reuse make_player()
└── __init__.py                 # EXISTING - no changes
```

## Running Tests

```bash
# All sync tests
cd /home/corex/aurevia-bench
bench --site x.conanacademy.com run-tests --app memora_admin --module memora_admin.tests.test_sync_wallets
bench --site x.conanacademy.com run-tests --app memora_admin --module memora_admin.tests.test_sync_progress
bench --site x.conanacademy.com run-tests --app memora_admin --module memora_admin.tests.test_flush_interactions

# All Frappe tests (including existing voucher tests)
bench --site x.conanacademy.com run-tests --app memora_admin
```

## Test Pattern

Each test follows this pattern:

1. **setUp**: Get Redis connection, generate unique test IDs
2. **Seed Redis**: Write test data to real Redis keys using unique player/subject IDs
3. **Seed MariaDB**: Create prerequisite records (Player Profile, Wallet, etc.) via Frappe ORM
4. **Execute**: Call the sync function directly (e.g., `sync_dirty_wallets()`)
5. **Assert**: Verify MariaDB state (records updated) and Redis state (dirty set cleaned up)
6. **tearDown**: Delete all Redis test keys, let FrappeTestCase rollback DB changes

## Key Design Decisions

- Tests use `FrappeTestCase` (not pytest) because sync tasks import `frappe` directly
- Redis keys use unique generated IDs (e.g., `PLAY-TEST-{uuid}`) for isolation
- Player fixtures reuse existing season `SEAS-00027` to avoid partition constraints
- Partial failures are simulated via `unittest.mock.patch` on `frappe.db.set_value`
- `_get_subject_lesson_count` is mocked to return controlled lesson counts
