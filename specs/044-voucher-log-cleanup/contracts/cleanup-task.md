# Contract: Voucher Redemption Log Cleanup Task

No external API — this is an internal scheduled task.

## Function Signature

```python
def cleanup_voucher_redemption_logs(
    triggered_by: str = "Scheduler",
    retention_days: int = 100,
    batch_size: int = 1000,
) -> None
```

## Internal Function

```python
def _do_voucher_log_cleanup(
    retention_days: int = 100,
    batch_size: int = 1000,
) -> tuple[int, int]  # (total_deleted, batches_executed)
```

## Scheduler Registration

```python
# hooks.py scheduler_events["cron"]
"30 5 * * *": ["memora_admin.tasks.voucher_log_cleanup.cleanup_voucher_redemption_logs"]
```

## Behavior Contract

- Deletes rows where `creation < NOW() - INTERVAL {retention_days} DAY`
- Orders by `creation ASC, name ASC`
- Batches of `batch_size` rows, `frappe.db.commit()` after each
- Returns `(0, 0)` when no eligible rows exist
- Raises on error after logging; already-committed batches persist
