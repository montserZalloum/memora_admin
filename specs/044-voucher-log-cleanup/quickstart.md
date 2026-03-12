# Quickstart: Voucher Redemption Log Cleanup

**Feature**: 044-voucher-log-cleanup | **Date**: 2026-03-11

## Overview

A daily scheduled task that deletes `Memora Voucher Redemption Log` rows older than 100 days, processing in batches of 1000 with commit-per-batch for restart safety.

## Files to Create/Modify

| File | Action | Purpose |
|------|--------|---------|
| `memora_admin/tasks/voucher_log_cleanup.py` | Create | Cleanup task implementation |
| `memora_admin/hooks.py` | Modify | Register scheduler entry |
| `memora_admin/tests/test_voucher_log_cleanup.py` | Create | Integration tests |

## Implementation Pattern

Follow `task_log_archive_batch_cleanup.py` exactly:

1. **Wrapper** `cleanup_voucher_redemption_logs()` — registered in hooks, handles logging/metrics/error
2. **Inner** `_do_voucher_log_cleanup()` — batched SELECT → DELETE → COMMIT loop
3. **Constants**: `TASK_NAME`, `DEFAULT_RETENTION_DAYS = 100`, `DEFAULT_BATCH_SIZE = 1000`

## Schedule

`"30 5 * * *"` — Daily at 05:30 server time

## Running Tests

```bash
cd /home/corex/aurevia-bench
bench --site [site] run-tests --app memora_admin --module memora_admin.tests.test_voucher_log_cleanup
```
