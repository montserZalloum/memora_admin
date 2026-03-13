# Contract: Analytics CLI Extensions for Memory State

## Existing Commands (reused as-is)

```
memora-analytics ingest-archive --batch-dir DIR
memora-analytics ingest-live --batch-dir DIR
memora-analytics verify
```

These commands are already generic enough to handle Memory State data. The analytics side reads the Parquet schema to determine table structure.

## Modified Command: handoff

The existing `handoff` command is extended to support season-based cleanup of the current mirror.

### Existing interface (date-based):
```
memora-analytics handoff --archive-batch-dir DIR --date-column COL --from DATE --to DATE
```

### Extended interface (season-based):
```
memora-analytics handoff --archive-batch-dir DIR --season-seq N --archive-type memory_state
```

**Behavior for season mode**:
1. `DELETE FROM memory_state_current WHERE season_seq = N`
2. Return count of rows removed

**Output (JSON)**:
```json
{
  "status": "ok",
  "mode": "season",
  "season_seq": 3,
  "rows_removed": 125000,
  "duration_ms": 450
}
```

**Backward compatibility**: The existing date-based mode continues to work unchanged. The command detects the mode from the presence of `--season-seq` vs `--from`/`--to`.

## ~~New Command: mirror-status~~ (Removed from production contract)

> `mirror-status` is an analytics-only utility for manual monitoring. It is not called by the production executor pipeline and is not part of the production-to-analytics integration contract.
```

## Modified Executor Flow for Memory State

### During Incremental Sync (sync.py)
1. Export Parquet
2. Transfer via rsync
3. Call `ingest-live` — upserts into `memory_state_current`
4. Update checkpoint

### During Season Archive (run.py)
1. Export full season Parquet
2. Validate (DQ + file checks)
3. Transfer via rsync
4. Call `ingest-archive` — stores in archive Parquet hierarchy
5. Call `handoff --season-seq N --archive-type memory_state` — removes from current mirror
6. Call `verify` — confirms archive integrity
7. Mark Completed

### During Purge (purge.py)
1. Check safety gates
2. `DROP PARTITION p_season_N` (production-side, no analytics CLI call needed)
3. Audit log
4. Mark Purged
