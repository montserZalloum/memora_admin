# Tasks: Analytics Lakehouse

**Input**: Design documents from `/specs/046-analytics-lakehouse/`
**Prerequisites**: plan.md, spec.md, data-model.md, contracts/cli-contract.json, research.md, quickstart.md

**Tests**: Included — constitution check requires all analytics CLI commands to have integration tests with DuckDB in-memory fixtures.

**Organization**: Tasks grouped by user story. User Stories 1 & 2 (P1) are **already implemented** in `archive_executor/` (export, transfer, scheduler, purge). Tasks below cover the analytics-side CLI and two production-side additions (SCD2 dimensions, dimension refresh hooks).

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependencies)
- **[Story]**: Which user story this task belongs to (e.g., US3, US4)
- Exact file paths included in all descriptions

---

## Phase 1: Setup

**Purpose**: Create the `analytics_cli/` standalone package structure

- [x] T001 Create analytics_cli/ package directory structure with commands/, views/, health/, tests/ subdirectories and __init__.py files per plan.md
- [x] T002 [P] Create pyproject.toml with Click, DuckDB, PyArrow, PyYAML dependencies and `memora-analytics` console_scripts entry point in analytics_cli/pyproject.toml

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Core infrastructure that MUST be complete before ANY user story can be implemented

**CRITICAL**: No user story work can begin until this phase is complete

- [x] T003 [P] Implement config module loading DUCKDB_PATH, LAKE_PATH, DIMENSIONS_PATH, MANIFESTS_PATH from environment variables with CLI flag overrides in analytics_cli/config.py
- [x] T004 [P] Implement DuckDB connection manager with file-based and in-memory modes, context manager pattern in analytics_cli/db.py
- [x] T005 Implement Click CLI group entry point with global --duckdb-path, --lake-path, --dimensions-path options and JSON stdout / log stderr convention in analytics_cli/__main__.py
- [x] T006 [P] Create test conftest with DuckDB in-memory connection fixture, temp Hive-partitioned directory builder, sample Parquet file generators for practice_log/memory_state/interaction_log schemas in analytics_cli/tests/conftest.py

**Checkpoint**: Foundation ready — user story implementation can now begin

---

## Already Implemented: User Stories 1 & 2 (P1)

> **US1 — Archive Historical Data** and **US2 — Transfer and Verify** are fully implemented in `archive_executor/` (278 integration tests passing). No new tasks required. See `archive_executor/run.py`, `exporter.py`, `transfer.py`, `validator.py`, `scheduler.py`, `purge.py`.

---

## Phase 3: User Story 3 — Query Analytics Data via DuckDB (Priority: P1) MVP

**Goal**: Parquet files ingested into Hive-partitioned lake directories and queryable via DuckDB semantic views on the Analytics Server

**Independent Test**: Load sample Parquet into lake directory, create views, run `SELECT COUNT(*) FROM practice_log_archive WHERE year=2025` and verify partition pruning

### Tests for User Story 3

> **Write these tests FIRST, ensure they FAIL before implementation**

- [x] T007 [P] [US3] Write tests for ingest-archive command covering: Parquet file copy to correct Hive partition path, manifest.json storage in manifests/archive/, view refresh after ingest, JSON response schema per cli-contract.json, error handling for missing batch-dir in analytics_cli/tests/test_ingest_archive.py
- [x] T008 [P] [US3] Write tests for semantic views covering: archive views for all 5 fact datasets read Parquet with hive_partitioning=true and union_by_name=true, dimension views read from dimensions/ path, combined views (practice_log_combined, memory_state_combined) UNION ALL archive+live, structure_progress_snapshots view handles snapshot_date partition in analytics_cli/tests/test_views.py

### Implementation for User Story 3

- [x] T009 [US3] Implement all DuckDB semantic view definitions: practice_log_archive, interaction_log_archive, memory_state_archive, task_run_log_archive, structure_progress_snapshots (archive views); dim_player, dim_player_history, dim_season, dim_plan, dim_review_item, dim_lesson (dimension views); practice_log_combined, memory_state_combined (combined views) using read_parquet with hive_partitioning=true and union_by_name=true per data-model.md section 2 in analytics_cli/views/semantic.py
- [x] T010 [US3] Implement ingest-archive command: accept --batch-dir, copy Parquet files to correct Hive-partitioned lake path based on manifest metadata, store manifest in manifests/archive/{JOB_ID}.json, call semantic view refresh, return JSON response per cli-contract.json ingest-archive schema, register command in CLI group in analytics_cli/commands/ingest_archive.py

**Checkpoint**: Archive Parquet files are loadable and queryable via DuckDB — MVP complete

---

## Phase 4: User Story 4 — Live Sync Unarchived Practice Data (Priority: P2)

**Goal**: Current unarchived practice data available in DuckDB via atomic live table swap, with combined archive+live view containing zero duplicates

**Independent Test**: Ingest archive data + live snapshot, query `practice_log_combined`, verify zero duplicate `(player_id, item_id, last_seen_at)` tuples and full date range coverage

### Tests for User Story 4

> **Write these tests FIRST, ensure they FAIL before implementation**

- [x] T011 [P] [US4] Write tests for ingest-live command covering: staging table creation, atomic swap to practice_log_live, row count verification, JSON response schema per cli-contract.json, error on missing batch-dir in analytics_cli/tests/test_ingest_live.py
- [x] T012 [P] [US4] Write tests for handoff command covering: date-range mode DELETE from live table by date column/range, season mode DELETE from memory_state_current by season_seq, rows_removed count, JSON response schemas for both modes per cli-contract.json in analytics_cli/tests/test_handoff.py

### Implementation for User Story 4

- [x] T013 [US4] Implement ingest-live command: accept --batch-dir, load Parquet into staging table, atomic CREATE OR REPLACE TABLE swap to practice_log_live, return JSON per cli-contract.json ingest-live schema, register in CLI group in analytics_cli/commands/ingest_live.py
- [x] T014 [US4] Implement handoff command: accept --archive-batch-dir with either --date-column/--from/--to (date-range mode) or --season-seq/--archive-type (season mode), DELETE matching rows from live/current tables, return JSON per cli-contract.json handoff schemas, register in CLI group in analytics_cli/commands/handoff.py
- [x] T015 [P] [US4] Implement refresh-recent command: accept --archive-type and --window-days (default 90), rebuild practice_recent table from practice_log_combined WHERE last_seen_at >= NOW() - window_days, return JSON per cli-contract.json, register in CLI group in analytics_cli/commands/refresh_recent.py
- [x] T016 [P] [US4] Implement refresh-aggregates command: accept --archive-type, rebuild practice_daily_agg and practice_monthly_agg tables from practice_log_combined per data-model.md section 2.5, return JSON per cli-contract.json, register in CLI group in analytics_cli/commands/refresh_aggregates.py
- [x] ~~T017 [P] [US4] Implement mirror-status command~~ — **Removed**: not part of production-to-analytics contract; implemented as analytics-only utility, not called by production executor

**Checkpoint**: Live sync data is queryable alongside archive data with zero duplication

---

## Phase 5: User Story 5 — Structure Progress Snapshots (Priority: P2)

**Goal**: Structure progress snapshots partitioned by snapshot_date queryable via DuckDB with partition pruning

**Independent Test**: Create Parquet files in `lake/structure_progress/snapshot_date=2026-03-10/` and `snapshot_date=2026-03-11/`, query `WHERE snapshot_date='2026-03-10'`, verify only that partition is read

> **Note**: Production-side snapshot export is already implemented in `archive_executor/snapshot.py`. The `structure_progress_snapshots` DuckDB view is created in T009. This phase adds snapshot-specific ingest validation.

- [x] T018 [US5] Add snapshot-specific test cases to verify ingest-archive correctly places structure_progress Parquet into snapshot_date=YYYY-MM-DD/ partition and structure_progress_snapshots view reads them with partition pruning in analytics_cli/tests/test_ingest_archive.py

**Checkpoint**: Structure progress trend queries (e.g., AVG completion by snapshot_date) return correct results

---

## Phase 6: User Story 6 — Refresh Dimension Tables (Priority: P2)

**Goal**: SCD2 Player History dimension exported as Parquet, all dimensions refreshed on source changes and via daily reconciliation

**Independent Test**: Insert a player plan change into `tabMemora Player Plan History`, trigger dimension refresh, verify `dim_player_history.parquet` contains correct SCD2 rows with valid_from/valid_to boundaries

### Tests for User Story 6

> **Write these tests FIRST, ensure they FAIL before implementation**

- [x] T019 [P] [US6] Write tests for SCD2 Player History dimension export (valid_from/valid_to derivation from ordered changes, is_current flag, plan_name denormalization) and dimension refresh trigger logic in archive_executor/tests/test_dimension_refresh.py

### Implementation for User Story 6

- [x] T020 [P] [US6] Create player_history.v1.yaml SCD2 dimension schema with LEAD window function query per data-model.md section 3 in archive_schemas/dimensions/player_history.v1.yaml
- [x] T021 [US6] Implement dimension_refresh service: SCD2 Player History export logic (query tabMemora Player Plan History, derive valid_from/valid_to/is_current via SQL LEAD window), full-refresh export for all 6 dimension types (player, player_history, season, plan, review_item, lesson), export to Parquet + transfer to analytics server in memora_admin/memora_admin/services/dimension_refresh.py
- [x] T022 [US6] Implement Frappe doc_event hooks triggering dimension refresh on after_insert/on_update for Memora Player Profile, Memora Academic Plan, Memora Season, Memora Review Item, Memora Lesson in memora_admin/memora_admin/services/dimension_refresh.py
- [x] T023 [US6] Implement daily dimension reconciliation background task (scheduled 04:00 via Frappe scheduler) that full-refreshes all 6 dimension Parquet files as a safety net for missed events in memora_admin/memora_admin/tasks/dimension_sync.py

**Checkpoint**: Dimension tables are up-to-date, SCD2 temporal joins return correct historical plan for any player at any point in time

---

## Phase 7: User Story 7 — Data Lake Health Checks (Priority: P3)

**Goal**: Daily automated health checks on the Analytics Server detecting duplicates, checksum mismatches, dimension gaps, and undersized partitions

**Independent Test**: Insert a duplicate row in practice_log, run `memora-analytics verify`, confirm duplicate check returns `fail` status with the duplicate flagged

### Tests for User Story 7

> **Write these tests FIRST, ensure they FAIL before implementation**

- [x] T024 [P] [US7] Write tests for all four health checks: duplicate detection (known duplicate found), checksum verification (matching + mismatching SHA-256), dimension coverage (missing player flagged), partition analysis (undersized partition flagged) in analytics_cli/tests/test_health_checks.py

### Implementation for User Story 7

- [x] T025 [P] [US7] Implement duplicate detection check: GROUP BY (player_id, item_id, last_seen_at) HAVING COUNT > 1 on practice_log_combined, return pass/fail with duplicate_count and sample_rows per data-model.md section 6 in analytics_cli/health/duplicate_check.py
- [x] T026 [P] [US7] Implement manifest checksum verification: scan manifests/archive/*.json, compute SHA-256 of referenced Parquet files, compare against manifest checksum, return pass/fail with files_checked and mismatches in analytics_cli/health/checksum_check.py
- [x] T027 [P] [US7] Implement dimension coverage check: LEFT JOIN practice_log_combined with dim_player_history on player_id, report players with no dimension row, return pass/fail with missing_players count and sample_ids in analytics_cli/health/dimension_coverage.py
- [x] T028 [P] [US7] Implement partition file size analysis: scan lake directories, identify partitions with files under 64MB threshold, return pass/warning with total_partitions, undersized_partitions, and details in analytics_cli/health/partition_analysis.py
- [x] T029 [US7] Implement verify CLI command: orchestrate all four health checks, aggregate into combined JSON response with overall status (ok/warning/error) and per-check results per cli-contract.json verify schema, register in CLI group in analytics_cli/commands/verify.py

**Checkpoint**: Health checks catch duplicate rows, checksum mismatches, and dimension gaps

---

## ~~Phase 8: User Story 8 — Compact Small Parquet Files~~ (Removed)

> **Removed from production contract**: `compact` is an analytics-only maintenance utility, not called by the production executor pipeline. It remains available as a standalone analytics CLI command but is not part of the production-to-analytics integration contract.

---

## Phase 9: Polish & Cross-Cutting Concerns

**Purpose**: End-to-end validation and cleanup across all stories

- [x] T032 [P] End-to-end validation following quickstart.md: install CLI, configure env, ingest sample archive + live data, query combined views, run verify
- [x] T033 [P] Review CLI --help output for all commands, ensure consistent flag naming and descriptions across analytics_cli/__main__.py

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: No dependencies — start immediately
- **Foundational (Phase 2)**: Depends on Phase 1 — BLOCKS all user stories
- **US3 (Phase 3)**: Depends on Phase 2 — MVP target
- **US4 (Phase 4)**: Depends on US3 (needs views and ingest infrastructure)
- **US5 (Phase 5)**: Depends on US3 (adds snapshot-specific tests to existing ingest)
- **US6 (Phase 6)**: Depends on Phase 2 only — **independent of analytics CLI**, production-side Frappe code
- **US7 (Phase 7)**: Depends on US3 (health checks operate on lake data and views)
- **~~US8 (Phase 8)~~**: Removed from production contract
- **Polish (Phase 9)**: Depends on all desired user stories being complete

### User Story Dependencies

```
Phase 1 (Setup)
    │
Phase 2 (Foundational)
    ├──────────────────────────────┐
    │                              │
Phase 3: US3 (P1) MVP        Phase 6: US6 (P2)
    │                         [independent — production-side]
    ├─────────┬───────────┐
    │         │           │
Phase 4   Phase 7     Phase 8
US4 (P2)  US7 (P3)   US8 (P3)
    │
Phase 5
US5 (P2)
    │
Phase 9 (Polish)
```

### Within Each User Story

- Tests MUST be written and FAIL before implementation begins
- View/model definitions before command implementations
- Command implementations include CLI group registration
- Story checkpoint before moving to next priority

### Parallel Opportunities

- **After Phase 2**: US3 and US6 can run in parallel (different codebases — analytics_cli vs memora_admin)
- **After US3**: US4, US5, and US7 can all run in parallel (different command files, no shared state)
- **Within US4**: T015, T016 are [P] (refresh-recent, refresh-aggregates — independent commands)
- **Within US7**: T025, T026, T027, T028 are [P] (four independent health check modules)

---

## Parallel Example: User Story 3

```bash
# Launch tests in parallel (different test files):
Task T007: "Write tests for ingest-archive in analytics_cli/tests/test_ingest_archive.py"
Task T008: "Write tests for semantic views in analytics_cli/tests/test_views.py"

# Then implement sequentially (views before ingest command):
Task T009: "Implement semantic view definitions in analytics_cli/views/semantic.py"
Task T010: "Implement ingest-archive command in analytics_cli/commands/ingest_archive.py"
```

## Parallel Example: User Story 7

```bash
# Launch all health check modules in parallel (independent files):
Task T025: "Implement duplicate_check.py"
Task T026: "Implement checksum_check.py"
Task T027: "Implement dimension_coverage.py"
Task T028: "Implement partition_analysis.py"

# Then wire them together:
Task T029: "Implement verify command orchestrating all checks"
```

---

## Implementation Strategy

### MVP First (User Story 3 Only)

1. Complete Phase 1: Setup (T001-T002)
2. Complete Phase 2: Foundational (T003-T006)
3. Complete Phase 3: User Story 3 (T007-T010)
4. **STOP and VALIDATE**: Ingest a sample Parquet, query via DuckDB views
5. Deploy analytics CLI to Analytics Server

### Incremental Delivery

1. Setup + Foundational → Foundation ready
2. US3 → Archive data queryable via DuckDB (MVP!)
3. US6 → Dimension tables refreshed (can run in parallel with US4)
4. US4 → Live + archive combined views with zero duplication
5. US5 → Snapshot queries validated
6. US7 → Health checks catch data quality issues
7. US8 → Compaction optimizes query performance
8. Polish → End-to-end validation

### Parallel Team Strategy

With two developers:

1. Both complete Setup + Foundational together
2. Once Foundational is done:
   - Developer A: US3 (analytics CLI) → US4 → US7 → US8
   - Developer B: US6 (dimension refresh — Frappe/production side)
3. Converge at Polish phase

---

## Notes

- [P] tasks = different files, no dependencies on incomplete tasks
- [Story] label maps task to specific user story for traceability
- US1 & US2 are already implemented — no tasks generated
- US5 is minimal — production snapshot export exists, analytics view created in US3
- US6 is production-side code (Frappe hooks) — independent of analytics CLI
- All CLI commands return JSON to stdout per cli-contract.json schemas
- All DuckDB views use `union_by_name=true` for schema evolution (FR-017)
- Commit after each task or logical group
- Stop at any checkpoint to validate story independently
