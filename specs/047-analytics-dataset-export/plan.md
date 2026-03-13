# Implementation Plan: Educational Analytics Dataset Export

**Branch**: `047-analytics-dataset-export` | **Date**: 2026-03-13 | **Spec**: `specs/047-analytics-dataset-export/spec.md`
**Input**: Feature specification from `/specs/047-analytics-dataset-export/spec.md`

## Summary

Build a standalone `analytics_exporter/` Python module that exports 12 Parquet datasets to `analytics_exports/` for the analytics server to compute educational performance reports. Exports cover four domains: practice log (with incremental watermark mode), item→curriculum mapping, content hierarchy (5 tables), and academic context (5 tables). No Frappe dependency — direct PyMySQL reads with READ COMMITTED isolation. Follows the same PyArrow/PyMySQL/YAML-schema pattern established by `archive_executor/`.

## Technical Context

**Language/Version**: Python 3.11+
**Primary Dependencies**: `pyarrow>=14.0,<19.0`, `pymysql>=1.1,<2.0`, `pyyaml>=6.0,<7.0`
**Storage**: MariaDB (source, read-only); Parquet files at `analytics_exports/` (output)
**Testing**: pytest, integration tests against real DB (same pattern as `archive_executor/tests/`)
**Target Platform**: Linux server (same host as `archive_executor/`)
**Project Type**: Single standalone Python module (CLI tool)
**Performance Goals**: Incremental practice log export runs measurably faster than full scan when <10% of rows have changed (SC-006)
**Constraints**: READ COMMITTED isolation (FR-023); no table locks (FR-004); empty Parquet files with correct schema on zero rows (edge case)
**Scale/Scope**: Practice log potentially millions of rows; hierarchy/academic tables thousands of rows (full snapshot acceptable)

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

| Principle | Applicable? | Status | Notes |
|-----------|-------------|--------|-------|
| I. Self-Healing Cache | ❌ No | PASS | Export is read-only; no Redis involvement |
| II. Sub-20ms Game API | ❌ No | PASS | Batch export job, not a game API endpoint |
| III. Content Hierarchy Integrity | ✅ Yes | PASS | Exported hierarchy must respect Subject→Track→Unit→Topic→Lesson chain; all parent refs denormalized from DocType fields (already maintained by Frappe) |
| IV. Double-Gate Access Control | ❌ No | PASS | Admin-only export; no content serving |
| V. Cryptographic Voucher Security | ❌ No | PASS | No voucher data exported |
| VI. Financial Precision | ❌ No | PASS | No monetary data |
| VII. Auditable State Machines | ⚠️ Partial | PASS | No state machine in exporter; export run outcomes must be logged with dataset name, row count, error detail (FR-025) |
| VIII. Test-First Coverage | ✅ Yes | REQUIRED | TDD mandatory; integration tests against real DB; see tests/ plan below |

**No violations — no Complexity Tracking entry required.**

**Post-design re-check (Phase 1)**: ✅ All gates still pass. SQL queries verified to use read-only SELECT with READ COMMITTED isolation. Hierarchy integrity preserved: item_mapping SQL excludes items with null curriculum path (FR-007). Hierarchy files export all entities including unpublished (FR-012, FR-019).

## Project Structure

### Documentation (this feature)

```text
specs/047-analytics-dataset-export/
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
analytics_exporter/          # NEW standalone module (sibling to archive_executor/)
├── __init__.py
├── __main__.py              # Entry: python3 -m analytics_exporter
├── config.py                # Config dataclass, Config.from_env()
├── db.py                    # get_connection(), streaming_cursor() — mirrors archive_executor/db.py
├── exporter.py              # write_parquet(), export_snapshot(), export_incremental()
├── watermark.py             # load_watermark(), save_watermark()
├── validator.py             # validate_export() — DQ checks: no-dup PKs, no-null IDs, min-row counts
├── run.py                   # orchestrate_exports(), main()
├── schemas/                 # YAML export definitions (one per output file)
│   ├── practice_log.yaml
│   ├── item_mapping.yaml
│   ├── subjects.yaml
│   ├── tracks.yaml
│   ├── units.yaml
│   ├── topics.yaml
│   ├── lessons.yaml
│   ├── seasons.yaml
│   ├── grades.yaml
│   ├── majors.yaml
│   ├── academic_plans.yaml
│   └── grade_majors.yaml
├── tests/
│   ├── conftest.py          # DB fixtures, test data helpers
│   ├── test_practice_log.py # US1 scenarios (4 scenarios + incremental merge)
│   ├── test_item_mapping.py # US2 scenarios (3 scenarios)
│   ├── test_hierarchy.py    # US3 scenarios (3 scenarios)
│   ├── test_academic_context.py  # US4 scenarios (3 scenarios)
│   ├── test_validator.py    # DQ validation unit tests
│   └── test_watermark.py    # Watermark load/save unit tests
└── requirements.txt         # pyarrow, pymysql, pyyaml

analytics_exports/           # Output directory (created by run.py if absent)
├── practice_log.parquet
├── item_mapping.parquet
├── subjects.parquet
├── tracks.parquet
├── units.parquet
├── topics.parquet
├── lessons.parquet
├── seasons.parquet
├── grades.parquet
├── majors.parquet
├── academic_plans.parquet
├── grade_majors.parquet
└── .watermark.json          # Incremental watermark state
```

**Structure Decision**: Single standalone module (`analytics_exporter/`) following the `archive_executor/` pattern — no Frappe imports, PyMySQL direct reads, PyArrow Parquet output. No job orchestration layer needed (direct write, no staging/transfer). Separate from `archive_executor/` to maintain clean responsibility separation (archive pipeline handles job-scoped fact archives; analytics exporter handles full-dataset reference exports).

## Complexity Tracking

> No constitution violations — section not required.
