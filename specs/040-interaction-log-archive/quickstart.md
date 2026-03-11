# Quickstart: Interaction Log Archiving

## Prerequisites

- Existing archive executor environment (`/opt/memora-archive/venv/`)
- Practice Log archiving already operational
- Analytics server reachable via SSH

## Implementation Order

### Phase 1: Schema & Configuration (no code changes to executor)

1. Create `archive_schemas/archive_types/interaction_log.v1.yaml`
2. Create `archive_schemas/dimensions/lesson.v1.yaml`
3. Add index on `tabMemora Interaction Log`.`timestamp` if not present
4. Validate registry: `python -m archive_executor.schemas validate`

### Phase 2: Generic DQ Validator

1. Add `validate_fact_quality_generic()` to `validator.py`
2. Add DQ rule engine supporting: not_null, enum_values, min_value, scope_range, referential, unique_key
3. Wire into `run.py` — check for `dq_rules` in archive type YAML, dispatch accordingly
4. Test with unit tests using mock Parquet data

### Phase 3: Pipeline Generalization

1. Update `run.py` to load archive type YAML and pass DQ rules to validator
2. Verify dimension export works for lesson (existing `export_dimension()` is generic)
3. Verify purge works for single-PK table (existing purge uses date-range DELETE, should work)
4. Integration test: create a Pending job for Interaction Log, run executor, verify Exported output

### Phase 4: Scheduler

1. Create `archive_executor/scheduler.py`
2. Implement `create_pending_jobs()` with date-range scanning and job creation
3. Add CLI entry point (`__main__` style)
4. Test: verify jobs are created for correct date ranges, skipping existing jobs

### Phase 5: Analytics Extensions

1. Extend analytics CLI with `refresh-aggregates` and `refresh-recent` commands
2. Add post-ingestion refresh calls to executor (best-effort, non-blocking)
3. Create analytics-side tables: `interaction_log_raw`, `interaction_log_recent`, `interaction_log_daily_agg`, `interaction_log_monthly_agg`

### Phase 6: Integration Testing

1. End-to-end test: scheduler → executor → transfer → ingest → refresh → purge
2. Verify 0 duplicates after retry
3. Verify aggregates match raw data
4. Verify recent layer contains only 90-day window

## Quick Verification Commands

```bash
# Validate schema registry
SCHEMA_REGISTRY_PATH=/path/to/archive_schemas python -c "
from archive_executor.schemas import validate_registry
errors = validate_registry('/path/to/archive_schemas')
print('OK' if not errors else errors)
"

# Create pending jobs (dry run — just check what would be created)
DB_HOST=... python -m archive_executor.scheduler \
  --archive-type interaction_log --retention-days 14

# Run executor (processes all Pending jobs)
DB_HOST=... python -m archive_executor.run

# Check job status
mysql -h 127.0.0.1 -u USER -pPASS DB -e "
  SELECT name, status, row_count, execution_stage
  FROM \`tabMemora Archive Job\`
  WHERE source_doctype='Memora Interaction Log'
  ORDER BY creation DESC LIMIT 10;
"
```

## Key Files to Modify

| File | Change |
|------|--------|
| `archive_schemas/archive_types/interaction_log.v1.yaml` | **NEW** — archive type schema |
| `archive_schemas/dimensions/lesson.v1.yaml` | **NEW** — lesson dimension schema |
| `archive_executor/validator.py` | Add `validate_fact_quality_generic()` |
| `archive_executor/run.py` | Load archive type YAML, dispatch generic DQ validation |
| `archive_executor/scheduler.py` | **NEW** — job creation scheduler |
| `archive_executor/ingestion.py` | Add `refresh_aggregates()`, `refresh_recent()` |
| `archive_executor/tests/conftest.py` | Add interaction log test constants and fixtures |
| `archive_executor/tests/test_interaction_log_pipeline.py` | **NEW** — integration tests |
