# Quickstart: Task Log Archive Implementation

## Prerequisites

- Branch: `042-task-log-archive`
- Working directory: `/home/corex/aurevia-bench/apps/memora_admin`
- DB credentials in environment (see MEMORY.md)

## Step 1: Add Archive Schema

Copy `contracts/task_run_log.v1.yaml` to:
```
archive_schemas/archive_types/task_run_log.v1.yaml
```

Verify the archive executor can load it:
```bash
DB_HOST=127.0.0.1 DB_PORT=3306 DB_USER=_9be6802bfff1e8ca \
DB_PASSWORD=zjAACevKaH5VGVP2 DB_NAME=_9be6802bfff1e8ca \
SCHEMA_REGISTRY_PATH=$(pwd)/archive_schemas \
python3 -c "
from archive_executor.schemas import load_archive_type
s = load_archive_type('$(pwd)/archive_schemas', 'task_run_log', 'v1')
print(s['archive_type'], s['source_table'])
"
```

## Step 2: Create the DocType

Create the DocType directory and files:
```
memora_admin/memora_admin/doctype/memora_task_log_archive_batch/
├── memora_task_log_archive_batch.json
└── memora_task_log_archive_batch.py
```

Run `bench migrate` to register the DocType in the DB:
```bash
cd /home/corex/aurevia-bench
bench --site <site> migrate
```

## Step 3: Add Covering Index

The covering index is added in `memora_admin/memora_admin/setup.py` under `before_migrate`:
```python
def before_migrate():
    frappe.db.sql("""
        ALTER TABLE `tabMemora Task Run Log`
          ADD INDEX IF NOT EXISTS `idx_task_log_archive`
            (`status`, `completed_at`, `name`)
    """)
```

Verify index exists:
```sql
SHOW INDEX FROM `tabMemora Task Run Log` WHERE Key_name = 'idx_task_log_archive';
```

## Step 4: Implement Archive Task

Create `memora_admin/tasks/archive_task_log.py` with:
- `archive_task_log(triggered_by="Scheduler")` — main entry point
- `_sync_batch_statuses()` — transitions Exported → Synced
- `_create_batch_for_job()` — creates batch record for new archive job

Register in `hooks.py`:
```python
"0 2 * * *": ["memora_admin.tasks.archive_task_log.archive_task_log"],
```

## Step 5: Implement Purge Task

Create `memora_admin/tasks/purge_task_log.py` with:
- `purge_task_log(triggered_by="Scheduler")` — main entry point
- `_purge_batch(batch, config)` — select-then-delete loop

Register in `hooks.py`:
```python
"30 3 * * *": ["memora_admin.tasks.purge_task_log.purge_task_log"],
```

## Step 6: Run Integration Tests

```bash
DB_HOST=127.0.0.1 DB_PORT=3306 DB_USER=_9be6802bfff1e8ca \
DB_PASSWORD=zjAACevKaH5VGVP2 DB_NAME=_9be6802bfff1e8ca \
SCHEMA_REGISTRY_PATH=$(pwd)/archive_schemas \
ARCHIVE_OUTPUT_PATH=/tmp/memora-archive-test \
python3 -m pytest archive_executor/tests/test_task_log_pipeline.py -v
```

## Verification Checklist

After a full archive + purge cycle in a test environment:

1. **Export**: `Memora Task Log Archive Batch` records exist with status `Exported` or later
2. **Row count**: `row_count` in batch matches actual rows in Parquet file
3. **No over-purge**: Query `tabMemora Task Run Log` for rows within the 90-day window — none should be missing
4. **Status set**: All purged batches have `status = 'Purged'` and `purged_at` populated
5. **Idempotency**: Re-running archive task creates no duplicate archive jobs
6. **Retry**: Set a batch to `Failed`, re-run archive task — batch retries from correct stage
7. **Covering index**: `EXPLAIN SELECT name FROM tabMemora Task Run Log WHERE status='Success' AND completed_at < '2026-01-01'` — shows `Using index`

## Manual Trigger (for testing)

```bash
# In bench console:
bench --site <site> execute memora_admin.tasks.archive_task_log.archive_task_log \
  --kwargs '{"triggered_by": "Manual"}'

bench --site <site> execute memora_admin.tasks.purge_task_log.purge_task_log \
  --kwargs '{"triggered_by": "Manual"}'
```
