# Sync Log Cleanup

## What It Does

`cleanup_sync_logs` is a daily maintenance task that deletes old rows from the
`Memora Sync Log` DocType to prevent unbounded table growth.

## Why Cleanup Instead of Archive

`Memora Sync Log` records operational sync events (wallet, progress, memory syncs
from Redis → MariaDB). This data is useful for short-term troubleshooting only.
It has no analytics value and is not sent to the analytics server. Keeping it
beyond a short window wastes storage with no benefit.

## Default Retention Policy

| Parameter         | Default |
|-------------------|---------|
| `retention_days`  | 7       |
| `batch_size`      | 500     |

Rows with `creation < now() - retention_days` are deleted.
Rows at the exact boundary are **kept** (strictly older rows only are removed).

## Batch Deletion Strategy

The task does not issue one large DELETE. Instead:

1. Select up to `batch_size` rows where `creation < cutoff`, ordered by
   `creation ASC, name ASC` (oldest first).
2. Delete that batch.
3. Commit.
4. Repeat until no eligible rows remain.

This keeps lock scope small, preserves partial progress on failure, and makes
reruns safe.

## Scheduler Entry

Registered in `memora_admin/hooks.py`:

```python
"0 5 * * *": ["memora_admin.tasks.sync_log_cleanup.cleanup_sync_logs"],
```

Runs once daily at **05:00**.

## Manual Execution

From bench:

```bash
bench --site <site> execute memora_admin.tasks.sync_log_cleanup.cleanup_sync_logs
```

With custom parameters:

```python
# From a bench console or script
from memora_admin.tasks.sync_log_cleanup import cleanup_sync_logs
cleanup_sync_logs(triggered_by="Manual", retention_days=14, batch_size=200)
```

## Configurable Parameters

| Parameter        | Type  | Default | Description                              |
|------------------|-------|---------|------------------------------------------|
| `triggered_by`   | str   | `"Scheduler"` | Logged in Task Run Log              |
| `retention_days` | int   | `7`     | How many days of rows to keep            |
| `batch_size`     | int   | `500`   | Rows deleted per batch                   |

## Expected Logs / Metrics

On each run, the task logs:

- Start: `sync_log_cleanup: starting (retention_days=7, batch_size=500)`
- Finish: `sync_log_cleanup: done — N rows deleted in B batches (X.XXs)`
- Per batch (DEBUG): `sync_log_cleanup: deleted batch of N rows`

Prometheus metrics emitted:

| Metric                             | Label                          |
|------------------------------------|--------------------------------|
| `memora_task_runs_total`           | `task_name=sync_log_cleanup`   |
| `memora_task_duration_seconds`     | `task_name=sync_log_cleanup`   |
| `memora_task_users_processed_total`| `task_name=sync_log_cleanup`   |

A `Memora Task Run Log` document is created after each run with status, row
counts, batch count, duration, and trigger source.

## Failure and Rerun Behavior

If a batch fails mid-run:

- All previously committed batches remain deleted (no rollback).
- The task logs `Failed` to Task Run Log and notifies admins.
- The exception is re-raised (no false success reported).
- The next scheduled run or manual rerun will pick up remaining eligible rows
  from where cleanup left off.

The task is fully idempotent — running it multiple times is always safe.
