# Contract: Analytics CLI Extensions for Interaction Log

## Existing Commands (unchanged)

```
memora-analytics ingest-archive --batch-dir DIR
memora-analytics ingest-live --batch-dir DIR
memora-analytics handoff --archive-batch-dir DIR --date-column COL --from DATE --to DATE
memora-analytics verify
```

## New Commands

### refresh-aggregates

Rebuilds daily and monthly aggregate tables from the historical raw layer.

```
memora-analytics refresh-aggregates --archive-type interaction_log
```

**Input**: Archive type identifier.

**Behavior**:
1. Rebuilds `interaction_log_daily_agg` from `interaction_log_raw`:
   - Groups by: `CAST(timestamp AS DATE)`, `player`, `lesson`, `event_type`
   - Metrics: `COUNT(*)`, `SUM(time_spent)`, `SUM(errors_count)`, `COUNT(*) FILTER (WHERE event_type = 'Completed')`, `COUNT(*)`
2. Rebuilds `interaction_log_monthly_agg` from `interaction_log_raw`:
   - Groups by: `DATE_TRUNC('month', timestamp)`, `player`, `lesson`, `event_type`
   - Same metrics as daily

**Output (JSON)**:
```json
{
  "status": "ok",
  "daily_rows": 42500,
  "monthly_rows": 8200,
  "duration_ms": 1250
}
```

**Exit codes**: 0 = success, 1 = failure

---

### refresh-recent

Rebuilds the recent detailed layer (90-day rolling window).

```
memora-analytics refresh-recent --archive-type interaction_log [--window-days 90]
```

**Input**: Archive type, optional window size (default 90).

**Behavior**:
1. `CREATE OR REPLACE TABLE interaction_log_recent AS SELECT * FROM interaction_log_raw WHERE timestamp >= CURRENT_DATE - INTERVAL {window_days} DAY`

**Output (JSON)**:
```json
{
  "status": "ok",
  "row_count": 156000,
  "window_days": 90,
  "oldest_record": "2025-12-12T00:00:00",
  "duration_ms": 800
}
```

**Exit codes**: 0 = success, 1 = failure

---

## Modified Flow in Executor

After successful ingestion (before marking Completed), the executor calls:
1. `memora-analytics refresh-recent --archive-type interaction_log`
2. `memora-analytics refresh-aggregates --archive-type interaction_log`

Both are called via SSH using the same `_run_ssh_command()` pattern as ingestion.

If refresh fails, the job still proceeds to Completed (refresh is best-effort, not a blocking gate). Failure is logged as a warning.
