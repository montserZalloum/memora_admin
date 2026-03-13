# Implementation Plan: Analytics Lakehouse

**Branch**: `046-analytics-lakehouse` | **Date**: 2026-03-12 | **Spec**: [spec.md](./spec.md)
**Input**: Feature specification from `/specs/046-analytics-lakehouse/spec.md`

## Summary

Build the `memora-analytics` CLI tool that runs on the Analytics Server and implements DuckDB-based data lake management. The production-side archive executor (export, transfer, scheduler, purge, live sync, incremental sync, snapshots) is **already implemented** in `archive_executor/`. This plan focuses on the analytics-side counterpart that the executor calls via SSH, plus two production-side additions: SCD2 Player History dimension management and dimension refresh hooks.

## Technical Context

**Language/Version**: Python 3.11+
**Primary Dependencies**: DuckDB (analytics engine), PyArrow (Parquet I/O), Click (CLI framework), PyYAML (schema registry)
**Storage**: DuckDB file database on Analytics Server; Parquet files in Hive-partitioned lake directories
**Testing**: pytest with DuckDB in-memory databases (analytics side), existing pytest + real DB integration tests (production side)
**Target Platform**: Linux server (Analytics Server at `ANALYTICS_SSH_HOST`)
**Project Type**: Single standalone CLI package (`analytics_cli/`) deployed to `/opt/analytics/memora-analytics`
**Performance Goals**: Ingest 1M+ rows per batch within SSH timeout (300s default); DuckDB queries leverage partition pruning for sub-second response on filtered scans
**Constraints**: No direct MariaDB access from analytics server; all data arrives as Parquet via rsync; append-only lake (no UPDATE/DELETE on Parquet except compaction); analytics CLI must return JSON to stdout (contract with `ingestion.py`)
**Scale/Scope**: ~500M practice log rows, ~10B memory state rows (partitioned by season), 5 fact datasets, 5 dimension tables, daily live sync + weekly snapshots

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

| Principle | Relevance | Status |
|-----------|-----------|--------|
| I. Self-Healing Cache | Not applicable — analytics pipeline does not touch Redis | PASS |
| II. Sub-20ms Game API | Not applicable — analytics runs on separate server, no game API impact | PASS |
| III. Content Hierarchy Integrity | Not applicable — read-only analytics, no bitmap modifications | PASS |
| IV. Double-Gate Access Control | Not applicable — no content access decisions | PASS |
| V. Cryptographic Voucher Security | Not applicable — no voucher handling | PASS |
| VI. Financial Precision | Not applicable — no monetary calculations | PASS |
| VII. Auditable State Machines | **Relevant** — Archive Job state machine already enforced in `run.py`; analytics CLI must not advance states directly (only return success/failure JSON) | PASS |
| VIII. Test-First Coverage | **Relevant** — all analytics CLI commands must have integration tests with DuckDB in-memory fixtures | PASS |

**Post-Phase-1 Re-check**: No violations introduced. Analytics CLI is a read-only consumer of Parquet files with no production database access, no Redis interaction, no financial calculations, and no voucher handling.

## Project Structure

### Documentation (this feature)

```text
specs/046-analytics-lakehouse/
├── plan.md              # This file
├── research.md          # Phase 0 output
├── data-model.md        # Phase 1 output
├── quickstart.md        # Phase 1 output
├── contracts/           # Phase 1 output (CLI contract JSON schemas)
└── tasks.md             # Phase 2 output (NOT created by /speckit.plan)
```

### Source Code (repository root)

```text
analytics_cli/                          # NEW — Analytics Server CLI tool
├── __init__.py
├── __main__.py                         # Click CLI entry point
├── config.py                           # DuckDB path, lake paths from env/args
├── db.py                               # DuckDB connection management
├── commands/
│   ├── __init__.py
│   ├── ingest_archive.py               # FR-004: Load archive Parquet into DuckDB
│   ├── ingest_live.py                  # FR-010: Staging → swap for live snapshots
│   ├── handoff.py                      # Remove archived ranges from live tables
│   ├── refresh_recent.py               # Rebuild rolling recent-N-days layer
│   ├── refresh_aggregates.py           # Rebuild daily/monthly aggregate tables
│   └── verify.py                       # FR-022: Health checks
├── views/
│   ├── __init__.py
│   └── semantic.py                     # FR-016/017: DuckDB view definitions
├── health/
│   ├── __init__.py
│   ├── duplicate_check.py              # Duplicate detection in combined view
│   ├── checksum_check.py               # Manifest SHA-256 verification
│   ├── dimension_coverage.py           # Dimension gap detection
│   └── partition_analysis.py           # File size analysis
└── tests/
    ├── conftest.py                     # DuckDB in-memory fixtures, temp Parquet dirs
    ├── test_ingest_archive.py
    ├── test_ingest_live.py
    ├── test_handoff.py
    ├── test_verify.py
    ├── test_views.py
    └── test_health_checks.py

memora_admin/memora_admin/
├── doctype/
│   └── memora_player_plan_history/     # Existing DocType — needs SCD2 hook logic
├── services/
│   └── dimension_refresh.py            # NEW — FR-015: Dimension refresh on events
└── tasks/
    └── dimension_sync.py               # NEW — FR-015: Daily dimension reconciliation

archive_executor/                       # EXISTING — production-side pipeline
├── (existing modules unchanged)
└── tests/
    └── test_dimension_refresh.py       # NEW — tests for SCD2 + refresh logic

archive_schemas/
├── dimensions/
│   └── player_history.v1.yaml          # NEW — SCD2 dimension schema
└── (existing schemas unchanged)
```

**Structure Decision**: The analytics CLI is a **standalone package** (`analytics_cli/`) mirroring the pattern of `archive_executor/` — no Frappe imports, pure Python + DuckDB. It deploys to the Analytics Server as a self-contained CLI tool. Production-side additions (SCD2 hooks, dimension refresh) integrate into existing Frappe code paths.

## Complexity Tracking

No constitution violations to justify.

## Existing Infrastructure Assessment

### Already Implemented (production side)

| Component | Module | Status |
|-----------|--------|--------|
| Archive pipeline orchestrator | `archive_executor/run.py` | Complete — 7-stage lifecycle |
| Parquet export engine | `archive_executor/exporter.py` | Complete — fact + dimension, streaming |
| DQ validation | `archive_executor/validator.py` | Complete — 16 rules + generic YAML engine |
| Schema registry | `archive_executor/schemas.py` | Complete — archive_types, dimensions, sync_types, snapshots |
| SSH transfer + checksum | `archive_executor/transfer.py` | Complete — rsync with SHA-256 verify |
| Analytics CLI caller | `archive_executor/ingestion.py` | Complete — remote commands via SSH |
| Live sync pipeline | `archive_executor/live_sync.py` | Complete — full snapshot with exclusions |
| Incremental sync | `archive_executor/sync.py` | Complete — checkpoint-based per-season |
| Snapshot pipeline | `archive_executor/snapshot.py` | Complete — weekly structure progress |
| Purge with audit | `archive_executor/purge.py` | Complete — batch DELETE + partition DROP |
| Safety gates | `archive_executor/safety_gates.py` | Complete — 5-gate pre-purge checks |
| Job scheduler | `archive_executor/scheduler.py` | Complete — date-range + season-scoped |
| Archive Job DocType | Frappe DocType | Complete — ARCH-.#####. naming |
| Live Sync Job DocType | Frappe DocType | Complete — LSYNC-.#####. naming |
| Archive schemas (YAML) | `archive_schemas/` | Complete — 4 archive types, 6 dimensions, 1 sync, 1 snapshot |
| 278 integration tests | `archive_executor/tests/` | Complete |

### To Be Built

| Component | Priority | Spec References |
|-----------|----------|-----------------|
| `memora-analytics` CLI tool | P1 | FR-016, FR-017, User Stories 3, 7, 8 |
| DuckDB ingest-archive command | P1 | FR-004 (analytics side) |
| DuckDB ingest-live command | P1 | FR-010 (analytics side) |
| DuckDB handoff command | P1 | FR-010, FR-011 |
| DuckDB semantic views | P1 | FR-016, FR-017 |
| DuckDB verify / health checks | P3 | FR-022, User Story 7 |
| SCD2 Player History dimension ETL | P2 | FR-014 |
| Dimension refresh hooks | P2 | FR-015 |
| Daily dimension reconciliation task | P2 | FR-015 |
| refresh-recent command | P2 | Rolling recent layer |
| refresh-aggregates command | P2 | Daily/monthly aggregates |
| `player_history.v1.yaml` schema | P2 | FR-014 |
