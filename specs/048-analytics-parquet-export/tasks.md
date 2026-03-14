# Tasks: Analytics Parquet Dataset Export

**Input**: Design documents from `/specs/048-analytics-parquet-export/`
**Prerequisites**: plan.md, spec.md, data-model.md, contracts/, research.md, quickstart.md

**Tests**: TDD mandatory (Constitution VIII). Integration tests against real DB for all new datasets.

**Organization**: Tasks grouped by user story. US1/US2/US4 are P1 (core), US3 is P2 (supplementary).

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependencies)
- **[Story]**: Which user story this task belongs to (US1, US2, US3, US4)
- All paths relative to repository root `analytics_exporter/`

---

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: Remove superseded 047 schemas, rename practice_log, update config for new env vars.

- [x] T001 Remove superseded 047 schema YAML files from analytics_exporter/schemas/ (subjects.yaml, tracks.yaml, units.yaml, topics.yaml, lessons.yaml, item_mapping.yaml, grades.yaml, majors.yaml, academic_plans.yaml, grade_majors.yaml, seasons.yaml) and their corresponding test files (analytics_exporter/tests/test_hierarchy.py, analytics_exporter/tests/test_academic_context.py, analytics_exporter/tests/test_item_mapping.py)
- [x] T002 [P] Rename analytics_exporter/schemas/practice_log.yaml to analytics_exporter/schemas/fact_practice.yaml: update `dataset` to `fact_practice`, `output_file` to `fact_practice.parquet`, keep same SQL and columns; update DQ rule IDs from DQ-PL to DQ-FP per output-parquet-schemas.yaml contract
- [x] T003 [P] Add `analytics_interaction_from` and `analytics_interaction_to` optional fields (str | None, default None) to Config dataclass in analytics_exporter/config.py, reading from env vars `ANALYTICS_INTERACTION_FROM` and `ANALYTICS_INTERACTION_TO`; when None, default to last 30 days in run.py

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Manifest module, multi-file export support, orchestration refactor. MUST complete before any user story.

**CRITICAL**: No user story work can begin until this phase is complete.

- [x] T004 Create analytics_exporter/manifest.py with `compute_sha256(file_path) -> str` (streaming 64KB chunks) and `write_manifest(output_dir, dataset_key, files_info, kind="analytics") -> str` that writes `{dataset_key}.manifest.json` per the manifest format in data-model.md (manifest_version, dataset_key, kind, schema_version, created_at, source, files array with filename/row_count/checksum/size_bytes)
- [x] T005 Write TDD unit tests for manifest module in analytics_exporter/tests/test_manifest.py: test compute_sha256 on a temp file returns correct hex digest, test write_manifest produces valid JSON with required fields, test multi-file manifest has correct files array length, test zero-byte file produces valid checksum
- [x] T006 Add `_export_snapshot_with_manifest()` wrapper in analytics_exporter/run.py that calls `_export_full_snapshot_dataset()` then `write_manifest()` for single-file datasets; add `_export_multi_file_dataset(config, log, dataset_key, schema_names)` that exports all schemas atomically (both succeed or cleanup partial files) then writes a combined manifest per R-004
- [x] T007 Update KNOWN_DATASETS in analytics_exporter/run.py to the 18-dataset catalog (dim_player, dim_content_hierarchy, dim_review_item, dim_season, dim_academic_plan, fact_interaction, fact_memory_state, fact_practice, fact_subscription, fact_voucher, fact_challenge_attempt, fact_challenge_detail, fact_structure_progress, fact_player_wallet, dim_lesson_stage, fact_content_report, fact_live_challenge_event, fact_live_challenge_participation, fact_archive_job, fact_task_run_log, fact_build_queue) plus multi-file group aliases (fact_challenge, fact_live_challenge, fact_task_run); refactor `orchestrate_exports()` dispatch skeleton with dimension -> core fact -> supplementary ordering
- [x] T008 Refactor `_export_practice_log()` in analytics_exporter/run.py to use `fact_practice` schema name and dataset key, generate manifest after successful export, update watermark key from `practice_log` to `fact_practice`; rename analytics_exporter/tests/test_practice_log.py to analytics_exporter/tests/test_fact_practice.py and update imports/dataset references

**Checkpoint**: Foundation ready — manifest generation works, orchestration skeleton dispatches all 18 datasets, multi-file atomicity in place.

---

## Phase 3: User Story 1 — Export Dimension Datasets for Reference Lookups (Priority: P1)

**Goal**: Export 5 dimension tables (players, content hierarchy, review items, seasons, academic plans) as Parquet files with manifests, providing complete reference data for all fact table joins.

**Independent Test**: Trigger dimension export and verify 5 valid Parquet files produced (dim_player, dim_content_hierarchy, dim_review_item, dim_season, dim_academic_plan), each with manifest.json containing SHA-256 and row count, each with expected columns and no null primary keys.

### Tests for User Story 1

> **NOTE: Write these tests FIRST, ensure they FAIL before implementation**

- [x] T009 [P] [US1] Write integration test for dim_player export in analytics_exporter/tests/test_dim_player.py: insert test player rows (TEST-DP-* prefix) into tabMemora Player Profile, export dim_player dataset, verify Parquet has 8 columns per contract (player_id, display_name, grade_id, major_id, season_id, gender, language, registered_at), verify no null player_id, verify sensitive fields (mobile, password) are NOT present, cleanup test rows
- [x] T010 [P] [US1] Write integration test for dim_content_hierarchy export in analytics_exporter/tests/test_dim_content_hierarchy.py: use existing hierarchy_rows() fixture to insert published lesson with full Subject->Track->Unit->Topic path, export dim_content_hierarchy dataset, verify Parquet has 16 columns per contract (lesson_id through stage_types), verify denormalized titles are present, verify stage_count and stage_types from subqueries, verify only published lessons included (insert unpublished lesson, confirm excluded)
- [x] T011 [P] [US1] Write integration test for dim_review_item export in analytics_exporter/tests/test_dim_review_item.py: insert test review items with stage_id, stage_type, question_text, correct_choice populated, export dim_review_item dataset, verify Parquet has 8 columns per contract (item_id through correct_choice), verify no null item_id, verify new columns (stage_id, stage_type, question_text, correct_choice) present and populated
- [x] T012 [P] [US1] Write integration test for dim_season export in analytics_exporter/tests/test_dim_season.py: use existing academic_context_rows() to insert test season with is_published=1, export dim_season dataset, verify Parquet has 6 columns per contract (season_id, season_title, season_seq, start_date, end_date, is_published), verify is_published column present (new vs. old schema)
- [x] T013 [P] [US1] Write integration test for dim_academic_plan export in analytics_exporter/tests/test_dim_academic_plan.py: insert test academic plan with grade/major JOINs and Plan Subject child records, export dim_academic_plan, verify Parquet has 11 columns per contract (plan_id through subject_list), verify denormalized grade_title and major_title present, verify subject_list from GROUP_CONCAT subquery

### Implementation for User Story 1

- [x] T014 [P] [US1] Create analytics_exporter/schemas/dim_player.yaml: dataset=dim_player, output_file=dim_player.parquet, mode=full_snapshot, sql_full SELECT from tabMemora Player Profile with column aliases per data-model.md (name AS player_id, display_name, grade AS grade_id, major AS major_id, season AS season_id, gender, preferred_lang AS language, creation AS registered_at), DQ rules DQ-DP-01..03
- [x] T015 [P] [US1] Create analytics_exporter/schemas/dim_content_hierarchy.yaml: dataset=dim_content_hierarchy, mode=full_snapshot, sql_full with LEFT JOINs on Subject/Track/Unit/Topic and correlated subqueries for stage_count (COUNT) and stage_types (GROUP_CONCAT DISTINCT) per R-007 SQL, WHERE is_published=1, 16 columns per contract, DQ rules DQ-CH-01..03
- [x] T016 [P] [US1] Create analytics_exporter/schemas/dim_review_item.yaml: dataset=dim_review_item, mode=full_snapshot, sql_full SELECT item_id, subject, topic, lesson, stage_id, stage_type, question_text, correct_choice from tabMemora Review Item, 8 columns per contract, DQ rules DQ-RI-01..02
- [x] T017 [P] [US1] Create analytics_exporter/schemas/dim_season.yaml: dataset=dim_season, mode=full_snapshot, sql_full SELECT name AS season_id, season_title, season_seq, start_date, end_date, is_published from tabMemora Season ORDER BY season_seq, 6 columns per contract, DQ rules DQ-DS-01..05
- [x] T018 [P] [US1] Create analytics_exporter/schemas/dim_academic_plan.yaml: dataset=dim_academic_plan, mode=full_snapshot, sql_full with LEFT JOINs on Grade/Major and correlated subquery for subject_list (GROUP_CONCAT from tabMemora Plan Subject), 11 columns per contract, DQ rules DQ-AP-01..02
- [x] T019 [US1] Wire 5 dimension datasets into orchestrate_exports() in analytics_exporter/run.py: dispatch dim_player, dim_content_hierarchy, dim_review_item, dim_season, dim_academic_plan via _export_snapshot_with_manifest(), add to dimension export group (runs first before facts)

**Checkpoint**: All 5 dimension Parquet files export with correct schemas and manifests. Run `pytest analytics_exporter/tests/test_dim_*.py -v`.

---

## Phase 4: User Story 2 — Export Core Fact Datasets for Learning and Business Analytics (Priority: P1)

**Goal**: Export 6 core fact datasets (interactions, memory state, practice, subscriptions, vouchers, challenges) as Parquet files, enabling learning performance and business revenue analytics.

**Independent Test**: Trigger fact export and verify 8 files produced (fact_interaction, fact_memory_state, fact_practice, fact_subscription, fact_voucher, fact_challenge_attempt + fact_challenge_detail), each with correct schemas and valid manifest files.

### Tests for User Story 2

> **NOTE: Write these tests FIRST, ensure they FAIL before implementation**

- [x] T020 [P] [US2] Write integration test for fact_interaction date-range export in analytics_exporter/tests/test_fact_interaction.py: insert test interaction log rows with timestamps in a known range, export fact_interaction with ANALYTICS_INTERACTION_FROM/TO set, verify Parquet has 10 columns per contract (event_id through client_metadata), verify only rows in date range included, verify no null event_id/player_id
- [x] T021 [P] [US2] Write integration test for fact_memory_state export in analytics_exporter/tests/test_fact_memory_state.py: verify BIN_TO_UUID converts binary item_id to UUID text string, verify CAST converts DECIMAL stability/difficulty to float64, verify Parquet has 12 columns per contract (ms_id through fsrs_step), verify no null ms_id/player_id/item_id; must handle the BIGINT PK and partitioned table structure
- [x] T022 [P] [US2] Write integration test for fact_subscription export in analytics_exporter/tests/test_fact_subscription.py: insert test subscription with linked transaction, export fact_subscription, verify Parquet has 8 columns per contract (player_id through txn_status), verify LEFT JOIN produces null payment fields when transaction missing
- [x] T023 [P] [US2] Write integration test for fact_voucher export in analytics_exporter/tests/test_fact_voucher.py: verify Parquet has 12 columns per contract (serial_no through allocated_to), verify JOIN on batch and LEFT JOIN on allocation, verify face_value exported as float (not Decimal), verify no null serial_no/batch_id
- [x] T024 [P] [US2] Write integration test for fact_challenge multi-file export in analytics_exporter/tests/test_fact_challenge.py: insert test challenge attempt with detail rows, export fact_challenge group, verify two Parquet files produced (fact_challenge_attempt with 13 columns, fact_challenge_detail with 5 columns per contract), verify combined manifest has both files in files array, verify atomic cleanup if one export fails

### Implementation for User Story 2

- [x] T025 [P] [US2] Create analytics_exporter/schemas/fact_interaction.yaml: dataset=fact_interaction, mode=date_range, sql_full SELECT from tabMemora Interaction Log with WHERE timestamp BETWEEN %s AND %s, column aliases per data-model.md (name AS event_id, player AS player_id, time_spent AS time_spent_sec, timestamp AS event_ts), 10 columns, DQ rules DQ-FI-01..03
- [x] T026 [P] [US2] Create analytics_exporter/schemas/fact_memory_state.yaml: dataset=fact_memory_state, mode=full_snapshot, sql_full SELECT with BIN_TO_UUID(item_id) AS item_id, CAST(stability AS DOUBLE), CAST(difficulty AS DOUBLE) per R-005 SQL, column aliases per data-model.md, 12 columns, DQ rules DQ-MS-01..04
- [x] T027 [P] [US2] Create analytics_exporter/schemas/fact_subscription.yaml: dataset=fact_subscription, mode=full_snapshot, sql_full with LEFT JOIN tabMemora Subscription Transaction on player, column aliases per data-model.md, 8 columns, DQ rules DQ-FS-01..02
- [x] T028 [P] [US2] Create analytics_exporter/schemas/fact_voucher.yaml: dataset=fact_voucher, mode=full_snapshot, sql_full with JOIN tabMemora Voucher Batch and LEFT JOIN tabMemora Voucher Allocation, column aliases per data-model.md, 12 columns, DQ rules DQ-FV-01..04
- [x] T029 [P] [US2] Create analytics_exporter/schemas/fact_challenge_attempt.yaml (13 columns, DQ-CA-01..03) and analytics_exporter/schemas/fact_challenge_detail.yaml (5 columns, DQ-CD-01..02) per contract; both mode=full_snapshot, multi_file_group=fact_challenge
- [x] T030 [US2] Wire 6 core fact datasets into orchestrate_exports() in analytics_exporter/run.py: add fact_interaction with date-range parameter passing (compute from/to from config, default last 30 days); add fact_memory_state, fact_subscription, fact_voucher as snapshot exports; add fact_challenge as multi-file export via _export_multi_file_dataset(); add `_export_date_range_dataset()` helper for interaction log
- [x] T031 [US2] Verify core fact tests pass: run `pytest analytics_exporter/tests/test_fact_interaction.py test_fact_memory_state.py test_fact_subscription.py test_fact_voucher.py test_fact_challenge.py test_fact_practice.py -v`

**Checkpoint**: All 8 core fact Parquet files export with correct schemas, type conversions, JOINs, date-range filtering, multi-file atomicity, and manifests.

---

## Phase 5: User Story 4 — Verify Export Integrity via Manifests (Priority: P1)

**Goal**: Verify that every Parquet file is accompanied by a manifest.json with SHA-256 checksum and row count that can be independently verified by the analytics server.

**Independent Test**: Export any dataset, read manifest.json, independently compute SHA-256 of the Parquet file, confirm checksum and row count match exactly.

### Tests for User Story 4

- [x] T032 [US4] Write integration tests for manifest end-to-end verification in analytics_exporter/tests/test_manifest.py: export a real single-file dataset (e.g., dim_season), read its manifest.json, independently compute SHA-256 of the Parquet file with hashlib, confirm checksum matches manifest exactly, confirm row count matches pq.read_table().num_rows, confirm size_bytes matches os.path.getsize()
- [x] T033 [US4] Write integration tests for manifest edge cases in analytics_exporter/tests/test_manifest.py: test zero-row dataset produces manifest with row_count=0 and valid SHA-256; test multi-file dataset (fact_challenge) produces manifest with multiple entries in files array; test manifest is only written after Parquet file is fully written (simulate write failure, verify no orphan manifest)

**Checkpoint**: SHA-256 and row count independently verifiable for all export types. Manifests never written for partial/failed exports.

---

## Phase 6: User Story 3 — Export Supplementary Datasets for Specialized Reports (Priority: P2)

**Goal**: Export 7 additional datasets (structure progress, player wallet, lesson stages, content reports, live challenges, archive jobs, task runs) for specialized analytics reports.

**Independent Test**: Trigger supplementary export and verify 9 files produced with correct schemas, valid manifests, and non-null primary keys.

### Tests for User Story 3

> **NOTE: Write these tests FIRST, ensure they FAIL before implementation**

- [x] T034 [P] [US3] Write integration tests for single-file supplementary datasets in analytics_exporter/tests/test_fact_supplementary.py: test fact_structure_progress (4 columns, no null player_id/subject_id), test fact_player_wallet (7 columns, unique player_id), test dim_lesson_stage (6 columns with LEFT JOIN settings, no null stage_id/lesson_id), test fact_content_report (8 columns, no null player_id), test fact_archive_job (11 columns, no null job_id/source_doctype); each test inserts test data, exports, verifies columns and DQ rules per contract
- [x] T035 [P] [US3] Write integration tests for multi-file supplementary datasets in analytics_exporter/tests/test_fact_supplementary.py: test fact_live_challenge exports two files (event with 9 columns + participation with 7 columns) with combined manifest; test fact_task_run exports two files (task_run_log with 10 columns + build_queue with 8 columns) with combined manifest; verify atomic cleanup on partial failure

### Implementation for User Story 3

- [x] T036 [P] [US3] Create analytics_exporter/schemas/fact_structure_progress.yaml: dataset=fact_structure_progress, mode=full_snapshot, sql_full SELECT player AS player_id, subject AS subject_id, completion_percentage AS completion_pct, passed_lessons_bitset from tabMemora Structure Progress, 4 columns, DQ rules DQ-SP-01..02
- [x] T037 [P] [US3] Create analytics_exporter/schemas/fact_player_wallet.yaml: dataset=fact_player_wallet, mode=full_snapshot, sql_full SELECT player AS player_id, total_xp, total_lessons, total_time_min, current_streak, daily_xp_json, last_sync_at from tabMemora Player Wallet, 7 columns, DQ rules DQ-PW-01..02
- [x] T038 [P] [US3] Create analytics_exporter/schemas/dim_lesson_stage.yaml: dataset=dim_lesson_stage, mode=full_snapshot, sql_full with LEFT JOIN tabMemora Lesson Stage Settings on stage_id, 6 columns per contract (stage_id, lesson_id, stage_type, is_skippable, default_stage_time, is_time_calculated), DQ rules DQ-LS-01..03
- [x] T039 [P] [US3] Create analytics_exporter/schemas/fact_content_report.yaml: dataset=fact_content_report, mode=full_snapshot, sql_full SELECT player AS player_id, subject AS subject_id, lesson AS lesson_id, report_type, description, status, creation AS created_at, modified AS resolved_at from tabMemora Content Report, 8 columns, DQ rules DQ-CR-01
- [x] T040 [P] [US3] Create analytics_exporter/schemas/fact_live_challenge_event.yaml (9 columns, DQ-LE-01..02) and analytics_exporter/schemas/fact_live_challenge_participation.yaml (7 columns, DQ-LP-01..02) per contract; both mode=full_snapshot, multi_file_group=fact_live_challenge
- [x] T041 [P] [US3] Create analytics_exporter/schemas/fact_archive_job.yaml: dataset=fact_archive_job, mode=full_snapshot, sql_full SELECT name AS job_id, source_doctype, status, archive_scope, started_at, completed_at, duration_seconds, row_count, file_size_bytes, retry_count, error_log from tabMemora Archive Job, 11 columns, DQ rules DQ-AJ-01..03
- [x] T042 [P] [US3] Create analytics_exporter/schemas/fact_task_run_log.yaml (10 columns, DQ-TL-01..02) and analytics_exporter/schemas/fact_build_queue.yaml (8 columns, DQ-BQ-01..02) per contract; both mode=full_snapshot, multi_file_group=fact_task_run
- [x] T043 [US3] Wire 7 supplementary datasets into orchestrate_exports() in analytics_exporter/run.py: add fact_structure_progress, fact_player_wallet, dim_lesson_stage, fact_content_report, fact_archive_job as snapshot exports; add fact_live_challenge and fact_task_run as multi-file exports via _export_multi_file_dataset()

**Checkpoint**: All 9 supplementary Parquet files export with correct schemas and manifests. Run `pytest analytics_exporter/tests/test_fact_supplementary.py -v`.

---

## Phase 7: Polish & Cross-Cutting Concerns

**Purpose**: End-to-end validation, logging improvements, cleanup.

- [x] T044 Update analytics_exporter/tests/conftest.py with table name constants and test data helpers for new source tables (tabMemora Player Profile, tabMemora Interaction Log, tabMemora Player Subscription, tabMemora Subscription Transaction, tabMemora Voucher Card, tabMemora Voucher Batch, tabMemora Voucher Allocation, tabMemora Challenge Attempt, tabMemora Challenge Attempt Detail, tabMemora Structure Progress, tabMemora Player Wallet, tabMemora Lesson Stage Settings, tabMemora Content Report, tabMemora Live Challenge Event, tabMemora Live Challenge Participation, tabMemora Task Run Log, tabMemora Build Queue)
- [x] T045 [P] Update logging in analytics_exporter/run.py main() to print per-dataset summary line matching quickstart.md format: `[dataset] rows=N duration=Xs status=ok|failed`
- [x] T046 End-to-end validation: run full export against real DB with all 18 datasets, verify all ~22 Parquet files and manifests produced in analytics_exports/, verify quickstart.md expected output matches actual
- [x] T047 Run quickstart.md validation: execute the exact commands from quickstart.md and verify output format, exit codes, and file layout match

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: No dependencies — can start immediately
- **Foundational (Phase 2)**: Depends on Setup completion — BLOCKS all user stories
- **US1 Dimensions (Phase 3)**: Depends on Foundational phase
- **US2 Core Facts (Phase 4)**: Depends on Foundational phase; can run in parallel with US1
- **US4 Manifests (Phase 5)**: Depends on at least US1 or US2 being complete (needs real exports to verify)
- **US3 Supplementary (Phase 6)**: Depends on Foundational phase; can run in parallel with US1/US2
- **Polish (Phase 7)**: Depends on all user stories being complete

### User Story Dependencies

- **US1 (P1)**: Can start after Foundational — no dependency on other stories
- **US2 (P1)**: Can start after Foundational — no dependency on other stories (fact_practice already migrated in Phase 2)
- **US4 (P1)**: Needs at least one dataset exportable — depends on US1 or US2
- **US3 (P2)**: Can start after Foundational — no dependency on US1/US2 (all use independent source tables)

### Within Each User Story

- Tests MUST be written and FAIL before schema implementation (TDD)
- YAML schemas before wiring into orchestrate_exports()
- Wire into orchestrate_exports() last (depends on schemas existing)
- Verify tests pass as checkpoint

### Parallel Opportunities

- **Phase 1**: T002 and T003 can run in parallel (different files)
- **Phase 2**: T004 and T005 can run in parallel, T006/T007/T008 sequential (same file: run.py)
- **Phase 3**: All test tasks T009-T013 can run in parallel; all schema tasks T014-T018 can run in parallel
- **Phase 4**: All test tasks T020-T024 can run in parallel; all schema tasks T025-T029 can run in parallel
- **Phase 6**: All test tasks T034-T035 can run in parallel; all schema tasks T036-T042 can run in parallel
- **Cross-story**: US1, US2, and US3 can all proceed in parallel after Phase 2

---

## Parallel Example: User Story 1

```bash
# Launch all dimension test files together (TDD — write first, expect failures):
Task T009: "Integration test for dim_player in test_dim_player.py"
Task T010: "Integration test for dim_content_hierarchy in test_dim_content_hierarchy.py"
Task T011: "Integration test for dim_review_item in test_dim_review_item.py"
Task T012: "Integration test for dim_season in test_dim_season.py"
Task T013: "Integration test for dim_academic_plan in test_dim_academic_plan.py"

# Then launch all dimension schemas together:
Task T014: "Create dim_player.yaml"
Task T015: "Create dim_content_hierarchy.yaml"
Task T016: "Create dim_review_item.yaml"
Task T017: "Create dim_season.yaml"
Task T018: "Create dim_academic_plan.yaml"
```

---

## Implementation Strategy

### MVP First (US1 + US4 Only)

1. Complete Phase 1: Setup (remove old schemas, update config)
2. Complete Phase 2: Foundational (manifest module, orchestration refactor)
3. Complete Phase 3: US1 — Dimension Datasets
4. Complete Phase 5: US4 — Manifest Verification
5. **STOP and VALIDATE**: 5 dimension Parquet files with verified manifests
6. Deploy if analytics server needs dimension data immediately

### Incremental Delivery

1. Setup + Foundational -> Infrastructure ready
2. US1 Dimensions -> 5 Parquet files with manifests (reference data available)
3. US2 Core Facts -> 8 more Parquet files (learning + business analytics enabled)
4. US4 Manifests -> End-to-end integrity verification confirmed
5. US3 Supplementary -> 9 more Parquet files (full report catalog)
6. Polish -> Production-ready with validated quickstart

### Parallel Team Strategy

With multiple developers after Phase 2:

1. Developer A: US1 (Dimensions) -> US4 (Manifest verification)
2. Developer B: US2 (Core Facts)
3. Developer C: US3 (Supplementary)
4. All stories complete and integrate independently via orchestrate_exports()

---

## Notes

- [P] tasks = different files, no dependencies
- [Story] label maps task to specific user story for traceability
- Each user story should be independently completable and testable
- TDD mandatory: write integration tests first, verify they fail, then implement schemas
- Commit after each task or logical group
- Stop at any checkpoint to validate story independently
- YAML schemas follow existing pattern in analytics_exporter/schemas/ (see fact_practice.yaml for reference)
- All new datasets use `_export_full_snapshot_dataset()` or `_export_multi_file_dataset()` — no custom export functions needed
- Multi-file groups (fact_challenge, fact_live_challenge, fact_task_run) share a single manifest with multiple files array entries
