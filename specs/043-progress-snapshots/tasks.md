# Tasks: Weekly Structure Progress Snapshots

**Input**: Design documents from `/specs/043-progress-snapshots/`
**Prerequisites**: plan.md, spec.md, research.md, data-model.md, contracts/

**Tests**: Integration tests are included — the spec requires pytest integration tests matching the existing `archive_executor/tests/` patterns (see plan.md Technical Context and quickstart.md).

**Organization**: Tasks are grouped by user story to enable independent implementation and testing of each story.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependencies)
- **[Story]**: Which user story this task belongs to (e.g., US1, US2, US3)
- Include exact file paths in descriptions

## Path Conventions

- **Source**: `archive_executor/` (existing package at repo root)
- **Tests**: `archive_executor/tests/` (existing test directory)
- **Schemas**: `archive_schemas/snapshot_types/` (new subdirectory)

---

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: Add snapshot-specific configuration and schema definition so the pipeline module and tests can reference them.

- [x] T001 Add `snapshot_output_path` field to `Config` dataclass and `from_env()` in `archive_executor/config.py`, reading from `SNAPSHOT_OUTPUT_PATH` env var with a sensible default (e.g., `/data/memora/snapshots/`)
- [x] T002 [P] Create `archive_schemas/snapshot_types/` directory and add `structure_progress.v1.yaml` schema file defining: snapshot_type, version, source tables, fact_columns (`snapshot_date`, `player_id`, `plan_id`, `subject_id`, `completion_percentage`), extraction SQL (valid rows INNER JOIN with plan NOT NULL, and rejected row COUNT query), Arrow types, and DQ rules (DQ-SP-01 through DQ-SP-08) per `data-model.md` validation rules
- [x] T003 [P] Create empty `archive_executor/snapshot.py` module with module docstring, imports for `pyarrow`, `pymysql`, `os`, `datetime`, and placeholder constants: `SNAPSHOT_SCHEMA` (pyarrow schema from `contracts/parquet-schema.md`), `_DATASET_KEY = "structure_progress_snapshot"`, `_FACT_FILENAME = "fact_structure_progress.parquet"`

**Checkpoint**: Config extended, schema YAML created, module skeleton ready.

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Core extraction and writing functions that ALL user stories depend on.

**Warning**: No user story work can begin until this phase is complete.

- [x] T004 Implement `_extract_valid_rows(conn, snapshot_date) -> SSDictCursor` in `archive_executor/snapshot.py` — executes the INNER JOIN extraction SQL from `data-model.md` (parameterized `snapshot_date`, `ORDER BY sp.player, sp.subject`) using `archive_executor.db.streaming_cursor()` pattern; returns the streaming cursor for chunk-based reading
- [x] T005 Implement `_count_rejected_rows(conn) -> tuple[int, int]` in `archive_executor/snapshot.py` — executes the LEFT JOIN rejected-row COUNT query from `data-model.md`; returns `(no_profile_count, null_plan_count)`
- [x] T006 Implement `_write_parquet(rows_iter, snapshot_date, staging_dir) -> tuple[str, int]` in `archive_executor/snapshot.py` — streams rows from cursor into a pyarrow Table using `SNAPSHOT_SCHEMA`, writes `fact_structure_progress.parquet` to `staging_dir` with Snappy compression; returns `(file_path, row_count)`. Handle empty result set (0 rows) by writing valid empty Parquet with correct schema per FR-013
- [x] T007 Implement `_build_snapshot_manifest(staging_dir, snapshot_date, parquet_path, row_count) -> str` in `archive_executor/snapshot.py` — calls `archive_executor.manifest.build_manifest()` with `dataset_key="structure_progress_snapshot"`, `kind="snapshot"`, `batch_id=f"SNAP-{snapshot_date}"`, `schema_version="1.0"`, `source="memora_admin"`, `scope_key=snapshot_date`; computes SHA-256 checksum and file size of the Parquet file; returns manifest path
- [x] T008 Implement `_atomic_swap(staging_dir, final_dir) -> None` in `archive_executor/snapshot.py` — if `final_dir` exists, rename to `{final_dir}.old`, rename `staging_dir` to `final_dir`, then delete `.old`; if staging has stale `.staging` dir from previous crash, clean it up before starting

**Checkpoint**: All building-block functions ready. User story implementation can begin.

---

## Phase 3: User Story 1 — Weekly Progress Snapshot Generation (Priority: P1) MVP

**Goal**: End-to-end pipeline that extracts structure progress with plan enrichment, writes Parquet partition, and builds manifest. Triggered manually or by cron.

**Independent Test**: Run `python -m archive_executor.snapshot --snapshot-date 2026-03-08` and verify a Parquet file + manifest appear at `{snapshot_output_path}/structure_progress/2026-03-08/` with correct columns and row count matching source.

### Tests for User Story 1

> **NOTE: Write these tests FIRST, ensure they FAIL before implementation**

- [x] T009 [P] [US1] Create `archive_executor/tests/test_snapshot.py` with test fixtures: DB connection setup (reuse `conftest.py` patterns), helper to insert test rows into `tabMemora Structure Progress` and `tabMemora Player Profile` with unique test prefixes (e.g., `SNAP-PLYR-001`, `SNAP-SUBJ-001`), and teardown that deletes test rows; use a temp directory for snapshot output
- [x] T010 [P] [US1] Add integration test `test_snapshot_basic_export` in `archive_executor/tests/test_snapshot.py` — insert 5 structure progress rows for 2 students across 3 subjects (all with valid player profiles and plans), run the snapshot pipeline, verify: Parquet file exists, row count = 5, all 5 columns present, `snapshot_date` matches, `player_id`/`plan_id`/`subject_id` match inserted data
- [x] T011 [P] [US1] Add integration test `test_snapshot_manifest_integrity` in `archive_executor/tests/test_snapshot.py` — after a snapshot run, verify: `manifest.json` exists, `dataset_key` = `"structure_progress_snapshot"`, `kind` = `"snapshot"`, `batch_id` starts with `SNAP-`, `row_count` matches Parquet row count, `checksum` is a valid sha256 hex string

### Implementation for User Story 1

- [x] T012 [US1] Implement `run_snapshot(config, snapshot_date=None) -> dict` in `archive_executor/snapshot.py` — orchestrator function that: (1) resolves `snapshot_date` to the most recent Sunday if not provided, (2) creates staging dir at `{config.snapshot_output_path}/structure_progress/.staging/{snapshot_date}/`, (3) cleans up stale staging if it exists, (4) opens DB connection, (5) calls `_extract_valid_rows` + `_write_parquet`, (6) calls `_count_rejected_rows` and logs warnings, (7) calls `_build_snapshot_manifest`, (8) calls `_atomic_swap` to final dir `{config.snapshot_output_path}/structure_progress/{snapshot_date}/`, (9) returns summary dict with `snapshot_date`, `row_count`, `rejected_no_profile`, `rejected_null_plan`
- [x] T013 [US1] Add `__main__` block to `archive_executor/snapshot.py` — parse `--snapshot-date` and `--dry-run` CLI args via `argparse`, load `Config.from_env()`, call `run_snapshot()`, log result summary via `StructuredLogger`; for `--dry-run`, extract and validate but skip the atomic swap step

**Checkpoint**: US1 complete — full snapshot pipeline works end-to-end with CLI support.

---

## Phase 4: User Story 2 — Plan-Aware Snapshot Correctness (Priority: P1)

**Goal**: Verify that plan_id is correctly resolved per student and that plan changes between snapshots are properly reflected as distinct plan_ids.

**Independent Test**: Insert a student on Plan A with progress, run snapshot, change student to Plan B with reset progress, run another snapshot for a different date, verify plan_ids differ across the two snapshots.

### Tests for User Story 2

- [x] T014 [P] [US2] Add integration test `test_snapshot_plan_enrichment` in `archive_executor/tests/test_snapshot.py` — insert 3 students each on different plans, run snapshot, verify each row's `plan_id` matches the student's profile plan (not just non-null, but the exact value)
- [x] T015 [P] [US2] Add integration test `test_snapshot_plan_change_across_weeks` in `archive_executor/tests/test_snapshot.py` — insert student on Plan A with 80% completion, snapshot for date X, update profile to Plan B and progress to 0%, snapshot for date Y, verify: date X snapshot has `plan_id = Plan A, completion = 80`, date Y snapshot has `plan_id = Plan B, completion = 0`, both snapshots coexist as separate partition directories

**Checkpoint**: US2 complete — plan correctness verified across plan changes.

---

## Phase 5: User Story 3 — Idempotent Rerun Safety (Priority: P2)

**Goal**: Rerunning the snapshot for the same date overwrites cleanly with identical output and no duplicates.

**Independent Test**: Run snapshot for date X twice, verify file contents are byte-identical and no duplicate rows exist.

### Tests for User Story 3

- [x] T016 [P] [US3] Add integration test `test_snapshot_idempotent_rerun` in `archive_executor/tests/test_snapshot.py` — insert test data, run snapshot for date X, record Parquet file bytes and manifest checksum, run snapshot again for same date X, verify: Parquet bytes identical, manifest `row_count` unchanged, `checksum` matches, no duplicate rows in output
- [x] T017 [P] [US3] Add integration test `test_snapshot_overwrite_with_changed_source` in `archive_executor/tests/test_snapshot.py` — run snapshot for date X with 5 rows, add 2 more source rows, rerun for same date X, verify: new Parquet has 7 rows (overwrite, not append), old snapshot fully replaced

### Implementation for User Story 3

- [x] T018 [US3] Verify `_atomic_swap` handles the overwrite case in `archive_executor/snapshot.py` — ensure that when `final_dir` already exists, the rename-to-`.old` → swap → delete-`.old` sequence works correctly; add a log message indicating overwrite of existing snapshot

**Checkpoint**: US3 complete — reruns are safe and idempotent.

---

## Phase 6: User Story 4 — Missing Plan Rejection (Priority: P2)

**Goal**: Students with no profile or null plan are excluded from output and rejections are counted and logged.

**Independent Test**: Insert structure progress rows for students without profiles and with null plans, run snapshot, verify those rows are absent from output and rejection counts are logged.

### Tests for User Story 4

- [x] T019 [P] [US4] Add integration test `test_snapshot_rejects_no_profile` in `archive_executor/tests/test_snapshot.py` — insert 3 structure progress rows for a student with NO matching player profile, plus 2 valid rows, run snapshot, verify: output has exactly 2 rows, summary reports `rejected_no_profile = 3`
- [x] T020 [P] [US4] Add integration test `test_snapshot_rejects_null_plan` in `archive_executor/tests/test_snapshot.py` — insert a player profile with `plan = NULL` and 2 structure progress rows for that student, plus 3 valid rows, run snapshot, verify: output has exactly 3 rows, summary reports `rejected_null_plan = 2`
- [x] T021 [P] [US4] Add integration test `test_snapshot_empty_table` in `archive_executor/tests/test_snapshot.py` — run snapshot with no structure progress rows (or all rejected), verify: Parquet file is written with correct schema and 0 rows, manifest `row_count = 0`, no errors raised (FR-013)

### Implementation for User Story 4

- [x] T022 [US4] Ensure `run_snapshot` in `archive_executor/snapshot.py` logs per-category rejection counts via `StructuredLogger` — log `rejected_no_profile` and `rejected_null_plan` as structured fields at WARNING level, and total rejected as part of the summary INFO log

**Checkpoint**: US4 complete — data quality enforced with observable rejection metrics.

---

## Phase 7: User Story 5 — Weekly Trend Analytics (Priority: P3)

**Goal**: Multiple weekly snapshots can be queried together to produce time-series trends per student per subject.

**Independent Test**: Generate 3+ snapshots for consecutive weeks with varying completions, query across all partitions, verify continuous time series.

### Tests for User Story 5

- [x] T023 [US5] Add integration test `test_snapshot_multi_week_trend` in `archive_executor/tests/test_snapshot.py` — insert a student with Math progress at 20%, snapshot for week 1; update to 50%, snapshot for week 2; update to 80%, snapshot for week 3; read all 3 Parquet files, verify: 3 rows for the student-subject pair with increasing `completion_percentage`, each with correct `snapshot_date`

**Checkpoint**: US5 complete — trend analytics dataset validated across multiple snapshots.

---

## Phase 8: Polish & Cross-Cutting Concerns

**Purpose**: DQ validation, logging improvements, and documentation.

- [x] T024 [P] Wire DQ validation into `run_snapshot` in `archive_executor/snapshot.py` — after Parquet write, load `structure_progress.v1.yaml` and run the generic DQ validator (`archive_executor.validator`) against the output; fail the run if any DQ rule is violated (not_null, min_value, max_value, unique_key per DQ-SP-01 through DQ-SP-08)
- [x] T025 [P] Add integration test `test_snapshot_dq_validation` in `archive_executor/tests/test_snapshot.py` — verify that DQ validation runs as part of the pipeline and passes for valid data
- [x] T026 Run `quickstart.md` validation — manually execute the commands in `specs/043-progress-snapshots/quickstart.md` against the implemented code to verify the developer guide is accurate

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: No dependencies — can start immediately
- **Foundational (Phase 2)**: Depends on Phase 1 completion — BLOCKS all user stories
- **US1 (Phase 3)**: Depends on Phase 2 — core pipeline
- **US2 (Phase 4)**: Depends on Phase 2 — can run in parallel with US1 (tests only verify join correctness, which is built in Phase 2)
- **US3 (Phase 5)**: Depends on US1 T012 (`run_snapshot` must exist for rerun testing)
- **US4 (Phase 6)**: Depends on Phase 2 — can run in parallel with US1 (rejection logic is in the extraction SQL)
- **US5 (Phase 7)**: Depends on US1 T012 (`run_snapshot` must exist to generate multi-week data)
- **Polish (Phase 8)**: Depends on all user stories being complete

### User Story Dependencies

- **US1 (P1)**: After Phase 2 — no dependencies on other stories
- **US2 (P1)**: After Phase 2 — no dependencies on other stories (verifies join correctness already built in foundational)
- **US3 (P2)**: After US1 T012 — needs `run_snapshot` to test reruns
- **US4 (P2)**: After Phase 2 — no dependencies on other stories (rejection is inherent in the SQL)
- **US5 (P3)**: After US1 T012 — needs `run_snapshot` to generate multi-week snapshots

### Within Each User Story

- Tests MUST be written and FAIL before implementation
- Foundational functions (Phase 2) before orchestrator (US1)
- Orchestrator before rerun/trend testing (US3, US5)

### Parallel Opportunities

- T002 and T003 can run in parallel (different files)
- T009, T010, T011 can run in parallel (same file but independent test functions)
- US2 tests (T014, T015) can run in parallel with US1 implementation (T012, T013)
- US4 tests (T019, T020, T021) can run in parallel with US1 implementation
- T024 and T025 can run in parallel

---

## Parallel Example: User Story 1

```bash
# Launch all US1 tests together (they will FAIL initially):
Task: T009 "Create test fixtures in archive_executor/tests/test_snapshot.py"
Task: T010 "Test basic export in archive_executor/tests/test_snapshot.py"
Task: T011 "Test manifest integrity in archive_executor/tests/test_snapshot.py"

# Then implement sequentially:
Task: T012 "Implement run_snapshot orchestrator in archive_executor/snapshot.py"
Task: T013 "Add __main__ CLI block in archive_executor/snapshot.py"
```

---

## Implementation Strategy

### MVP First (User Story 1 Only)

1. Complete Phase 1: Setup (T001–T003)
2. Complete Phase 2: Foundational (T004–T008)
3. Complete Phase 3: US1 tests + implementation (T009–T013)
4. **STOP and VALIDATE**: Run `python -m archive_executor.snapshot --snapshot-date 2026-03-08` and verify output
5. Run tests: `python -m pytest archive_executor/tests/test_snapshot.py -v`

### Incremental Delivery

1. Setup + Foundational → Pipeline building blocks ready
2. US1 → Full pipeline works → **MVP complete**
3. US2 → Plan correctness verified → Confidence in join logic
4. US3 + US4 → Idempotency + rejection safety → Operational reliability
5. US5 → Multi-week trend validation → Analytics readiness
6. Polish → DQ validation wired in → Production quality

### Single Developer Recommended Order

Phase 1 → Phase 2 → Phase 3 (US1) → Phase 4 (US2) → Phase 6 (US4) → Phase 5 (US3) → Phase 7 (US5) → Phase 8

Note: US4 before US3 because rejection logic is already inherent in the SQL — just needs test verification. US3 (idempotency) and US5 (trends) need `run_snapshot` to exist first.

---

## Notes

- All source code goes in `archive_executor/snapshot.py` (single new module per R-001)
- All tests go in `archive_executor/tests/test_snapshot.py` (single new test file)
- Schema YAML goes in `archive_schemas/snapshot_types/structure_progress.v1.yaml`
- Config change is a 2-line addition to existing `archive_executor/config.py`
- No Frappe dependencies — pure Python with pymysql + pyarrow
- Reuses `archive_executor.db`, `archive_executor.manifest`, `archive_executor.logger`, `archive_executor.validator`
- Test data prefixes: `SNAP-PLYR-*`, `SNAP-SUBJ-*`, `SNAP-PLAN-*` to avoid collision with existing test data
