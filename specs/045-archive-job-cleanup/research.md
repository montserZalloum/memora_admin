# Research: Archive Job Cleanup

## R-001: Cleanup task pattern for two-tier retention

**Decision**: Use a single task function with two sequential cleanup passes — one for `Purged` (30-day retention) and one for `Failed` (90-day retention). Each pass uses the same batched-delete loop.

**Rationale**: The existing cleanup tasks (`task_log_archive_batch_cleanup`, `sync_log_cleanup`, `voucher_log_cleanup`) all handle a single status with a single retention period. This feature requires two status/retention pairs, but the core loop is identical. Running two passes in sequence within one task invocation keeps the scheduler simple (one cron entry) and the total count/batch tracking unified.

**Alternatives considered**:
- Two separate tasks (one per status): Rejected — doubles scheduler entries, hooks.py changes, and test boilerplate for no operational benefit.
- Single query with `CASE WHEN` cutoff: Rejected — makes the SQL harder to read and debug; two clean passes are more maintainable.

## R-002: Dependency check against Memora Task Log Archive Batch

**Decision**: Use a subquery exclusion pattern — `WHERE name NOT IN (SELECT DISTINCT archive_job_id FROM ... WHERE status NOT IN ('Purged', 'Failed'))` — to skip archive jobs that still have active child batch rows.

**Rationale**: The spec requires (FR-004) that a terminal archive job is NOT deleted if any related `Memora Task Log Archive Batch` row is in a non-terminal state (`Pending`, `Exported`, `Synced`). The `archive_job_id` field on the batch table stores the archive job's `name` as a plain string. A `NOT IN` subquery is the simplest correct approach and handles the "no related batch rows" case correctly (job IS eligible if it has zero batch rows).

**Alternatives considered**:
- LEFT JOIN with NULL check: Functionally equivalent but slightly more complex SQL for this use case. Both produce the same query plan for the expected data volumes.
- Pre-fetch active job IDs into a Python set: Rejected — adds a separate query and Python-side filtering. The SQL approach keeps it atomic within a single query.

## R-003: Age indicator field — `modified` vs `creation`

**Decision**: Use `modified` as the age indicator for both `Purged` and `Failed` rows.

**Rationale**: The spec explicitly states (Assumptions section): "The `modified` field on `Memora Archive Job` is used as the age indicator because it reflects the last state change timestamp." This is consistent with how Frappe auto-updates `modified` on every `doc.save()` / status change. A job that reached `Purged` status will have `modified` set to the time of that final transition, which is the correct anchor for the retention window.

**Alternatives considered**:
- `creation`: Would measure age from job creation, not completion. A long-running archive job could be deleted before it even reaches terminal state if creation-based. Rejected.
- `completed_at` / `purged_at`: These are stage-specific timestamps. Not all `Failed` jobs have `completed_at` set (they may fail before completion). Using `modified` is universal across both terminal states.

## R-004: Ordering strategy for batched deletion

**Decision**: Order by `modified ASC, name ASC` — delete oldest-modified rows first.

**Rationale**: FR-006 requires "oldest eligible rows first, ordered by `modified ASC`". Adding `name ASC` as a tiebreaker ensures deterministic ordering when multiple rows share the same `modified` timestamp. This matches the pattern used by `task_log_archive_batch_cleanup` (which orders by `purged_at ASC, name ASC`).

**Alternatives considered**: None — this is the only approach that satisfies FR-006.

## R-005: Scheduler time slot

**Decision**: Schedule at `0 6 * * *` (daily at 06:00 UTC) — after the existing cleanup chain and notification tasks.

**Rationale**: The existing cleanup tasks run between 02:00 and 05:30. The failed archive notification runs at 06:00. Scheduling archive job cleanup at 06:30 (`30 6 * * *`) keeps it in the low-traffic maintenance window and runs AFTER the batch cleanup task (04:30), ensuring batch rows have been cleaned up first — reducing the chance of the dependency check blocking a deletion.

**Alternatives considered**:
- Running before batch cleanup: Rejected — would mean more archive jobs are blocked by active batch rows that could have been cleaned up first.
- Running at the same time as another task: Rejected — serial scheduling avoids competing for DB resources during the maintenance window.

## R-006: Batch size default

**Decision**: Use 500 as the default batch size, matching `task_log_archive_batch_cleanup`.

**Rationale**: Archive jobs accumulate slowly (roughly one per doctype+scope+version per season). Even after years of operation, the total eligible rows per cleanup run will typically be in the tens, not thousands. A batch size of 500 means the task will almost always complete in a single batch per status pass. The configurable parameter allows adjustment if needed.

**Alternatives considered**:
- 100: Unnecessarily small for the expected volume.
- 1000: Acceptable but no benefit over 500 for this low-volume table.
