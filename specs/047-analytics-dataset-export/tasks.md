# Tasks: Educational Analytics Dataset Export

**Input**: Design documents from `/specs/047-analytics-dataset-export/`
**Prerequisites**: plan.md ✅, spec.md ✅, research.md ✅, data-model.md ✅, contracts/ ✅, quickstart.md ✅

**Tests**: Included per Constitution Principle VIII (Test-First Coverage — mandatory TDD).

**Organization**: Tasks grouped by user story to enable independent implementation and testing.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependencies on incomplete tasks)
- **[Story]**: Which user story this task belongs to ([US1]–[US4])

---

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: Create the `analytics_exporter/` module scaffold before any implementation begins.

- [x] T001 Create analytics_exporter/ directory structure: `analytics_exporter/__init__.py`, `analytics_exporter/__main__.py` (entry: `python3 -m analytics_exporter`), `analytics_exporter/schemas/` (empty), `analytics_exporter/tests/__init__.py`
- [x] T002 [P] Create analytics_exporter/requirements.txt with `pyarrow>=14.0,<19.0`, `pymysql>=1.1,<2.0`, `pyyaml>=6.0,<7.0`
- [x] T003 [P] Create analytics_exporter/config.py — `Config` frozen dataclass with `from_env()`: DB_HOST, DB_PORT, DB_USER, DB_PASSWORD, DB_NAME, ANALYTICS_OUTPUT_PATH (default: `analytics_exports`), ANALYTICS_SCHEMA_PATH (default: `analytics_exporter/schemas`), ANALYTICS_CHUNK_SIZE (default: 50000), ANALYTICS_LOG_PATH, ANALYTICS_MODE (default: `auto`), ANALYTICS_DATASETS (default: all)

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Core infrastructure shared by all four user stories. All unit tests written and failing before implementation (TDD).

**⚠️ CRITICAL**: No user story work can begin until this phase is complete.

- [x] T004 Create analytics_exporter/db.py — `get_connection(config) -> pymysql.Connection` issuing `SET SESSION TRANSACTION ISOLATION LEVEL READ COMMITTED` immediately on open; `streaming_cursor(config)` context manager using `SSDictCursor`; `validate_identifier(name)` allowlist check (mirrors `archive_executor/db.py` pattern)
- [x] T005 [P] Write failing unit tests for exporter utilities in analytics_exporter/tests/test_exporter_unit.py — test `_sql_type_to_arrow()` for all SQL types (INT, FLOAT, DECIMAL, DATETIME, DATE, VARCHAR, ENUM, TINYINT), `_coerce_value()` for None/Decimal/date/string cases, `_rows_to_batch()` with mixed types
- [x] T006 [P] Write failing unit tests for watermark in analytics_exporter/tests/test_watermark.py — test load returns None when file absent, load returns dict when file exists, save writes atomically (using `os.replace()`), interrupted save does not corrupt existing watermark
- [x] T007 [P] Write failing unit tests for validator in analytics_exporter/tests/test_validator.py — test `unique_key` catches duplicates, `not_null` catches nulls, `min_value` catches negatives, `min_rows` catches empty tables, all rules pass on clean data
- [x] T008 Create analytics_exporter/exporter.py — implement `_sql_type_to_arrow()`, `_coerce_value()`, `_rows_to_batch()` (unit tests T005 now pass); `write_parquet(table, path)` (writes empty Parquet with correct schema if zero rows); `export_snapshot(config, sql, params, columns, schema_def) -> (path, int)` using `streaming_cursor`, `ParquetWriter`, chunked batch writes
- [x] T009 [P] Create analytics_exporter/watermark.py — `load_watermark(watermark_path) -> dict | None`; `save_watermark(watermark_path, data: dict)` with atomic `os.replace()` via `.tmp` file (unit tests T006 now pass)
- [x] T010 [P] Create analytics_exporter/validator.py — `validate_export(parquet_path, dq_rules: list[dict]) -> list[str]` (returns list of violation messages; empty = pass); supports rule types: `unique_key`, `not_null`, `min_value`, `min_rows` (unit tests T007 now pass)
- [x] T011 [P] Create analytics_exporter/run.py skeleton — `load_schema(schema_path) -> dict` (reads YAML); `create_output_dir(path)`; `orchestrate_exports(config, log) -> dict[str, ExportResult]` (dispatches per-schema; placeholder — no datasets wired yet); `main()` (load config, set up logger, call orchestrate_exports, return exit code 0/1/2)
- [x] T012 [P] Create analytics_exporter/tests/conftest.py — `db_conn` fixture (direct pymysql connection, READ COMMITTED); `practice_log_rows(conn)` insert/cleanup helper (rows with `TEST-PL-` prefix player_ids); `review_item_rows(conn)` helper (items with `TEST-RI-` prefix); `hierarchy_rows(conn)` helper (subject/track/unit/topic/lesson with `TEST-HI-` prefix); `academic_context_rows(conn)` helper (seasons/grades/majors/plans/grade_majors with `TEST-AC-` prefix)

**Checkpoint**: Foundation ready — all utility unit tests pass, conftest fixtures available, run.py main() runs without error.

---

## Phase 3: User Story 1 — Export Practice Log (Priority: P1) 🎯 MVP

**Goal**: Export all practice log rows to `analytics_exports/practice_log.parquet` with 7 required fields; support incremental watermark mode for efficient re-runs.

**Independent Test**: Run `python3 -m analytics_exporter` with `ANALYTICS_DATASETS=practice_log`; verify `analytics_exports/practice_log.parquet` exists with correct schema, zero duplicate `(player_id, item_id)` rows, zero-value rows included; run a second time and verify only delta rows were queried; verify `.watermark.json` updated.

### Implementation for User Story 1

- [x] T013 [US1] Write failing integration tests in analytics_exporter/tests/test_practice_log.py — covers all 4 acceptance scenarios: (1) full export produces 7-field Parquet with no duplicate PKs; (2) incremental re-export queries only rows with `last_seen_at > watermark` and merges correctly; (3) rows with `correct_count=0` / `last_result='Incorrect'` are included without modification; (4) connection uses READ COMMITTED (verify via `SELECT @@transaction_isolation` in same connection); (5) zero-row practice log produces valid empty Parquet with correct schema (verify `.watermark.json` not written on error)
- [x] T014 [P] [US1] Create analytics_exporter/schemas/practice_log.yaml — `dataset: practice_log`, `output_file: practice_log.parquet`, `mode: incremental_watermark`, `watermark_column: last_seen_at`, `primary_key: [player_id, item_id]`; full_snapshot SQL and incremental SQL (with `WHERE last_seen_at > %s`); 7-column schema_snapshot; DQ rules DQ-PL-01 through DQ-PL-05 from contracts/output-parquet-schemas.yaml
- [x] T015 [US1] Implement `export_incremental(config, existing_path, delta_sql, params, columns, schema_def, pk_columns) -> (path, int)` in analytics_exporter/exporter.py — loads existing Parquet via `pq.read_table()`, runs delta query via `streaming_cursor`, concatenates tables, deduplicates by PK (keep last/delta row wins), writes merged table back to path
- [x] T016 [US1] Wire practice_log export in analytics_exporter/run.py `orchestrate_exports()` — load `schemas/practice_log.yaml`; if mode=`incremental_watermark` and watermark exists and `ANALYTICS_MODE != full`: call `export_incremental()`; otherwise call `export_snapshot()`; call `validate_export()` on output; update watermark via `save_watermark()` only on success; log result

**Checkpoint**: `practice_log.parquet` exported correctly in full and incremental modes; all T013 tests pass.

---

## Phase 4: User Story 2 — Export Item → Curriculum Mapping (Priority: P1)

**Goal**: Export `analytics_exports/item_mapping.parquet` mapping each review item to its full curriculum path; items without complete curriculum assignment excluded.

**Independent Test**: Export `item_mapping.parquet`; verify each `item_id` has non-null `lesson_id`, `topic_id`, `unit_id`, `track_id`, `subject_id`; join with `practice_log.parquet` on `item_id` produces zero unmatched rows for items with curriculum assignments.

### Implementation for User Story 2

- [x] T017 [US2] Write failing integration tests in analytics_exporter/tests/test_item_mapping.py — covers 3 acceptance scenarios: (1) export produces 6-field Parquet with no null columns, one row per item_id; (2) items with null/empty `lesson` excluded from output; (3) every active item_id in practice log resolves to a row in item_mapping (LEFT JOIN produces zero nulls)
- [x] T018 [P] [US2] Create analytics_exporter/schemas/item_mapping.yaml — `mode: full_snapshot`; SQL with null/empty filter on all 5 hierarchy columns per data-model.md canonical query; 6-column schema_snapshot; DQ rules DQ-IM-01 through DQ-IM-04
- [x] T019 [US2] Wire item_mapping export in analytics_exporter/run.py `orchestrate_exports()` — load `schemas/item_mapping.yaml`, call `export_snapshot()`, call `validate_export()`, log result
- [x] T020 [US2] Verify join integrity in integration test: load both `practice_log.parquet` (from T016) and `item_mapping.parquet` using PyArrow, perform LEFT JOIN on `item_id`, assert zero null `lesson_id` values for active items (adds assertion to test_item_mapping.py)

**Checkpoint**: `item_mapping.parquet` exported; SC-003 and SC-004 satisfied; all T017 tests pass.

---

## Phase 5: User Story 3 — Export Content Hierarchy (Priority: P2)

**Goal**: Export five hierarchy Parquet files (`subjects`, `tracks`, `units`, `topics`, `lessons`) for curriculum rollups; includes published and unpublished entities; parent refs form valid tree.

**Independent Test**: Export all five hierarchy files; verify `lessons → topics → units → tracks → subjects` chain: every `topic_id` in lessons.parquet resolves to a row in topics.parquet; every `unit_id` in topics.parquet resolves to units.parquet, etc.; parent nodes with no children still exported.

### Implementation for User Story 3

- [x] T021 [US3] Write failing integration tests in analytics_exporter/tests/test_hierarchy.py — covers 3 acceptance scenarios: (1) five files produced with correct fields and `id`/`name` columns; (2) full hierarchy traversal resolves (no orphaned FK refs); (3) unpublished entities are included (insert an `is_published=0` lesson and verify it appears in output)
- [x] T022 [P] [US3] Create analytics_exporter/schemas/subjects.yaml, tracks.yaml, units.yaml — full_snapshot SQL per data-model.md canonical queries; column schema_snapshots; DQ rules per contracts/output-parquet-schemas.yaml
- [x] T023 [P] [US3] Create analytics_exporter/schemas/topics.yaml, lessons.yaml — full_snapshot SQL; column schema_snapshots; DQ rules
- [x] T024 [US3] Wire all five hierarchy exports in analytics_exporter/run.py `orchestrate_exports()` — load and dispatch subjects, tracks, units, topics, lessons schemas (all `mode: full_snapshot`); call `validate_export()` for each; log results per dataset

**Checkpoint**: Five hierarchy Parquet files produced; hierarchy tree resolves completely; all T021 tests pass.

---

## Phase 6: User Story 4 — Export Academic Context (Priority: P2)

**Goal**: Export five academic context Parquet files (`seasons`, `grades`, `majors`, `academic_plans`, `grade_majors`) for performance segmentation; all records included regardless of published status.

**Independent Test**: Export all five academic context files; verify `academic_plans → seasons/grades/majors` FKs all resolve; `grade_majors → grades/majors` resolves; draft plans and all seasons included.

### Implementation for User Story 4

- [x] T025 [US4] Write failing integration tests in analytics_exporter/tests/test_academic_context.py — covers 3 acceptance scenarios: (1) five files produced with correct required fields per spec FR-013–FR-018; (2) academic_plans FKs (`season`, `grade`, `major`) resolve to rows in their respective files; (3) unpublished plans included (insert `is_published=0` plan, verify it appears in output)
- [x] T026 [P] [US4] Create analytics_exporter/schemas/seasons.yaml, grades.yaml, majors.yaml — full_snapshot SQL; column schema_snapshots (seasons includes `season_seq` INT, `start_date` DATE, `end_date` DATE); DQ rules per contracts/output-parquet-schemas.yaml
- [x] T027 [P] [US4] Create analytics_exporter/schemas/academic_plans.yaml, grade_majors.yaml — full_snapshot SQL (grade_majors uses `WHERE parenttype = 'Memora Grade'`); column schema_snapshots; DQ rules
- [x] T028 [US4] Wire all five academic context exports in analytics_exporter/run.py `orchestrate_exports()` — load and dispatch seasons, grades, majors, academic_plans, grade_majors schemas; call `validate_export()` for each; log results

**Checkpoint**: Five academic context Parquet files produced; all FK refs resolve; SC-005 and SC-008 satisfied; all T025 tests pass.

---

## Phase 7: Polish & Cross-Cutting Concerns

**Purpose**: CLI ergonomics, end-to-end validation, and hardening.

- [x] T029 [P] Add `ANALYTICS_DATASETS` filtering to analytics_exporter/run.py `orchestrate_exports()` — parse comma-separated dataset names from env; skip datasets not in the list; validate dataset names against known schemas at startup
- [x] T030 [P] Add `ANALYTICS_MODE` override to analytics_exporter/run.py — `full`: ignore watermark and always call `export_snapshot()` for practice_log; `incremental`: fail with exit code 2 if no watermark exists; `auto` (default): use watermark if present, else full
- [x] T031 [P] Create run_analytics_export.sh — wrapper script that sets env vars from a `.env` file if present and invokes `python3 -m analytics_exporter`; suitable for cron/supervisor invocation
- [x] T032 End-to-end validation per quickstart.md — run full export against test DB; verify all 12 Parquet files exist; run incremental pass; verify `.watermark.json` updated; read all 12 files with PyArrow and verify row counts > 0; join `practice_log → item_mapping → lessons → topics → units → tracks → subjects` end-to-end and verify SC-001 (zero unresolved FK refs); join `academic_plans → seasons/grades/majors` and verify SC-005

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: No dependencies — start immediately
- **Foundational (Phase 2)**: Depends on Phase 1 completion — **BLOCKS all user stories**
- **US1 (Phase 3)**: Depends on Phase 2; no dependency on US2–US4
- **US2 (Phase 4)**: Depends on Phase 2; may reference US1 output for join test (T020) but independently testable
- **US3 (Phase 5)**: Depends on Phase 2; no dependency on US1–US2
- **US4 (Phase 6)**: Depends on Phase 2; no dependency on US1–US3
- **Polish (Phase 7)**: Depends on all user story phases

### User Story Dependencies

- **US1 (P1)**: Foundational complete → implement independently
- **US2 (P1)**: Foundational complete → implement independently (join test uses US1 output but US2 itself is independent)
- **US3 (P2)**: Foundational complete → implement independently
- **US4 (P2)**: Foundational complete → implement independently

### Within Each User Story

1. Write tests first (TDD — tests must FAIL before implementation)
2. Create YAML schema(s) [P] — parallelizable with test writing
3. Implement exporter extension (if needed)
4. Wire in run.py
5. Verify tests pass

### Parallel Opportunities

- T002, T003 parallel with each other (Phase 1)
- T005, T006, T007, T009, T011, T012 parallel within Phase 2 (all different files)
- Once Phase 2 complete: US1, US2, US3, US4 can run in parallel across team members
- Within each story: test task and YAML schema task(s) are parallel ([P] marked)
- T029, T030, T031 parallel within Phase 7

---

## Parallel Example: User Story 3

```bash
# After Phase 2 complete, launch US3 in parallel:
Task A: T021 — Write failing integration tests in tests/test_hierarchy.py
Task B: T022 [P] — Create schemas/subjects.yaml, tracks.yaml, units.yaml
Task C: T023 [P] — Create schemas/topics.yaml, lessons.yaml

# Then sequentially:
Task D: T024 — Wire hierarchy exports in run.py (depends on T022, T023)
```

---

## Implementation Strategy

### MVP First (US1 Only)

1. Complete Phase 1: Setup (T001–T003)
2. Complete Phase 2: Foundational (T004–T012) — CRITICAL, blocks all stories
3. Complete Phase 3: US1 Practice Log (T013–T016)
4. **STOP and VALIDATE**: `python3 -m analytics_exporter` produces `practice_log.parquet`, passes DQ, incremental mode works
5. Demo to analytics engineer — can compute per-item metrics immediately

### Incremental Delivery

1. Setup + Foundational → module ready
2. US1 (Practice Log) → analytics server has learning outcomes data (**MVP**)
3. US2 (Item Mapping) → can now aggregate per-item metrics to lesson level
4. US3 (Hierarchy) → full curriculum rollups available (lesson → topic → unit → track → subject)
5. US4 (Academic Context) → segmentation by season/grade/major/plan enabled
6. Polish → production-hardened CLI

### Parallel Team Strategy

With two developers after Phase 2:
- **Dev A**: US1 + US2 (both P1, practice log + item mapping)
- **Dev B**: US3 + US4 (both P2, hierarchy + academic context, no dependency on Dev A)

---

## Summary

| Phase | Tasks | Parallel | User Story |
|---|---|---|---|
| Phase 1: Setup | T001–T003 | T002, T003 | — |
| Phase 2: Foundational | T004–T012 | T005–T007, T009, T011, T012 | — |
| Phase 3: US1 Practice Log | T013–T016 | T014 | US1 |
| Phase 4: US2 Item Mapping | T017–T020 | T018 | US2 |
| Phase 5: US3 Hierarchy | T021–T024 | T022, T023 | US3 |
| Phase 6: US4 Academic Context | T025–T028 | T026, T027 | US4 |
| Phase 7: Polish | T029–T032 | T029, T030, T031 | — |
| **Total** | **32 tasks** | **17 parallelizable** | |

**MVP Scope**: Phases 1–3 (T001–T016) — delivers working practice log export.

---

## Notes

- [P] tasks touch different files with no cross-dependencies
- Test tasks are written FIRST per Constitution Principle VIII — verify they FAIL before implementing
- Each user story has an independent test criterion in its phase header — use it as acceptance check
- Watermark safety: never update `.watermark.json` if export or DQ validation failed
- Avoid: calling `_process_pending_jobs()` from archive_executor in any test — use only analytics_exporter fixtures
- `tabMemora Grade Major` child table query must include `WHERE parenttype = 'Memora Grade'` — see research.md R-008
