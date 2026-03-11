# Implementation Plan: Interaction Log Archiving

**Branch**: `040-interaction-log-archive` | **Date**: 2026-03-11 | **Spec**: [spec.md](./spec.md)
**Input**: Feature specification from `/specs/040-interaction-log-archive/spec.md`

## Summary

Extend the existing archive executor pipeline to support the Interaction Log DocType (`tabMemora Interaction Log`). The pipeline already handles Practice Log archiving through YAML-configured schema definitions. This feature adds a new archive type schema (`interaction_log.v1`), a new lesson dimension, a generic DQ validation engine, a job scheduler for automated daily archiving, and analytics-side extensions for aggregation and a recent detailed layer.

The core pipeline (Pending → Processing → Exported → Transferred → Ingested → Completed → Purged) remains unchanged. The primary work is: (1) YAML schemas for the new doctype, (2) generalizing the hardcoded Practice Log DQ validator, (3) adding a scheduler to create daily archive jobs, and (4) analytics-side tables and CLI commands for aggregates and the 90-day recent layer.

## Technical Context

**Language/Version**: Python 3.11+ (standalone executor, no Frappe runtime)
**Primary Dependencies**: PyArrow (Parquet), PyMySQL, PyYAML, rsync/SSH (transfer)
**Storage**: MariaDB (production source), DuckDB (analytics target), Parquet (intermediate files)
**Testing**: pytest with `@pytest.mark.integration` marker for DB-dependent tests
**Target Platform**: Linux server (cron-scheduled)
**Project Type**: Single project (CLI executor)
**Performance Goals**: Archive 1M interaction records per daily run within 2 hours (export → ingestion, excluding deletion)
**Constraints**: Zero data loss, zero duplicates on analytics, batched deletion (10K rows, 2s sleep), no impact on concurrent production reads/writes
**Scale/Scope**: Hundreds of millions of interaction records per year; 14-day production retention window

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

Constitution is unpopulated (template only) — no gates to check. Proceeding.

**Post-design re-check**: No constitution violations. The design extends existing patterns without introducing new architectural layers or dependencies.

## Project Structure

### Documentation (this feature)

```text
specs/040-interaction-log-archive/
├── plan.md              # This file
├── research.md          # Phase 0: Research decisions (8 topics)
├── data-model.md        # Phase 1: Entity models and analytics tables
├── quickstart.md        # Phase 1: Implementation guide
├── contracts/
│   ├── archive-type-schema.yaml     # interaction_log.v1 YAML contract
│   ├── lesson-dimension-schema.yaml # lesson.v1 dimension contract
│   ├── analytics-cli-extensions.md  # New analytics CLI commands
│   ├── scheduler-interface.md       # Job scheduler contract
│   └── dq-validation-contract.md    # Generic DQ engine contract
└── tasks.md             # Phase 2 output (/speckit.tasks)
```

### Source Code (repository root)

```text
archive_executor/
├── run.py                  # MODIFY — load archive type YAML, dispatch generic DQ
├── validator.py            # MODIFY — add validate_fact_quality_generic()
├── ingestion.py            # MODIFY — add refresh_aggregates(), refresh_recent()
├── scheduler.py            # NEW — daily job creation scheduler
├── __main__.py             # EXISTING (no change)
└── tests/
    ├── conftest.py                          # MODIFY — add interaction log fixtures
    ├── test_interaction_log_pipeline.py      # NEW — integration tests
    └── test_generic_dq_validator.py          # NEW — unit tests for DQ engine

archive_schemas/
├── archive_types/
│   ├── practice_log.v1.yaml       # EXISTING (no change)
│   └── interaction_log.v1.yaml    # NEW
└── dimensions/
    ├── player.v3.yaml             # EXISTING (reused)
    ├── lesson.v1.yaml             # NEW
    ├── season.v1.yaml             # EXISTING (reused)
    └── plan.v1.yaml               # EXISTING (reused)
```

**Structure Decision**: Extends the existing `archive_executor/` and `archive_schemas/` structure. No new top-level directories. The executor is a standalone Python package run via cron outside Frappe.

## Key Design Decisions

### D-01: Generic DQ Validation via YAML Rules (Research R-01)

The current `validate_fact_quality()` is hardcoded for Practice Log's 16 rules. A new `validate_fact_quality_generic()` function reads DQ rules from the archive type YAML schema and applies them using a rule engine. Rule types: `not_null`, `enum_values`, `min_value`, `scope_range`, `referential`, `unique_key`. The existing function is preserved for backward compatibility — Practice Log continues to use it until migrated.

### D-02: Interaction Log as Standard Frappe DocType (Research R-02)

Unlike Practice Log (custom table, composite PK), Interaction Log uses Frappe's auto-named `name` field as the single PK and deduplication key. Scope column is `timestamp`. The purge module's date-range DELETE pattern works without modification.

### D-03: Lesson Dimension (Research R-03)

New `lesson.v1` dimension captures lesson metadata (title, topic, subject, track, unit, XP, flags) via a JOIN to `tabMemora Topic`. Reuses the existing dimension export infrastructure.

### D-04: Analytics-Side Aggregation (Research R-04)

Two new CLI commands on the analytics server: `refresh-aggregates` and `refresh-recent`. Called by the executor after ingestion as best-effort (non-blocking). Analytics tables: `interaction_log_raw`, `interaction_log_recent` (90-day), `interaction_log_daily_agg`, `interaction_log_monthly_agg`.

### D-05: Job Scheduler (Research R-06)

New `scheduler.py` module scans for unarchived date ranges and creates one Pending job per day. Uses the UNIQUE KEY constraint on `(source_doctype, archive_scope, schema_version)` to prevent duplicates. Runs via cron 30 minutes before the executor.

### D-06: Backward Compatibility

All changes are additive. The executor's main loop already iterates over all Pending jobs regardless of `source_doctype`. The archive type YAML lookup (`archive_type_key`) already reads from the job's `archive_type` field. No changes to Practice Log behavior.

## Risk Assessment

| Risk | Impact | Mitigation |
|------|--------|------------|
| Interaction Log table missing `timestamp` index | Slow queries on large tables | Add index as first task; verify with EXPLAIN |
| Generic DQ validator edge cases | False positives/negatives in validation | Comprehensive unit tests with mock Parquet data |
| Analytics CLI extension deployment timing | Executor calls commands that don't exist yet | Best-effort calls with graceful fallback on failure |
| High-volume table (hundreds of millions/year) | Memory pressure during export | Existing streaming cursor + chunked writes handle this |

## Complexity Tracking

No constitution violations to justify.
