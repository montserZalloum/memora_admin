# Quickstart: Archive Job Cleanup

## What This Feature Does

Adds a daily scheduled task that automatically deletes old terminal `Memora Archive Job` rows:
- **Purged** jobs: deleted after 30 days (configurable)
- **Failed** jobs: deleted after 90 days (configurable)

Active/in-progress jobs are never touched. Jobs with active child batch rows are preserved.

## Files to Create/Modify

| Action | File | Purpose |
|--------|------|---------|
| CREATE | `memora_admin/tasks/archive_job_cleanup.py` | Cleanup task implementation |
| CREATE | `memora_admin/tests/test_archive_job_cleanup.py` | Integration test suite |
| MODIFY | `memora_admin/hooks.py` | Add scheduler entry at `30 6 * * *` |

## Implementation Pattern

Follow the established cleanup task pattern exactly as used in `task_log_archive_batch_cleanup.py`:

1. **Wrapper function** (`cleanup_archive_jobs`): handles timing, logging, metrics, error notification
2. **Implementation function** (`_do_archive_job_cleanup`): validates params, runs two sequential passes (Purged then Failed), returns `(total_deleted, batches_executed)`
3. **Batch loop**: `SELECT ... LIMIT batch_size` → `DELETE` → `COMMIT` → repeat until empty
4. **Dependency check**: `NOT IN` subquery against `tabMemora Task Log Archive Batch` non-terminal statuses

## Key Differences from Reference Task

| Aspect | `task_log_archive_batch_cleanup` | `archive_job_cleanup` (new) |
|--------|---|----|
| Target table | `tabMemora Task Log Archive Batch` | `tabMemora Archive Job` |
| Eligible statuses | `Purged` only | `Purged` (30d) + `Failed` (90d) |
| Age field | `purged_at` | `modified` |
| Dependency check | None | Subquery against batch table |
| Pass count | 1 | 2 (one per status) |

## Testing

Run tests via Frappe bench:
```bash
cd /home/corex/aurevia-bench
bench --site x.conanacademy.com run-tests \
  --app memora_admin \
  --module memora_admin.tests.test_archive_job_cleanup
```

## Configuration

| Parameter | Default | Description |
|-----------|---------|-------------|
| `purged_retention_days` | 30 | Days before Purged jobs are eligible |
| `failed_retention_days` | 90 | Days before Failed jobs are eligible |
| `batch_size` | 500 | Max rows per DELETE batch |
