# Archive Task Interface

## `memora_admin.tasks.archive_task_log`

### Entry Point

```python
def archive_task_log(triggered_by: str = "Scheduler") -> None:
    """Daily archive task for Memora Task Run Log.

    Phase 1 — Sync batch statuses:
      For each Pending/Exported batch with a linked archive job:
        - If archive job is Completed → transition batch to Synced
        - If archive job is Failed (retry_count >= 3) → transition batch to Failed

    Phase 2 — Create new archive jobs:
      Calls scheduler.create_pending_jobs(config, "task_run_log", retention_days)
      For each new archive job created, creates a linked Memora Task Log Archive Batch

    Respects RUNTIME_CAP_SECONDS = 300. Logs to Memora Task Run Log via log_task_run().
    """
```

### Batch Creation Helper

```python
def _create_batch_for_job(job_name: str, source_doctype: str, job_meta: dict) -> str:
    """Create a Memora Task Log Archive Batch linked to an archive job.

    Extracts date_from, date_to, cutoff_date from job_meta.query_filter.
    Returns the new batch name.
    """
```

### Status Sync Helper

```python
def _sync_batch_statuses() -> tuple[int, int]:
    """Scan Exported batches and transition to Synced when archive job is Completed.

    Returns (synced_count, failed_count).
    """
```

---

## `memora_admin.tasks.purge_task_log`

### Entry Point

```python
def purge_task_log(triggered_by: str = "Scheduler") -> None:
    """Daily purge task for confirmed-archived Task Run Log rows.

    For each Synced batch:
      1. SELECT name FROM tabMemora Task Run Log
         WHERE status IN ('Success','Failed','Partial')
           AND completed_at >= date_from AND completed_at < date_to
           AND completed_at < NOW() - INTERVAL retention_days DAY
         ORDER BY completed_at LIMIT 10000
      2. DELETE WHERE name IN (...)  [with innodb_lock_wait_timeout=5]
      3. Commit, sleep 2s, repeat
      4. Transition batch to Purged when no rows remain

    Respects RUNTIME_CAP_SECONDS = 300. Logs to Memora Task Run Log.
    """
```

### Sub-batch Purge Helper

```python
def _purge_sub_batch(
    conn,
    source_table: str,
    date_from: str,
    date_to: str,
    retention_days: int,
    terminal_statuses: tuple[str, ...],
) -> int:
    """Execute one select-then-delete cycle. Returns rows deleted."""
```

---

## Scheduler Hooks (hooks.py additions)

```python
# Daily at 02:00: Archive eligible task run log rows
"0 2 * * *": ["memora_admin.tasks.archive_task_log.archive_task_log"],

# Daily at 03:30: Purge confirmed-archived task run log rows
"30 3 * * *": ["memora_admin.tasks.purge_task_log.purge_task_log"],
```

The archive task runs before the purge task to maximize the window for analytics pipeline processing between archive and purge.
