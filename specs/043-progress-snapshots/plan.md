# Implementation Plan: Weekly Structure Progress Snapshots

**Branch**: `043-progress-snapshots` | **Date**: 2026-03-11 | **Spec**: [spec.md](./spec.md)
**Input**: Feature specification from `/specs/043-progress-snapshots/spec.md`

## Summary

Build a weekly batch pipeline that captures a point-in-time snapshot of all `Memora Structure Progress` rows, enriched with each student's active `plan_id` from `Memora Player Profile`, and writes the result as a Parquet partition keyed by `snapshot_date`. The pipeline runs standalone in `archive_executor/` (reusing existing DB helpers, streaming cursors, and Parquet writing patterns), is scheduled via system cron every Sunday at 03:00 Asia/Amman, and guarantees idempotent overwrites with zero duplicate rows.

## Technical Context

**Language/Version**: Python 3.11+ (matches existing archive_executor)
**Primary Dependencies**: pyarrow (Parquet writing), pymysql (DB access via archive_executor.db), archive_executor shared utilities (Config, StructuredLogger, manifest builder)
**Storage**: MariaDB (read-only source: `tabMemora Structure Progress`, `tabMemora Player Profile`), Parquet files (output to analytics server storage)
**Testing**: pytest (integration tests in `archive_executor/tests/`, matching existing test patterns)
**Target Platform**: Linux server (same host as archive executor)
**Project Type**: Single — new module in existing `archive_executor/` package
**Performance Goals**: Complete snapshot of full Structure Progress table within 10 minutes; streaming cursor keeps memory bounded regardless of table size
**Constraints**: Read-only on source tables (FR-009); no archive/purge logic (FR-011); must not impact production DB responsiveness
**Scale/Scope**: ~10K-100K Structure Progress rows per weekly snapshot; grows linearly with student population; Parquet compression keeps storage minimal

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

| Principle | Relevant? | Status | Notes |
|-----------|-----------|--------|-------|
| I. Self-Healing Cache Architecture | No | PASS | Pipeline reads from MariaDB only; no Redis interaction |
| II. Sub-20ms Game API Performance | No | PASS | Batch pipeline; no FastAPI endpoints |
| III. Content Hierarchy Integrity | No | PASS | Read-only on source data; no structural modifications |
| IV. Double-Gate Access Control | No | PASS | No content access flow involved |
| V. Cryptographic Voucher Security | No | PASS | No voucher operations |
| VI. Financial Precision | No | PASS | `completion_percentage` is Float, not monetary |
| VII. Auditable State Machines | Marginal | PASS | No multi-state lifecycle in v1 — single run-to-completion; no job state machine needed |
| VIII. Test-First Coverage | Yes | PASS | Integration tests required; pytest with real DB (matching archive_executor test patterns) |

**Gate result**: PASS — no violations. Feature is a read-only batch export pipeline that does not touch any core game, cache, or financial systems.

## Project Structure

### Documentation (this feature)

```text
specs/043-progress-snapshots/
├── plan.md              # This file
├── research.md          # Phase 0: technology & pattern decisions
├── data-model.md        # Phase 1: entity schema & relationships
├── quickstart.md        # Phase 1: developer getting-started guide
├── contracts/
│   ├── parquet-schema.md    # Output Parquet column contract
│   └── manifest-schema.md   # Manifest JSON contract
└── tasks.md             # Phase 2 output (created by /speckit.tasks)
```

### Source Code (repository root)

```text
archive_executor/
├── snapshot.py              # NEW — core snapshot pipeline module
├── config.py                # EXISTING — add snapshot_output_path field
├── db.py                    # EXISTING — reuse get_connection, streaming_cursor
├── logger.py                # EXISTING — reuse StructuredLogger
├── manifest.py              # EXISTING — reuse build_manifest
└── tests/
    └── test_snapshot.py     # NEW — integration tests

archive_schemas/
└── snapshot_types/
    └── structure_progress.v1.yaml  # NEW — schema definition for DQ + metadata
```

**Structure Decision**: New `snapshot.py` module inside existing `archive_executor/` package. This reuses the established DB helpers, streaming cursor, Parquet writing, structured logging, and manifest builder. No new packages or directories beyond the module file and its test. The snapshot schema YAML goes in a new `snapshot_types/` subdirectory under `archive_schemas/` to distinguish from archive and sync types.

## Complexity Tracking

> No constitution violations — this section is intentionally empty.
