# Contract: Archive Job Scheduler

## Module

`archive_executor/scheduler.py`

## Entry Point

```
python -m archive_executor.scheduler --archive-type interaction_log --retention-days 14
```

Or programmatically:
```python
from archive_executor.scheduler import create_pending_jobs
from archive_executor.config import Config

config = Config.from_env()
created = create_pending_jobs(config, archive_type="interaction_log", retention_days=14)
# Returns: list of created job names
```

## Behavior

1. Load archive type schema from YAML registry (`interaction_log.v1.yaml`)
2. Query source table for date range of unarchived data:
   ```sql
   SELECT MIN(timestamp) AS min_ts, MAX(timestamp) AS max_ts
   FROM `tabMemora Interaction Log`
   ```
3. Compute archive window: `[min_ts, NOW() - retention_days)`
4. For each day in the archive window:
   - Check if a non-Failed job already exists for this (source_doctype, archive_scope=date, schema_version):
     ```sql
     SELECT 1 FROM `tabMemora Archive Job`
     WHERE source_doctype = 'Memora Interaction Log'
       AND archive_scope = %s
       AND schema_version = 'v1'
       AND status NOT IN ('Failed')
     ```
   - If no job exists, INSERT a new Pending job with `job_meta` populated from the YAML schema

## Output (JSON to stdout)

```json
{
  "archive_type": "interaction_log",
  "retention_days": 14,
  "jobs_created": 3,
  "jobs_skipped": 11,
  "date_range": ["2026-02-20", "2026-02-25"],
  "job_names": ["ARCH-00100", "ARCH-00101", "ARCH-00102"]
}
```

## Cron Integration

```cron
# Create pending jobs at 01:30, then run executor at 02:00
30 1 * * * /opt/memora-archive/venv/bin/python -m archive_executor.scheduler --archive-type interaction_log --retention-days 14
0  2 * * * /opt/memora-archive/venv/bin/python -m archive_executor.run
```
