# Tasks: Interaction Log Archiving

**Input**: Design documents from `/specs/040-interaction-log-archive/`
**Prerequisites**: plan.md, spec.md, research.md, data-model.md, contracts/

**Tests**: Included — plan.md explicitly specifies test files (`test_generic_dq_validator.py`, `test_interaction_log_pipeline.py`).

**Organization**: Tasks are grouped by user story to enable independent implementation and testing of each story.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependencies)
- **[Story]**: Which user story this task belongs to (e.g., US1, US2, US3)
- Include exact file paths in descriptions

## Path Conventions

```
archive_executor/          # Standalone Python CLI executor (no Frappe runtime)
archive_schemas/           # YAML registry (archive types + dimensions)
```

---

## Phase 1: Setup (Schema & Configuration)

**Purpose**: Create YAML schemas and ensure database readiness — no executor code changes.

- [x] T001 [P] Create archive type schema per contract in `archive_schemas/archive_types/interaction_log.v1.yaml` — define fact_columns, scope_column, fact_sql (filtered + full_snapshot), export_metadata, dimensions, schema_snapshot, and dq_rules (DQ-01 through DQ-13)
- [x] T002 [P] Create lesson dimension schema per contract in `archive_schemas/dimensions/lesson.v1.yaml` — define entity, version, source_table, id_column, fields, and SQL with JOIN to `tabMemora Topic`
- [x] T003 Add `idx_timestamp` index on `tabMemora Interaction Log`.`timestamp` column if not already present — verify with `SHOW INDEX FROM`

---

## Phase 2: Foundational (Generic DQ Validation Engine)

**Purpose**: Build the YAML-driven DQ rule engine that all Interaction Log validation depends on. MUST complete before any user story.

**⚠️ CRITICAL**: The Interaction Log pipeline cannot validate exported data without this engine.

- [x] T004 Implement `validate_fact_quality_generic()` in `archive_executor/validator.py` — rule engine supporting types: `not_null`, `enum_values`, `min_value`, `max_value`, `column_lte_column`, `scope_range`, `referential`, `unique_key` per `contracts/dq-validation-contract.md`
- [x] T005 Wire generic DQ dispatch in `archive_executor/run.py` — if archive type YAML contains `dq_rules`, call `validate_fact_quality_generic()` with rules, dimension paths, and scope dates; otherwise fall back to legacy `validate_fact_quality()`
- [x] T006 [P] Create unit tests for generic DQ engine in `archive_executor/tests/test_generic_dq_validator.py` — test each rule type (not_null, enum_values, min_value, scope_range, referential, unique_key) with mock Parquet data, including pass and fail cases

**Checkpoint**: Generic DQ engine ready — Interaction Log pipeline can now validate exports.

---

## Phase 3: User Story 1 — Automated Daily Archive of Old Interaction Records (Priority: P1) 🎯 MVP

**Goal**: The system automatically identifies interaction records older than the 14-day retention window, creates daily archive jobs, and exports them into verifiable Parquet batch files with correct record counts and checksums.

**Independent Test**: Run scheduler to create jobs for a date range, then run executor against a dataset with records spanning 30 days. Verify records older than 14 days are exported into batch files, recent records are untouched, and mid-batch failures are recoverable.

### Implementation for User Story 1

- [x] T007 [US1] Implement `create_pending_jobs()` and CLI entry point in `archive_executor/scheduler.py` — scan source table for MIN/MAX timestamp, compute archive window `[min_ts, NOW() - retention_days)`, create one Pending job per day (skipping days with existing non-Failed jobs), populate `job_meta` from YAML schema per `contracts/scheduler-interface.md`
- [x] T008 [US1] Add interaction log test constants, fixtures, and helper functions to `archive_executor/tests/conftest.py` — test player/lesson/interaction records with `timestamp` in 2099 range, cleanup helpers by prefix, job creation fixtures
- [x] T009 [US1] Add scheduler and export integration tests to `archive_executor/tests/test_interaction_log_pipeline.py` — test: job creation for correct date ranges, skipping existing jobs, zero-record graceful completion, export with correct record counts/checksums, mid-batch failure recovery

**Checkpoint**: Scheduler creates pending jobs and executor exports Interaction Log records into Parquet files.

---

## Phase 4: User Story 2 — Verified Transfer and Ingestion to Analytics (Priority: P2)

**Goal**: Exported batch files are transferred to the analytics server via SSH/SCP, ingested into the cumulative `interaction_log_raw` table (append-only, deduplicated by `name`), and integrity is verified via checksum comparison.

**Independent Test**: Archive a batch, transfer it, ingest it, then query `interaction_log_raw` to confirm all records are present with no duplicates. Re-ingest the same batch and verify zero duplicates.

### Implementation for User Story 2

- [x] T010 [US2] Verify and adapt ingestion pipeline for Interaction Log in `archive_executor/ingestion.py` — ensure `interaction_log_raw` table is created on first ingest with correct schema, records are appended cumulatively, and deduplication uses `name` field
- [x] T011 [US2] Add transfer and ingestion integration tests to `archive_executor/tests/test_interaction_log_pipeline.py` — test: transfer with checksum verification, cumulative ingestion (append not replace), deduplication on re-ingest, retry from transfer phase without re-export

**Checkpoint**: Batch files transfer to analytics and ingest into `interaction_log_raw` with deduplication.

---

## Phase 5: User Story 3 — Safe Production Deletion After Full Verification (Priority: P3)

**Goal**: Records are deleted from production only after all pipeline stages (export, transfer, ingest) are verified complete. Deletion happens in small batches (10K rows, 2s sleep) with an audit log recording every operation.

**Independent Test**: Run a complete archive cycle end-to-end and confirm: (a) deletion is blocked when ingestion failed, (b) deletion happens in batched DELETEs, (c) resumable after interruption, (d) audit log records job ID, row counts, batch size, duration, and status.

### Implementation for User Story 3

- [x] T012 [US3] Verify and adapt purge module for Frappe single-PK table (`name` column) with `timestamp` filter column — confirm batched `DELETE FROM ... WHERE timestamp >= %s AND timestamp < %s LIMIT %s` works correctly for `tabMemora Interaction Log`
- [x] T013 [US3] Add purge and audit log integration tests to `archive_executor/tests/test_interaction_log_pipeline.py` — test: deletion blocked when ingestion incomplete, batched DELETE (10K limit), resumable after interruption, audit log entry with all required fields (job_id, rows_deleted, batch_size, num_batches, duration, status)

**Checkpoint**: Archived records are safely deleted from production with full audit trail.

---

## Phase 6: User Story 4 — Recent Detailed Layer and Aggregations on Analytics (Priority: P4)

**Goal**: After ingestion, the analytics server maintains a 90-day recent detailed layer for fast queries, plus daily and monthly aggregate tables (interaction count, total time spent, total errors, completion rate) grouped by day/month + player + lesson + event_type.

**Independent Test**: Ingest batches spanning several months, run refresh commands, verify: recent layer contains only last 90 days, daily aggregates match raw data grouped by day, monthly aggregates match raw data grouped by month.

### Implementation for User Story 4

- [x] T014 [US4] Implement `refresh_recent()` in `archive_executor/ingestion.py` — rebuild `interaction_log_recent` as `SELECT * FROM interaction_log_raw WHERE timestamp >= CURRENT_DATE - INTERVAL {window_days} DAY` per `contracts/analytics-cli-extensions.md`
- [x] T015 [US4] Implement `refresh_aggregates()` in `archive_executor/ingestion.py` — rebuild `interaction_log_daily_agg` (GROUP BY date, player, lesson, event_type) and `interaction_log_monthly_agg` (GROUP BY month, player, lesson, event_type) with metrics: COUNT, SUM(time_spent), SUM(errors_count), completed_count, total_events per `contracts/analytics-cli-extensions.md`
- [x] T016 [US4] Add post-ingestion refresh calls in `archive_executor/run.py` — after successful ingestion, call `refresh-recent` and `refresh-aggregates` via SSH (best-effort, non-blocking; failure logged as warning, job still proceeds to Completed)
- [x] T017 [US4] Add integration tests for recent layer and aggregation refresh in `archive_executor/tests/test_interaction_log_pipeline.py` — test: recent layer contains only 90-day window, daily aggregates match raw data, monthly aggregates match raw data, refresh is idempotent, old records removed from recent layer on re-refresh

**Checkpoint**: Analytics server provides fast 90-day queries and historical aggregate reporting.

---

## Phase 7: User Story 5 — Batch Logging and Observability (Priority: P5)

**Goal**: Every archive batch has a complete log entry with: batch ID, table name, batch time range, process timestamps, record counts per phase, status per phase, error messages, and retry indicator. Failed batches log the failure phase and error. Retried batches reflect updated counts.

**Independent Test**: Run successful and failed archive cycles, verify every batch has a complete log entry with all required fields per FR-014.

### Implementation for User Story 5

- [x] T018 [US5] Verify and enhance batch logging for Interaction Log jobs in `archive_executor/run.py` — ensure all metadata fields per FR-014 are populated: batch ID, source_doctype (`Memora Interaction Log`), batch time range, start/end timestamps, record counts per phase (extracted, transferred, ingested, deleted), final status, retry indicator, error messages on failure
- [x] T019 [US5] Add logging completeness integration tests to `archive_executor/tests/test_interaction_log_pipeline.py` — test: successful batch has all metadata fields, failed batch contains failure phase and error, retried batch reflects retry status and updated counts

**Checkpoint**: Full audit trail and observability for all Interaction Log archive operations.

---

## Phase 8: Polish & Cross-Cutting Concerns

**Purpose**: End-to-end validation and cleanup across all stories.

- [x] T020 Run end-to-end integration test: scheduler → export → transfer → ingest → refresh-recent → refresh-aggregates → purge → verify zero duplicates, correct aggregates, 90-day recent window
- [x] T021 Validate schema registry with `python -m archive_executor.schemas validate` — confirm `interaction_log.v1` and `lesson.v1` load without errors
- [x] T022 Run quickstart.md verification commands — scheduler dry run, executor run, job status query

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: No dependencies — can start immediately
- **Foundational (Phase 2)**: Depends on Phase 1 (schemas must exist for DQ rules to reference)
- **US1 (Phase 3)**: Depends on Phase 2 — needs generic DQ engine for export validation
- **US2 (Phase 4)**: Depends on US1 — needs exported batch files to transfer/ingest
- **US3 (Phase 5)**: Depends on US2 — deletion only after verified ingestion
- **US4 (Phase 6)**: Depends on US2 — aggregation needs ingested data in `interaction_log_raw`
- **US5 (Phase 7)**: Depends on US1 — logging verified once pipeline runs (can overlap with US2-US4)
- **Polish (Phase 8)**: Depends on all user stories being complete

### User Story Dependencies

```
Phase 1 (Setup) → Phase 2 (DQ Engine) → US1 (Export) → US2 (Transfer/Ingest) ─┬→ US3 (Purge)
                                                                                 ├→ US4 (Analytics)
                                                         US1 ──────────────────→ US5 (Logging)
```

- **US1 (P1)**: First story after foundational — no other story dependencies
- **US2 (P2)**: Depends on US1 (needs exported batches)
- **US3 (P3)**: Depends on US2 (needs verified ingestion before deletion)
- **US4 (P4)**: Depends on US2 (needs `interaction_log_raw` populated)
- **US5 (P5)**: Depends on US1 (needs pipeline running to verify logs)

### Within Each User Story

- Implementation tasks before integration tests
- Core logic before wiring/orchestration
- Story complete before moving to next priority

### Parallel Opportunities

- T001 and T002 (Setup — different files)
- T006 can run in parallel with T004/T005 (different file, mock-based tests)
- US3 and US4 can run in parallel after US2 completes (independent concerns)
- US5 can overlap with US2–US4 (logging is observable once US1 runs)

---

## Parallel Example: Phase 1 (Setup)

```bash
# Launch in parallel (different files, no dependencies):
Task: T001 "Create interaction_log.v1.yaml in archive_schemas/archive_types/"
Task: T002 "Create lesson.v1.yaml in archive_schemas/dimensions/"
```

## Parallel Example: After US2 Completes

```bash
# US3 and US4 can run in parallel (independent concerns):
Task: T012 [US3] "Verify purge for Frappe single-PK table"
Task: T014 [US4] "Implement refresh_recent() in ingestion.py"
```

---

## Implementation Strategy

### MVP First (User Story 1 Only)

1. Complete Phase 1: Setup (schemas + index)
2. Complete Phase 2: Foundational (generic DQ engine)
3. Complete Phase 3: User Story 1 (scheduler + export)
4. **STOP and VALIDATE**: Create pending jobs, run executor, verify Parquet exports
5. Deploy scheduler + executor for Interaction Log

### Incremental Delivery

1. Setup + Foundational → Schemas registered, DQ engine ready
2. US1 → Scheduler creates jobs, executor exports → **MVP deployed**
3. US2 → Transfer + ingest to analytics raw layer → Data preserved off-production
4. US3 → Safe batched deletion with audit → Production table stays small
5. US4 → Aggregates + recent layer → Analytics reporting enabled
6. US5 → Logging verification → Full observability confirmed
7. Each story adds value without breaking previous stories

### Cron Schedule (Target)

```cron
# Create pending jobs at 01:30, run executor at 02:00
30 1 * * * python -m archive_executor.scheduler --archive-type interaction_log --retention-days 14
0  2 * * * python -m archive_executor.run
```

---

## Notes

- [P] tasks = different files, no dependencies on incomplete tasks
- [Story] label maps task to specific user story for traceability
- The existing pipeline (Pending → Processing → Exported → Transferred → Ingested → Completed → Purged) is already generic — primary work is YAML schemas, DQ engine, scheduler, and analytics extensions
- Practice Log behavior is fully preserved — all changes are additive (D-06)
- `name` field is the PK and deduplication key for Interaction Log (unlike Practice Log's composite PK)
- Test data uses `timestamp` in 2099 range to avoid production data collision
