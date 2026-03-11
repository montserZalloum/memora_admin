# Quickstart: Memory State Archive Lifecycle

## Prerequisites

- Existing archive executor environment (`/opt/memora-archive/venv/`)
- Practice Log and Interaction Log archiving already operational
- Analytics server reachable via SSH
- `tabMemora Memory State` is RANGE-partitioned by `season_seq` (setup.py handles this)

## Implementation Order

### Phase 1: Schema & Sync Infrastructure

1. Create `archive_schemas/archive_types/memory_state.v1.yaml`
2. Validate registry: `python -c "from archive_executor.schemas import ..."`
3. Add `sync_state_path` and `sync_output_path` to `config.py`
4. Create `archive_executor/sync.py` — incremental sync engine

### Phase 2: Season Scheduler

1. Extend `archive_executor/scheduler.py` with `create_season_archive_jobs()`
2. Add `--mode season` CLI argument
3. Add `_build_season_job_meta()` helper (distinct from date-based `_build_job_meta()`)

### Phase 3: Exporter Adaptation for Season-Scoped Jobs

1. Modify `exporter.py` `export_fact_data()` to handle `filter_type=season` in `job_meta`
   - When `filter_type=season`: bind single `season_seq` parameter to fact_sql
   - When `filter_type` absent/date: existing range-based behavior (backward compatible)
2. Verify dimension export works for player dimension (already generic)

### Phase 4: Safety Gates

1. Create `archive_executor/safety_gates.py`
2. Implement 4 gates: archive validation, player linkage, plan linkage, partition exists
3. Unit tests for each gate with mock DB responses

### Phase 5: Purge via DROP PARTITION

1. Extend `archive_executor/purge.py` with `_purge_partition()` function
2. Detection: if `job_meta.query_filter.filter_type == "season"`, use DROP PARTITION
3. Safety gate check before DROP
4. Audit log entry after DROP

### Phase 6: Analytics-Side Extensions

1. Extend `handoff` command with `--season-seq` mode
2. Add `mirror-status` command
3. Create `memory_state_current` DuckDB table
4. Ensure `ingest-live` upserts correctly for composite PK `(name, season_seq)`

### Phase 7: Integration Testing

1. Test incremental sync: seed data → sync → verify mirror → modify data → re-sync → verify upsert
2. Test season archive: end season → scheduler creates job → executor archives → validate
3. Test safety gates: verify blocked when player linked, allowed when cleared
4. Test DROP PARTITION: verify partition gone, audit log written
5. End-to-end: active season → sync → end season → archive → mirror cleanup → safety gates → DROP PARTITION

## Quick Verification Commands

```bash
# Validate schema registry
SCHEMA_REGISTRY_PATH=/path/to/archive_schemas python -c "
from archive_executor.schemas import validate_registry
errors = validate_registry('/path/to/archive_schemas')
print('OK' if not errors else errors)
"

# Run incremental sync
DB_HOST=... SYNC_STATE_PATH=./sync_state python -m archive_executor.sync \
  --archive-type memory_state

# Create season archive jobs
DB_HOST=... python -m archive_executor.scheduler \
  --archive-type memory_state --mode season

# Run executor (processes all Pending jobs including Memory State)
DB_HOST=... python -m archive_executor.run

# Check job status
mysql -h 127.0.0.1 -u USER -pPASS DB -e "
  SELECT name, status, archive_scope, row_count, execution_stage
  FROM \`tabMemora Archive Job\`
  WHERE source_doctype='Memora Memory State'
  ORDER BY creation DESC LIMIT 10;
"

# Check sync checkpoint
cat sync_state/memory_state/season_3.json

# Check partitions
mysql -h 127.0.0.1 -u USER -pPASS DB -e "
  SELECT PARTITION_NAME, TABLE_ROWS
  FROM INFORMATION_SCHEMA.PARTITIONS
  WHERE TABLE_NAME = 'tabMemora Memory State'
  ORDER BY PARTITION_ORDINAL_POSITION;
"

# Check active linkages for a season
mysql -h 127.0.0.1 -u USER -pPASS DB -e "
  SELECT COUNT(*) AS active_players
  FROM \`tabMemora Player Profile\`
  WHERE season = 'SEAS-00003';
  SELECT COUNT(*) AS active_plans
  FROM \`tabMemora Academic Plan\`
  WHERE season = 'SEAS-00003' AND is_published = 1;
"
```

## Key Files

| File | Change |
|------|--------|
| `archive_schemas/archive_types/memory_state.v1.yaml` | **NEW** — archive type schema |
| `archive_executor/sync.py` | **NEW** — incremental sync engine |
| `archive_executor/safety_gates.py` | **NEW** — pre-cleanup safety checks |
| `archive_executor/scheduler.py` | **MODIFY** — add season-based scheduling |
| `archive_executor/config.py` | **MODIFY** — add sync config variables |
| `archive_executor/exporter.py` | **MODIFY** — handle season-scoped export |
| `archive_executor/purge.py` | **MODIFY** — add DROP PARTITION path |
| `archive_executor/run.py` | **MODIFY** — integrate season-scoped flow |
| `archive_executor/ingestion.py` | **MODIFY** — add season-based handoff |
| `archive_executor/tests/conftest.py` | **MODIFY** — add Memory State fixtures |
| `archive_executor/tests/test_memory_state_sync.py` | **NEW** — sync tests |
| `archive_executor/tests/test_memory_state_archive.py` | **NEW** — archive + purge tests |
| `archive_executor/tests/test_safety_gates.py` | **NEW** — safety gate tests |
