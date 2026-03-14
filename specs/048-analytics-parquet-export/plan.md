# Implementation Plan: Analytics Parquet Dataset Export

**Branch**: `048-analytics-parquet-export` | **Date**: 2026-03-13 | **Spec**: `specs/048-analytics-parquet-export/spec.md`
**Input**: Feature specification from `/specs/048-analytics-parquet-export/spec.md`

## Summary

Extend the existing `analytics_exporter/` module (built in feature 047) from 12 datasets to 18 datasets producing ~22 Parquet files. Add manifest generation (SHA-256 checksum + row count per file), replace normalized hierarchy/academic files with denormalized versions, and add new domains: player profiles, interactions, memory state, subscriptions, vouchers, challenges, progress, wallets, lesson stages, content reports, live challenges, archive jobs, and task runs. Same infrastructure: YAML-driven schemas, PyMySQL with READ COMMITTED isolation, PyArrow streaming export. No new dependencies.

## Technical Context

**Language/Version**: Python 3.11+
**Primary Dependencies**: `pyarrow>=14.0,<19.0`, `pymysql>=1.1,<2.0`, `pyyaml>=6.0,<7.0` (all existing)
**Storage**: MariaDB (source, read-only); Parquet files at `analytics_exports/` (output)
**Testing**: pytest, integration tests against real DB (same pattern as existing `analytics_exporter/tests/`)
**Target Platform**: Linux server (same host as `archive_executor/`)
**Project Type**: Single standalone Python module (CLI tool) — extending existing `analytics_exporter/`
**Performance Goals**: All 18 datasets export within reasonable time. Interaction log (largest table) uses date-range filtering to bound query time. Practice log uses incremental watermark for delta efficiency.
**Constraints**: READ COMMITTED isolation (no table locks); no blocking concurrent writes; manifest SHA-256 for every output file; multi-file datasets must be atomic (both succeed or both fail)
**Scale/Scope**: Interaction log ~11K rows (growing); vouchers ~5K rows; most other tables <1K rows. Full snapshot acceptable for all except practice log (incremental) and interactions (date-range).

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

| Principle | Applicable? | Status | Notes |
|-----------|-------------|--------|-------|
| I. Self-Healing Cache | No | PASS | Export is read-only from MariaDB; no Redis involvement |
| II. Sub-20ms Game API | No | PASS | Batch export job, not a game API endpoint |
| III. Content Hierarchy Integrity | Yes | PASS | Exported hierarchy respects Subject->Track->Unit->Topic->Lesson chain; denormalized via JOINs on source-of-truth tables |
| IV. Double-Gate Access Control | No | PASS | Admin-only export; no content serving |
| V. Cryptographic Voucher Security | Yes | PASS | Voucher export excludes PINs entirely — only exports card serial, batch info, status, and allocation data. No HMAC hashes, no plaintexts. |
| VI. Financial Precision | Partial | PASS | `face_value` and `amount_paid` are exported as float64 from DECIMAL source. This is acceptable for analytics (aggregate reporting) — not used for financial calculations. Constitution requirement applies to commission/invoice math paths, not read-only analytics exports. |
| VII. Auditable State Machines | Partial | PASS | No state machine in exporter. Export results are logged with dataset name, row count, and error detail. Archive job and voucher card statuses are exported as-is (read-only reflection of production state). |
| VIII. Test-First Coverage | Yes | REQUIRED | TDD mandatory; integration tests against real DB for all new datasets |

**No violations — no Complexity Tracking entry required.**

**Post-design re-check (Phase 1)**: All gates still pass. Voucher export SQL confirmed to exclude PIN-related columns. Memory state export uses SQL-side `CAST()` and `BIN_TO_UUID()` — no Python-side precision concerns. Hierarchy JOINs read from canonical Frappe tables. All queries use READ COMMITTED isolation via existing `db.py`.

## Project Structure

### Documentation (this feature)

```text
specs/048-analytics-parquet-export/
├── plan.md              # This file
├── research.md          # Phase 0 output
├── data-model.md        # Phase 1 output
├── quickstart.md        # Phase 1 output
├── contracts/           # Phase 1 output
│   ├── output-parquet-schemas.yaml
│   └── cli.md
└── tasks.md             # Phase 2 output (/speckit.tasks command)
```

### Source Code (repository root)

```text
analytics_exporter/                  # EXISTING module (extended)
├── __init__.py
├── __main__.py                      # Entry: python3 -m analytics_exporter
├── config.py                        # MODIFY: add ANALYTICS_INTERACTION_FROM/TO
├── db.py                            # UNCHANGED
├── exporter.py                      # UNCHANGED (handles Decimal, streaming, zero-row)
├── manifest.py                      # NEW: SHA-256 manifest generation
├── watermark.py                     # UNCHANGED
├── validator.py                     # UNCHANGED
├── run.py                           # MODIFY: new KNOWN_DATASETS, orchestrate_exports(), multi-file support
├── schemas/                         # REPLACE old schemas, add new ones
│   ├── dim_player.yaml              # NEW
│   ├── dim_content_hierarchy.yaml   # NEW (replaces subjects/tracks/units/topics/lessons)
│   ├── dim_review_item.yaml         # NEW (replaces item_mapping)
│   ├── dim_season.yaml              # NEW (replaces seasons — adds is_published)
│   ├── dim_academic_plan.yaml       # NEW (replaces academic_plans/grade_majors/grades/majors)
│   ├── fact_interaction.yaml        # NEW
│   ├── fact_memory_state.yaml       # NEW
│   ├── fact_practice.yaml           # RENAMED from practice_log.yaml (same content)
│   ├── fact_subscription.yaml       # NEW
│   ├── fact_voucher.yaml            # NEW
│   ├── fact_challenge_attempt.yaml  # NEW
│   ├── fact_challenge_detail.yaml   # NEW
│   ├── fact_structure_progress.yaml # NEW
│   ├── fact_player_wallet.yaml      # NEW
│   ├── dim_lesson_stage.yaml        # NEW
│   ├── fact_content_report.yaml     # NEW
│   ├── fact_live_challenge_event.yaml        # NEW
│   ├── fact_live_challenge_participation.yaml # NEW
│   ├── fact_archive_job.yaml        # NEW
│   ├── fact_task_run_log.yaml       # NEW
│   └── fact_build_queue.yaml        # NEW
├── tests/
│   ├── conftest.py                  # MODIFY: fixtures for new datasets
│   ├── test_dim_player.py           # NEW
│   ├── test_dim_content_hierarchy.py # NEW
│   ├── test_dim_review_item.py      # NEW
│   ├── test_dim_season.py           # NEW
│   ├── test_dim_academic_plan.py    # NEW
│   ├── test_fact_interaction.py     # NEW
│   ├── test_fact_memory_state.py    # NEW
│   ├── test_fact_practice.py        # RENAMED from test_practice_log.py
│   ├── test_fact_subscription.py    # NEW
│   ├── test_fact_voucher.py         # NEW
│   ├── test_fact_challenge.py       # NEW (covers both attempt + detail)
│   ├── test_fact_supplementary.py   # NEW (covers progress, wallet, stage, report, live_challenge, archive_job, task_run)
│   ├── test_manifest.py            # NEW
│   ├── test_validator.py            # UNCHANGED
│   └── test_watermark.py           # UNCHANGED
└── requirements.txt                 # UNCHANGED

analytics_exports/                   # Output directory (created by run.py if absent)
├── dim_player.parquet
├── dim_player.manifest.json
├── dim_content_hierarchy.parquet
├── dim_content_hierarchy.manifest.json
├── dim_review_item.parquet
├── dim_review_item.manifest.json
├── dim_season.parquet
├── dim_season.manifest.json
├── dim_academic_plan.parquet
├── dim_academic_plan.manifest.json
├── fact_interaction.parquet
├── fact_interaction.manifest.json
├── fact_memory_state.parquet
├── fact_memory_state.manifest.json
├── fact_practice.parquet
├── fact_practice.manifest.json
├── fact_subscription.parquet
├── fact_subscription.manifest.json
├── fact_voucher.parquet
├── fact_voucher.manifest.json
├── fact_challenge.manifest.json
├── fact_challenge_attempt.parquet
├── fact_challenge_detail.parquet
├── fact_structure_progress.parquet
├── fact_structure_progress.manifest.json
├── fact_player_wallet.parquet
├── fact_player_wallet.manifest.json
├── dim_lesson_stage.parquet
├── dim_lesson_stage.manifest.json
├── fact_content_report.parquet
├── fact_content_report.manifest.json
├── fact_live_challenge.manifest.json
├── fact_live_challenge_event.parquet
├── fact_live_challenge_participation.parquet
├── fact_archive_job.parquet
├── fact_archive_job.manifest.json
├── fact_task_run.manifest.json
├── fact_task_run_log.parquet
├── fact_build_queue.parquet
└── .watermark.json
```

**Structure Decision**: Extend the existing `analytics_exporter/` module. Same directory layout, same infrastructure files. Only changes: new YAML schemas, manifest.py, updated run.py orchestration, and new test files. Old schemas that are superseded are removed.

## Complexity Tracking

> No constitution violations — section not required.
