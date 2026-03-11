# Implementation Plan: Production Archival and Purge for Memora Task Run Log

**Branch**: `042-task-log-archive` | **Date**: 2026-03-11 | **Spec**: [spec.md](./spec.md)
**Input**: Feature specification from `/specs/042-task-log-archive/spec.md`

## Summary

Implement a two-task Frappe pipeline to archive and safely purge `tabMemora Task Run Log` records. The archive task acts as a daily scheduler: it creates `tabMemora Archive Job` records for eligible date windows (rows with terminal status and `completed_at` older than 90 days), creates linked `Memora Task Log Archive Batch` tracker records, and monitors archive jobs to transition batches from `Exported → Synced` when analytics confirms ingestion. A separate purge task deletes `Synced` batch rows from production using select-then-delete in sub-batches of 10,000 with lock timeout safety and a 300-second runtime cap.

This feature adds a new archive type (`task_run_log`) to the existing archive_executor pipeline and introduces the `Memora Task Log Archive Batch` DocType as the source-side lifecycle tracker.

## Technical Context

**Language/Version**: Python 3.11+ (archive_executor, standalone); Frappe/Python (tasks)
**Primary Dependencies**: PyArrow (Parquet), PyMySQL, archive_executor (exporter, scheduler, schemas), Frappe scheduler
**Storage**: MariaDB (source: `tabMemora Task Run Log`), Parquet files (local then remote), DuckDB (analytics)
**Testing**: pytest with integration marker; existing `archive_executor/tests/` pattern
**Target Platform**: Linux server (Frappe scheduler + archive_executor cron)
**Project Type**: Single Frappe app extension + standalone executor enhancement
**Performance Goals**: Archive eligibility query < 1s for 500k rows (SC-008); purge sub-batch < 5s under normal load (SC-004)
**Constraints**: Zero terminal-status rows lost (SC-002); zero rows within 90-day window deleted (SC-003); 300-second runtime cap (SC-005)
**Scale/Scope**: Up to 50,000 rows per archive batch (FR-004); up to 10,000 rows per purge sub-batch (FR-005)

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

Constitution is unpopulated (template only) — no gates to check. Proceeding.

**Post-design re-check**: No violations. The design extends existing patterns (archive_executor pipeline, YAML schema registry, Frappe task utilities) without introducing new architectural layers or external dependencies.

## Project Structure

### Documentation (this feature)

```text
specs/042-task-log-archive/
├── plan.md              # This file
├── research.md          # Phase 0: 9 research decisions
├── data-model.md        # Phase 1: Entities, state machine, index
├── quickstart.md        # Phase 1: Implementation guide + verification
├── contracts/
│   ├── task_run_log.v1.yaml          # Archive YAML schema contract
│   ├── archive-task-interface.md     # Task function signatures + scheduler hooks
│   └── batch-doctype.md              # DocType field contract + permissions
└── tasks.md             # Phase 2 output (/speckit.tasks — NOT created here)
```

### Source Code (repository root)

```text
archive_schemas/
└── archive_types/
    └── task_run_log.v1.yaml          # NEW — archive type schema

memora_admin/
├── memora_admin/
│   ├── doctype/
│   │   └── memora_task_log_archive_batch/    # NEW DocType
│   │       ├── memora_task_log_archive_batch.json
│   │       └── memora_task_log_archive_batch.py
│   └── setup.py                              # MODIFY — add covering index in before_migrate
├── tasks/
│   ├── archive_task_log.py                   # NEW — archive scheduler + status sync
│   └── purge_task_log.py                     # NEW — purge Synced batches
└── hooks.py                                  # MODIFY — add 2 new cron entries

archive_executor/
└── tests/
    └── test_task_log_pipeline.py             # NEW — integration tests
```

**Structure Decision**: Additive extension of existing patterns. No new top-level directories. No changes to archive_executor core modules (exporter.py, run.py, purge.py) — the new archive type plugs in via the YAML schema registry.

## Key Design Decisions

### D-01: Integration with Existing Analytics Pipeline (Research R-02)

The archive task does NOT reimplement export/transfer/ingest logic. It creates `tabMemora Archive Job` records which the existing archive_executor cron processes through its full pipeline (Pending → Processing → Exported → Transferred → Ingested → Completed). The `Memora Task Log Archive Batch` is a lightweight source-side tracker.

**Batch ↔ Job lifecycle mapping**:
- Archive task creates job → batch created in `Pending`
- Archive executor claims job → batch remains `Pending`
- Archive executor completes export → batch transitions to `Exported` (detected by monitoring loop)
- Archive executor completes ingestion → batch transitions to `Synced`
- Purge task deletes source rows → batch transitions to `Purged`

The monitoring loop runs inside the daily archive task (Phase 1 of each run).

### D-02: Terminal Status Set (Research R-01)

The DocType defines `status` options as `Success`, `Failed`, `Partial`. The spec's `Skipped` does not exist in the DocType. Implementation uses `('Success', 'Failed', 'Partial')` as the terminal set throughout (archive eligibility query, fact_sql, purge SELECT).

### D-03: Date-Scoped Archive (Research R-08)

Like `interaction_log`, task run logs are date-scoped: one archive job per completed day. The scope column is `completed_at`. The scheduler's `create_pending_jobs()` function is reused with `archive_type="task_run_log"` and `retention_days=90`.

No dimensions are needed (task run logs are self-contained; no player/lesson FK).

### D-04: Select-then-Delete Purge Pattern (Research R-03)

The purge task uses select-then-delete (not the existing `purge.py`'s direct DELETE). Each 10,000-row sub-batch:
1. `SELECT name ... WHERE status IN (...) AND completed_at IN range AND completed_at < cutoff LIMIT 10000`
2. `DELETE WHERE name IN (...)`

The retention window guard is re-applied at purge time (SC-003).

### D-05: innodb_lock_wait_timeout = 5 (Research R-04)

Each purge sub-batch uses a fresh DB connection with `SET SESSION innodb_lock_wait_timeout = 5` immediately after acquire.

### D-06: Covering Index (Research R-06)

`(status, completed_at, name)` on `tabMemora Task Run Log` — created in `setup.py / before_migrate` using `ADD INDEX IF NOT EXISTS`. This is idempotent and safe for repeated migrations.

### D-07: Runtime Cap (Research R-09)

Both tasks capture `start_time = time.monotonic()` and check `time.monotonic() - start_time >= 300` after each batch. Remaining work is deferred to the next daily run.

## Risk Assessment

| Risk | Impact | Mitigation |
|------|--------|------------|
| Terminal status discrepancy (spec vs DocType) | Silent exclusion of rows | Use DocType values (`Partial` not `Skipped`); define as a named constant |
| Archive executor not running when archive task schedules | Batches stuck in Exported indefinitely | Archive monitor (`archive_monitor.py`) already alerts on jobs stuck >48h; batch monitoring loop detects stuck jobs and can alert |
| Purge deletes rows within retention window | Data loss (SC-003 violation) | Retention guard in SELECT re-applied at purge time even for Synced batches |
| `Memora Task Log Archive Batch` row not created (job creation succeeds but batch insert fails) | Orphaned archive job without batch tracker | Create batch in same transaction or with retry; if batch creation fails, also rollback job creation |
| Large backlog of eligible rows (first run) | Many archive jobs created, executor backlog | Scheduler creates jobs per day; executor processes one at a time; acceptable backlog |
| Parquet file missing when purge task runs | Purge blocked forever | Purge task checks `file_path` is a real directory before proceeding (matching existing purge.py pattern) |

## Complexity Tracking

No constitution violations to justify.
