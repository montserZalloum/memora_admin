# Contract: Incremental Sync Pipeline

## Module

`archive_executor/sync.py` (new module)

## Entry Point

```
python -m archive_executor.sync --archive-type memory_state
```

Or programmatically:
```python
from archive_executor.sync import run_incremental_sync
from archive_executor.config import Config

config = Config.from_env()
results = run_incremental_sync(config, archive_type="memory_state")
# Returns: list of per-season sync results
```

## Behavior

### Step 1: Discover Active Seasons

```sql
SELECT s.name AS season_name, s.season_seq
FROM `tabMemora Season` s
WHERE s.end_date >= CURDATE()
  AND s.is_published = 1
ORDER BY s.season_seq
```

### Step 2: For Each Active Season

1. **Load checkpoint**: Read `{sync_state_path}/memory_state/season_{N}.json`
   - If no checkpoint exists, initialize with `last_checkpoint = "1970-01-01T00:00:00"` (first sync will extract all rows)

2. **Compute extraction window**:
   ```
   extract_from = last_checkpoint - safety_overlap  (default overlap: 5 minutes)
   ```

3. **Extract changed rows**:
   ```sql
   SELECT ... FROM `tabMemora Memory State` ms
   WHERE ms.season_seq = %s AND ms.modified >= %s
   ORDER BY ms.modified
   ```
   Parameters: `(season_seq, extract_from)`
   Uses streaming cursor for memory efficiency.

4. **Skip if no rows**: If 0 rows extracted, update checkpoint timestamp and continue to next season.

5. **Export to Parquet**: Write to `{sync_output_path}/memory_state/season_{N}/sync_{timestamp}.parquet`
   - Include metadata columns: `archive_scope=season_{N}`, `synced_at=NOW()`

6. **Transfer**: rsync to analytics server at `{remote_sync_path}/memory_state/season_{N}/`

7. **Ingest**: Call `memora-analytics ingest-live --batch-dir {remote_path}`
   - Analytics side upserts into `memory_state_current` table
   - Deduplication key: `(name, season_seq)`

8. **Update checkpoint**: Write new checkpoint to JSON file:
   ```json
   {
     "season_seq": N,
     "season_name": "SEAS-NNNNN",
     "last_checkpoint": "<max modified from extracted rows>",
     "last_sync_rows": <count>,
     "total_rows_synced": <cumulative>,
     "last_sync_at": "<now>"
   }
   ```

9. **Cleanup local**: Remove the transferred Parquet file.

### Step 3: Report

Output JSON summary to stdout.

## Output (JSON to stdout)

```json
{
  "archive_type": "memory_state",
  "seasons_synced": 2,
  "seasons_skipped": 0,
  "total_rows_synced": 3450,
  "results": [
    {
      "season_seq": 3,
      "season_name": "SEAS-00003",
      "rows_extracted": 1200,
      "checkpoint": "2026-03-11T15:00:00",
      "status": "ok"
    },
    {
      "season_seq": 4,
      "season_name": "SEAS-00004",
      "rows_extracted": 2250,
      "checkpoint": "2026-03-11T15:00:00",
      "status": "ok"
    }
  ]
}
```

## Configuration (via environment variables)

| Variable | Default | Description |
|----------|---------|-------------|
| `SYNC_STATE_PATH` | `./sync_state` | Base directory for checkpoint files |
| `SYNC_OUTPUT_PATH` | `./sync_output` | Base directory for sync Parquet files |
| `SYNC_OVERLAP_SECONDS` | `300` | Safety overlap in seconds (default 5 min) |
| `SYNC_REMOTE_PATH` | (from config) | Remote path on analytics server |

## Error Handling

- **Extraction failure**: Log error, skip season, do NOT advance checkpoint
- **Transfer failure**: Log error, skip season, do NOT advance checkpoint (Parquet preserved locally for retry)
- **Ingestion failure**: Log error, skip season, do NOT advance checkpoint
- **Partial season failure**: Other seasons continue independently

## Idempotency

The safety overlap ensures that re-running sync after a failure re-extracts the same window plus any new changes. The analytics-side upsert ensures duplicate rows are merged, not appended. Checkpoint is only advanced after successful ingestion.

## Sync Pause for Archive Transition

When a season's archive job is created (season transitions from active to ended):
1. The season scheduler creates the archive job
2. The sync module detects that an archive job exists for this season:
   ```sql
   SELECT 1 FROM `tabMemora Archive Job`
   WHERE source_doctype = 'Memora Memory State'
     AND archive_scope = CONCAT('season_', %s)
     AND schema_version = 'v1'
     AND status NOT IN ('Failed')
   ```
3. If found, sync is paused for this season (skipped in sync loop)
4. The archive pipeline handles the final full export
