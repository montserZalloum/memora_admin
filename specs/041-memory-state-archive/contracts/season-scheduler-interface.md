# Contract: Season Archive Scheduler

## Module

`archive_executor/scheduler.py` (extend existing module)

## Entry Point

```
python -m archive_executor.scheduler --archive-type memory_state --mode season
```

Or programmatically:
```python
from archive_executor.scheduler import create_season_archive_jobs
from archive_executor.config import Config

config = Config.from_env()
created = create_season_archive_jobs(config, archive_type="memory_state")
# Returns: list of created job names
```

## Behavior

1. Load archive type schema from YAML registry (`memory_state.v1.yaml`)
2. Verify `scope_type == "season"` in the schema
3. Query for archive-eligible seasons:
   ```sql
   SELECT s.name AS season_name, s.season_seq, s.end_date
   FROM `tabMemora Season` s
   WHERE s.end_date < CURDATE()
     AND NOT EXISTS (
       SELECT 1 FROM `tabMemora Archive Job` aj
       WHERE aj.source_doctype = 'Memora Memory State'
         AND aj.archive_scope = CONCAT('season_', s.season_seq)
         AND aj.schema_version = 'v1'
         AND aj.status NOT IN ('Failed')
     )
   ORDER BY s.season_seq
   ```
4. For each eligible season:
   - Generate archive_scope: `season_{season_seq}` (e.g., `season_3`)
   - Build job_meta with season_seq, season_name, and fact_sql
   - Insert Pending archive job

## job_meta for Season Jobs

```json
{
  "query_filter": {
    "season_seq": 3,
    "season_name": "SEAS-00003",
    "filter_column": "season_seq",
    "filter_type": "season"
  },
  "export_columns": ["name", "season_seq", "subject", "player", "item_id", "..."],
  "schema_snapshot": { "..." },
  "related_tables": [ "..." ],
  "fact_sql": { "filtered": "...", "incremental": "..." },
  "scope_column": "season_seq"
}
```

## Output (JSON to stdout)

```json
{
  "archive_type": "memory_state",
  "mode": "season",
  "jobs_created": 2,
  "jobs_skipped": 1,
  "eligible_seasons": [
    {"season_name": "SEAS-00002", "season_seq": 2, "end_date": "2025-12-31"},
    {"season_name": "SEAS-00003", "season_seq": 3, "end_date": "2026-02-28"}
  ],
  "job_names": ["ARCH-00050", "ARCH-00051"]
}
```

## Cron Integration

```cron
# Create season archive jobs daily at 01:00
0 1 * * * /opt/memora-archive/venv/bin/python -m archive_executor.scheduler --archive-type memory_state --mode season

# Run incremental sync every 15 minutes
*/15 * * * * /opt/memora-archive/venv/bin/python -m archive_executor.sync --archive-type memory_state

# Run executor at 02:00 (processes Pending archive jobs)
0 2 * * * /opt/memora-archive/venv/bin/python -m archive_executor.run
```

## CLI Arguments

| Argument | Required | Default | Description |
|----------|----------|---------|-------------|
| `--archive-type` | Yes | — | Archive type key (e.g., `memory_state`) |
| `--mode` | No | `date` | `date` for existing date-based scheduling, `season` for season-based |
| `--retention-days` | No* | 14 | Required when `--mode=date`, ignored for `--mode=season` |

*Required for date mode, not used for season mode (archive eligibility is determined by `end_date`).
