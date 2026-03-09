# Implementation Plan: Memora Archive System

**Branch**: `039-archive-system` | **Date**: 2026-03-09 | **Spec**: `specs/039-archive-system/spec.md`
**Input**: Feature specification from `/specs/039-archive-system/spec.md`

## Summary

Season-based data archival system that exports ended-season data (starting with Practice Log, ~500M rows) to self-contained Parquet batches with dimension snapshots. Two-plane architecture: a Frappe DocType (Memora Archive Job) manages job state and admin UI, while a standalone Python executor script (separate virtualenv, cron-triggered) handles the actual export. YAML-based schema registry enables extensibility to new tables without executor code changes.

## Technical Context

**Language/Version**: Python 3.11+ (Frappe v15 for DocType; standalone for executor)
**Primary Dependencies**: Frappe Framework (DocType, hooks, scheduled tasks), pyarrow (Parquet export), pymysql (direct DB access), pyyaml (schema registry)
**Storage**: MariaDB (source of truth via Frappe ORM for DocType, direct SQL for executor), Filesystem (Parquet files at `ARCHIVE_OUTPUT_PATH`)
**Testing**: `frappe.tests.utils.FrappeTestCase` (DocType tests), pytest (executor unit tests)
**Target Platform**: Linux server (same host as Frappe bench)
**Project Type**: Single (backend-only, no frontend beyond Frappe admin panel)
**Performance Goals**: 1M Practice Log rows archived within 30 minutes (SC-002); purge without >2x latency spike (SC-005)
**Constraints**: Executor must not import Frappe; separate virtualenv; all fields read-only; filesystem permissions 0700
**Scale/Scope**: ~500M Practice Log rows total, per-season batches (size varies); 100k concurrent users unaffected (executor runs at 2 AM)

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

| Principle | Applies? | Status | Notes |
|-----------|----------|--------|-------|
| I. Self-Healing Cache | No | PASS | Archive system does not use Redis. No cache keys introduced. |
| II. Sub-20ms Game API | No | PASS | No FastAPI endpoints. Executor runs outside request path. |
| III. Content Hierarchy | No | PASS | Does not modify content hierarchy or bitmaps. |
| IV. Double-Gate Access | No | PASS | No access control changes. |
| V. Crypto Voucher Security | No | PASS | No voucher operations. |
| VI. Financial Precision | No | PASS | No monetary calculations. |
| VII. Auditable State Machines | **Yes** | PASS | Archive Job has defined state machine: Pending→Processing→Completed→Purged / Failed. Transitions enforced via `VALID_TRANSITIONS` dict pattern (same as Voucher Batch). |
| VIII. Test-First Coverage | **Yes** | PASS | Tests planned for: DocType validation, state transitions, scheduled task trigger, executor core logic, purge resumption. |

**Gate result**: PASS — no violations.

**Post-Phase 1 re-check**: PASS — data model and contracts align with Principles VII and VIII. No new Redis keys or FastAPI endpoints introduced.

## Project Structure

### Documentation (this feature)

```text
specs/039-archive-system/
├── plan.md              # This file
├── spec.md              # Feature specification
├── research.md          # Phase 0: technical research
├── data-model.md        # Phase 1: entity definitions
├── quickstart.md        # Phase 1: setup guide
├── contracts/
│   ├── archive-job-doctype.md   # DocType contract
│   └── executor-interface.md    # Executor script contract
└── tasks.md             # Phase 2 output (via /speckit.tasks)
```

### Source Code (repository root)

```text
memora_admin/
├── memora_admin/memora_admin/
│   ├── doctype/
│   │   └── memora_archive_job/          # NEW DocType
│   │       ├── __init__.py
│   │       ├── memora_archive_job.json  # Schema + fields
│   │       ├── memora_archive_job.py    # State machine, retry action
│   │       ├── memora_archive_job.js    # Retry button, read-only enforcement
│   │       └── test_memora_archive_job.py  # DocType tests
│   └── patches/
│       └── 039_archive_job_unique_index.py  # Composite unique constraint
├── memora_admin/tasks/
│   ├── archive_trigger.py               # Scheduled: detect ended seasons, create jobs
│   └── archive_notify.py                # Scheduled: notify on permanently failed jobs
├── memora_admin/hooks.py                # Register scheduled tasks
├── archive_schemas/                     # YAML schema registry
│   ├── dimensions/
│   │   ├── player.v1.yaml
│   │   └── review_item.v1.yaml
│   └── archive_types/
│       └── practice_log.v1.yaml
└── archive_executor/                    # Standalone executor
    ├── run.py                           # Entry point (cron target)
    ├── config.py                        # Env var loading
    ├── db.py                            # pymysql connection + helpers
    ├── exporter.py                      # Parquet export (fact + dimensions)
    ├── manifest.py                      # manifest.json builder
    ├── validator.py                     # File validation (checksums, row counts)
    ├── purge.py                         # Source data purge logic
    ├── schemas.py                       # YAML registry loader
    ├── logger.py                        # JSON structured logging
    └── requirements.txt                 # pyarrow, pandas, pymysql, pyyaml
```

**Structure Decision**: Two-plane architecture. Frappe DocType + tasks live in the standard `memora_admin` module. The standalone executor lives in `archive_executor/` within the app repo (deployed to `/opt/memora-archive/` in production). Schema registry at `archive_schemas/` is version-controlled with the app, referenced via `SCHEMA_REGISTRY_PATH` env var.

## Complexity Tracking

No constitution violations to justify. The architecture is intentionally simple:
- Single DocType (no child tables needed)
- Sequential job processing (no concurrency complexity)
- File-based output (no distributed storage)
- YAML config (no database-backed registry)
