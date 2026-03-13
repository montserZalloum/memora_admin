# Tasks: Memory State Archive Lifecycle

**Input**: Design documents from `/specs/041-memory-state-archive/`
**Prerequisites**: plan.md, spec.md, research.md, data-model.md, contracts/

**Tests**: Not included (not explicitly requested). Test infrastructure (conftest fixtures) included in Polish phase for future test authoring.

**Organization**: Tasks are grouped by user story to enable independent implementation and testing of each story.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependencies)
- **[Story]**: Which user story this task belongs to (e.g., US1, US2, US3)
- Include exact file paths in descriptions

---

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: Archive type schema and executor configuration for Memory State

- [x] T001 Create archive type schema at `archive_schemas/archive_types/memory_state.v1.yaml` with season-scoped fact SQL (full export + incremental), 14 fact columns with `BIN_TO_UUID(item_id)` conversion, player/season dimensions, and 11 DQ rules per the contract
- [x] T002 [P] Add sync configuration fields (`sync_state_path`, `sync_output_path`, `sync_overlap_seconds`, `sync_remote_path`) with env-var defaults to the `Config` frozen dataclass in `archive_executor/config.py`

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Core modifications to existing modules that MUST be complete before any user story can be implemented

**CRITICAL**: No user story work can begin until this phase is complete

- [x] T003 Modify `export_fact_data()` in `archive_executor/exporter.py` to detect `filter_type=season` in `query_filter` and bind a single `season_seq` parameter instead of date-range parameters — existing date-based behavior unchanged when `filter_type` is absent
- [x] T004 [P] Add `create_season_archive_jobs()` function, `_build_season_job_meta()` helper, and `--mode season` CLI argument to `archive_executor/scheduler.py` — queries `tabMemora Season` for ended seasons (`end_date < CURDATE()`) without an existing non-Failed archive job, creates one Pending job per eligible season with `archive_scope=season_N`

**Checkpoint**: Foundation ready — user story implementation can now begin

---

## Phase 3: User Story 1 — Incremental Analytics Sync for Active Seasons (Priority: P1) MVP

**Goal**: Incrementally sync current Memory State data for all active seasons to the analytics current mirror, reflecting changes within one sync cycle (15 min target)

**Independent Test**: Run a sync cycle for an active season and verify that only changed rows (based on `modified` timestamp with safety overlap) are upserted into the analytics current mirror, and that row counts match expectations

### Implementation for User Story 1

- [x] T005 [US1] Create `archive_executor/sync.py` with `_discover_active_seasons()` that queries `tabMemora Season` for published seasons where `end_date >= CURDATE()`, and `_load_checkpoint()`/`_save_checkpoint()` for per-season JSON files at `{sync_state_path}/memory_state/season_{N}.json`
- [x] T006 [US1] Implement `_extract_incremental()` in `archive_executor/sync.py` — computes `extract_from = last_checkpoint - safety_overlap`, executes the incremental fact SQL with streaming cursor, returns rows and max `modified` timestamp
- [x] T007 [US1] Implement `_export_sync_parquet()` in `archive_executor/sync.py` — writes extracted rows to Parquet at `{sync_output_path}/memory_state/season_{N}/sync_{timestamp}.parquet` with injected `synced_at` and `archive_scope` metadata columns
- [x] T008 [US1] Implement `_transfer_and_ingest()` in `archive_executor/sync.py` — rsync Parquet to `{sync_remote_path}/memory_state/season_{N}/`, call `memora-analytics ingest-live --batch-dir {remote_path}`, cleanup local Parquet on success
- [x] T009 [US1] Add `_is_season_archived()` in `archive_executor/sync.py` — checks if a non-Failed archive job exists for the season (`tabMemora Archive Job` WHERE `source_doctype='Memora Memory State'` AND `archive_scope=season_{N}`); when true, skip sync for that season
- [x] T010 [US1] Implement `run_incremental_sync(config, archive_type)` orchestrator in `archive_executor/sync.py` — iterates active seasons, loads checkpoint, extracts/exports/transfers/ingests for each, updates checkpoint only after successful ingestion; add `__main__` CLI entry point with `--archive-type` argument and JSON stdout summary

**Checkpoint**: Incremental sync operational — active season data flows to analytics within the sync interval

---

## Phase 4: User Story 2 — Season Archive Export and Validation (Priority: P1)

**Goal**: Export a final season snapshot when a season becomes archive-eligible and validate its completeness before allowing any production cleanup

**Independent Test**: Mark a season as ended, trigger the archive export via scheduler + executor, verify the Parquet output matches the source row count and integrity checks, confirm the archive job record reflects the validation outcome

### Implementation for User Story 2

- [x] T011 [US2] Modify `_export_job()` in `archive_executor/run.py` to detect season-scoped jobs via `job_meta.query_filter.filter_type == "season"` — pass `season_seq` to `export_fact_data()`, handle derived season dimension export (generate season dimension from `tabMemora Season` WHERE `season_seq = N` instead of JOIN-based referencing), ensure DQ validation runs with `memory_state.v1.yaml` rules

**Checkpoint**: Season archive export and validation pipeline functional — ended seasons produce validated Parquet archives

---

## Phase 5: User Story 3 — Analytics Archive Storage and Current Mirror Cleanup (Priority: P2)

**Goal**: Store archived season data as compressed Parquet in per-season directories and remove that season from the analytics current mirror so dashboards remain fast

**Independent Test**: Archive a completed season, verify Parquet files exist at `archive/memory_state/season_{N}/`, confirm the season's rows are removed from the current mirror table

### Implementation for User Story 3

- [x] T012 [US3] Add `handoff_season()` function to `archive_executor/ingestion.py` — calls `memora-analytics handoff --archive-batch-dir DIR --season-seq N --archive-type memory_state` via SSH, parses JSON response with rows_removed count
- [x] T013 [US3] Modify `_process_ingested_jobs()` in `archive_executor/run.py` to detect season-scoped jobs and call `handoff_season()` instead of `handoff_archive()` — pass `season_seq` from `job_meta.query_filter` and `archive_type` from job record

**Checkpoint**: Archived seasons are stored as Parquet and removed from the current mirror — dashboards only query active seasons

---

## Phase 6: User Story 4 — Production Cleanup with Safety Gates (Priority: P2)

**Goal**: Gate production cleanup behind archive validation, dependency review, and active-linkage checks — proceed with `DROP PARTITION` only when all gates pass

**Independent Test**: Attempt cleanup for an archived season — verify blocked when player/plan linkage exists, verify blocked when archive validation hasn't succeeded, verify `DROP PARTITION` proceeds only when all four gates pass

### Implementation for User Story 4

- [x] T014 [US4] Create `archive_executor/safety_gates.py` with `GateCheck`/`GateResult` dataclasses and four gate functions: `_check_archive_validation()` (Completed/Purged job exists), `_check_player_linkage()` (no player profiles linked), `_check_plan_linkage()` (no published plans linked), `_check_partition_exists()` (partition `p_season_N` found in INFORMATION_SCHEMA.PARTITIONS); implement `check_all_gates(config, season_name, season_seq)` that runs all four and returns aggregate result with blocker messages
- [x] T015 [US4] Add `_purge_partition()` to `archive_executor/purge.py` — calls `check_all_gates()` before executing `ALTER TABLE \`tabMemora Memory State\` DROP PARTITION p_season_{N}`, logs to `archive_delete_audit_log`, marks job Purged; modify `purge_completed_jobs()` to detect season-scoped jobs (via `filter_type=season` in `job_meta`) and route to `_purge_partition()` instead of batched DELETE

**Checkpoint**: Production cleanup is fully gated — irreversible `DROP PARTITION` only executes when all safety checks pass

---

## Phase 7: User Story 5 — Per-Season Sync and Archive Metadata Tracking (Priority: P3)

**Goal**: Maintain per-season control metadata on both production and analytics sides so that sync state, archive state, and cleanup eligibility are tracked independently for each season

**Independent Test**: Run sync and archive operations across multiple seasons simultaneously and verify that each season's metadata is tracked and updated independently

### Implementation for User Story 5

- [x] ~~T016 [US5] Add `get_mirror_status()` function~~ — **Removed from production contract**: `mirror-status` is not called by the production executor pipeline

**Checkpoint**: Per-season metadata is observable — operators can monitor sync state, mirror state, and archive state independently for each season

---

## Phase 8: Polish & Cross-Cutting Concerns

**Purpose**: Test infrastructure, error handling hardening, and end-to-end validation

- [x] T017 [P] Add Memory State test constants (`MEMORY_STATE_TABLE`, season prefixes, `ALL_MS_TEST_JOBS`), insert/delete helpers for `tabMemora Memory State` rows (with `UUID_TO_BIN()` for `item_id`), and season fixtures to `archive_executor/tests/conftest.py`
- [x] T018 [P] Validate error path resilience — ensure extraction, transfer, and ingestion failures in `archive_executor/sync.py` do not advance the checkpoint and log actionable error messages
- [x] T019 Run quickstart.md verification commands to validate schema registry, incremental sync, season scheduling, executor pipeline, and partition cleanup end-to-end

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: No dependencies — can start immediately
- **Foundational (Phase 2)**: Depends on Setup completion — BLOCKS all user stories
- **US1 (Phase 3)**: Depends on Foundational — can start after T003/T004 complete
- **US2 (Phase 4)**: Depends on Foundational — can start after T003/T004 complete
- **US3 (Phase 5)**: Depends on US2 (archive export must work before mirror cleanup makes sense)
- **US4 (Phase 6)**: Depends on US2 (safety gates verify archive completion) and US3 (mirror cleanup should complete before partition drop)
- **US5 (Phase 7)**: Depends on US1 and US3 (metadata tracking spans both sync and archive)
- **Polish (Phase 8)**: Depends on all user stories being complete

### User Story Dependencies

- **US1 (P1)**: Can start after Foundational (Phase 2) — no dependencies on other stories
- **US2 (P1)**: Can start after Foundational (Phase 2) — no dependencies on other stories
- **US3 (P2)**: Depends on US2 (archive export must produce validated Parquet before mirror cleanup)
- **US4 (P2)**: Depends on US2 + US3 (safety gates check archive completion; mirror should be cleaned first)
- **US5 (P3)**: Depends on US1 + US3 (monitoring spans both sync and archive operations)

### Within Each User Story

- Foundation modules (exporter, scheduler) before story-specific modules
- Core logic before orchestration (e.g., extraction before `run_incremental_sync()`)
- Production-side changes before analytics-side integration
- Safety checks before destructive operations

### Parallel Opportunities

- **Phase 1**: T001 and T002 can run in parallel (different files)
- **Phase 2**: T003 and T004 can run in parallel (different files)
- **Phase 3+4**: US1 (sync.py) and US2 (run.py) can start in parallel after Foundational
- **Phase 6**: T014 (safety_gates.py) is a new file — can start as soon as US2 completes
- **Phase 8**: T017 and T018 can run in parallel (different files)

---

## Parallel Example: US1 + US2 After Foundational

```bash
# After Phase 2 completes, launch US1 and US2 in parallel:

# Stream 1 — US1: Incremental Sync
Task: T005 "Create sync.py with discovery + checkpoint"
Task: T006 "Implement incremental extraction"
Task: T007 "Implement Parquet export + transfer"
Task: T008 "Add sync pause detection"
Task: T009 "Implement orchestrator + CLI"

# Stream 2 — US2: Season Archive Export
Task: T011 "Modify _export_job() for season scope"
```

---

## Implementation Strategy

### MVP First (US1 Only)

1. Complete Phase 1: Setup (T001–T002)
2. Complete Phase 2: Foundational (T003–T004)
3. Complete Phase 3: US1 — Incremental Sync (T005–T010)
4. **STOP and VALIDATE**: Run sync for an active season, verify analytics mirror updated
5. Deploy sync on 15-minute cron schedule

### Incremental Delivery

1. Setup + Foundational → Foundation ready
2. Add US1 (Incremental Sync) → Test → Deploy sync cron (MVP!)
3. Add US2 (Season Archive Export) → Test → Validate Parquet output
4. Add US3 (Mirror Cleanup) → Test → Confirm dashboard performance
5. Add US4 (Safety Gates + DROP PARTITION) → Test → Enable production cleanup
6. Add US5 (Metadata Monitoring) → Test → Operator visibility
7. Each story adds value without breaking previous stories

### Key Integration Points

| Stage | Executor Module | Analytics CLI Command |
|-------|----------------|----------------------|
| Incremental sync | `sync.py` | `ingest-live` |
| Archive ingest | `run.py` (existing) | `ingest-archive` |
| Mirror cleanup | `ingestion.py` → `run.py` | `handoff --season-seq` |
| Archive verify | `run.py` (existing) | `verify` |
| ~~Mirror monitoring~~ | ~~`ingestion.py`~~ | ~~`mirror-status`~~ (removed from production contract) |

---

## Notes

- [P] tasks = different files, no dependencies
- [Story] label maps task to specific user story for traceability
- All changes to existing modules (exporter, scheduler, run, ingestion, purge) are additive — existing date-based pipeline continues unchanged
- Season-scoped behavior is triggered by `filter_type=season` in `job_meta.query_filter`
- `DROP PARTITION` is irreversible — safety gates are mandatory, never optional
- Sync checkpoint is only advanced after successful analytics ingestion
- `p_future` partition is never dropped (validated by Gate 4 pattern check)
