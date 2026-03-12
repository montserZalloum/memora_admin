# Research: Voucher Redemption Log Cleanup

**Feature**: 044-voucher-log-cleanup | **Date**: 2026-03-11

## R-001: Existing Cleanup Task Patterns

**Decision**: Follow the exact same pattern as `task_log_archive_batch_cleanup.py` — the project already has a well-established cleanup task structure.

**Rationale**: The codebase has multiple cleanup tasks (`build_cleanup`, `sync_log_cleanup`, `task_log_archive_batch_cleanup`, `voucher_cleanup`, `announcement_cleanup`) all following the same pattern:
- Wrapper function registered in `hooks.py` scheduler
- Inner `_do_*` function with the actual batched delete logic
- Uses `frappe.db.sql` for SELECT + `frappe.db.delete` + `frappe.db.commit` per batch
- Logging via `logging.getLogger(__name__)`
- Metrics via `task_utils.py` helpers (`log_task_run`, `TASK_RUNS`, `USERS_PROCESSED`, `TASK_DURATION`)
- Error handling with `notify_admins`

**Alternatives considered**: Raw SQL DELETE with LIMIT (no SELECT first) — rejected because the existing pattern is proven and consistent.

## R-002: Target DocType — Memora Voucher Redemption Log

**Decision**: Standard Frappe DocType with `name` PK and `creation` timestamp. Cleanup uses `creation` field only.

**Rationale**: Per spec, `creation` (Frappe insert timestamp) is the sole eligibility field. The business `timestamp` field is irrelevant for cleanup. The table is insert-only audit data, not read by runtime flows.

**Alternatives considered**: Using `timestamp` field — rejected per FR-003.

## R-003: Retention Period and Batch Size

**Decision**: 100-day retention, 1000-row batch size (per spec FR-002, FR-004).

**Rationale**: Spec is explicit. These are hardcoded defaults but exposed as function parameters for testability.

## R-004: Schedule Slot

**Decision**: Daily at 05:30 (`"30 5 * * *"`).

**Rationale**: Available slot in `hooks.py` cron schedule. Existing cleanup tasks run between 01:00–05:00. 05:30 is the next available off-peak slot.

**Alternatives considered**: 03:00 as mentioned in spec assumptions — rejected because 03:00 slot already has `leaderboard_cleanup` and `live_sync_trigger`.

## R-005: Test Strategy

**Decision**: Follow `test_task_log_archive_batch_cleanup.py` pattern — Frappe integration tests using `FrappeTestCase`, direct DB insertion, tearDown cleanup.

**Rationale**: Proven pattern in the codebase. Tests cover: zero rows, old rows deleted, recent rows kept, boundary cutoff, multiple batches, incremental commits, restart safety, logging, wrapper success/failure metrics.

**Alternatives considered**: Unit tests with mocked DB — rejected because the existing test pattern uses real DB and is already established.
