# Tasks: Archive Job Cleanup

**Input**: Design documents from `/specs/045-archive-job-cleanup/`
**Prerequisites**: plan.md, spec.md, research.md, data-model.md, quickstart.md

**Tests**: Included — spec requires Test-First Coverage (Constitution VIII) with full test suite.

**Organization**: Tasks grouped by user story. US1 is the MVP (single Purged pass). Each subsequent story adds behavior incrementally.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependencies)
- **[Story]**: Which user story this task belongs to (e.g., US1, US2, US3)
- Include exact file paths in descriptions

---

## Phase 1: Setup

**Purpose**: Create file skeletons following the established cleanup task pattern

- [x] T001 Create `memora_admin/tasks/archive_job_cleanup.py` with module docstring, imports (`frappe`, `frappe.utils.add_days`, `frappe.utils.now_datetime`, `task_utils` observability helpers), constants (`TASK_NAME = "archive_job_cleanup"`, `DEFAULT_PURGED_RETENTION_DAYS = 30`, `DEFAULT_FAILED_RETENTION_DAYS = 90`, `DEFAULT_BATCH_SIZE = 500`), and empty function signatures for `cleanup_archive_jobs()` and `_do_archive_job_cleanup()`. Follow the structure of `memora_admin/tasks/task_log_archive_batch_cleanup.py`
- [x] T002 [P] Create `memora_admin/tests/test_archive_job_cleanup.py` with test class `TestArchiveJobCleanup(FrappeTestCase)`, `setUp`/`tearDown` for row cleanup, `_make_archive_job` helper (inserts `Memora Archive Job` via `frappe.get_doc` with configurable `status` and `modified`), and `_exists` / `_count_names` utility functions. Follow the structure of `memora_admin/tests/test_task_log_archive_batch_cleanup.py`

---

## Phase 3: User Story 1 — Automatic cleanup of old successful archive jobs (Priority: P1) 🎯 MVP

**Goal**: Old `Purged` archive jobs (modified > 30 days ago) are automatically deleted while recent ones survive

**Independent Test**: Insert Purged archive job rows with modified dates on both sides of the 30-day boundary, run cleanup, verify correct rows are deleted/preserved

### Tests for User Story 1 ⚠️

> **NOTE: Write these tests FIRST, ensure they FAIL before implementation**

- [x] T003 [US1] Write test `test_zero_row_case_exits_cleanly` — call `_do_archive_job_cleanup()` on empty table, assert returns `(0, 0)` in `memora_admin/tests/test_archive_job_cleanup.py`
- [x] T004 [US1] Write test `test_old_purged_rows_deleted` — insert Purged job with `modified` older than 30 days, run cleanup, assert row deleted and returns `(1, 1)` in `memora_admin/tests/test_archive_job_cleanup.py`
- [x] T005 [P] [US1] Write test `test_recent_purged_rows_not_deleted` — insert Purged job with `modified` within 30 days, run cleanup, assert row preserved and returns `(0, 0)` in `memora_admin/tests/test_archive_job_cleanup.py`
- [x] T006 [P] [US1] Write test `test_exact_purged_retention_cutoff` — insert two Purged jobs, one 1 minute before cutoff and one 1 minute after, verify correct boundary behavior in `memora_admin/tests/test_archive_job_cleanup.py`

### Implementation for User Story 1

- [x] T007 [US1] Implement `_do_archive_job_cleanup` Purged pass in `memora_admin/tasks/archive_job_cleanup.py` — parameter validation (`purged_retention_days >= 0`, `batch_size > 0`), compute cutoff via `add_days(now_datetime(), -purged_retention_days)`, batch loop: `SELECT name FROM tabMemora Archive Job WHERE status = 'Purged' AND modified < cutoff ORDER BY modified ASC, name ASC LIMIT batch_size` → `frappe.db.delete` → `frappe.db.commit` → repeat until empty. Return `(total_deleted, batches_executed)`. Reference: data-model.md Pass 1 query (without dependency subquery — added in US4)

**Checkpoint**: Purged cleanup works end-to-end. Run tests: `bench --site x.conanacademy.com run-tests --app memora_admin --module memora_admin.tests.test_archive_job_cleanup`

---

## Phase 4: User Story 2 — Cleanup of old failed archive jobs with extended retention (Priority: P1)

**Goal**: Old `Failed` archive jobs (modified > 90 days ago) are deleted while recent Failed jobs are preserved for debugging

**Independent Test**: Insert Failed archive job rows with modified dates around the 90-day boundary, run cleanup, verify correct retention behavior

### Tests for User Story 2 ⚠️

- [x] T008 [US2] Write test `test_old_failed_rows_deleted` — insert Failed job with `modified` older than 90 days, run cleanup, assert deleted in `memora_admin/tests/test_archive_job_cleanup.py`
- [x] T009 [P] [US2] Write test `test_recent_failed_rows_not_deleted` — insert Failed job with `modified` within 90 days, run cleanup, assert preserved in `memora_admin/tests/test_archive_job_cleanup.py`

### Implementation for User Story 2

- [x] T010 [US2] Add Failed pass to `_do_archive_job_cleanup` in `memora_admin/tasks/archive_job_cleanup.py` — after the Purged loop completes, run a second identical batch loop with `status = 'Failed'` and `modified < cutoff_failed` (computed from `failed_retention_days`). Add `failed_retention_days` parameter (default 90). Accumulate deletions and batch counts across both passes. Reference: research.md R-001 (two sequential passes)

**Checkpoint**: Both Purged (30-day) and Failed (90-day) cleanup passes work correctly

---

## Phase 5: User Story 3 — Active jobs are never deleted (Priority: P1)

**Goal**: Archive jobs in non-terminal statuses are never deleted regardless of age

**Independent Test**: Insert archive jobs in every non-terminal status with old modified dates, run cleanup, verify all are preserved

> **NOTE**: No additional implementation required — the status filter (`WHERE status = 'Purged'` / `WHERE status = 'Failed'`) in T007/T010 already ensures active jobs are excluded. This phase adds explicit test coverage.

### Tests for User Story 3 ⚠️

- [x] T011 [US3] Write test `test_non_terminal_statuses_survive` — insert jobs in each non-terminal status (`Pending`, `Processing`, `Exported`, `Transferred`, `Ingested`, `Completed`) with `modified` older than 90 days, also insert one eligible Purged and one eligible Failed job. Run cleanup, assert only the terminal jobs are deleted and all 6 non-terminal jobs survive. In `memora_admin/tests/test_archive_job_cleanup.py`

**Checkpoint**: Verified that only Purged and Failed jobs are ever deleted — all other statuses are safe

---

## Phase 6: User Story 4 — Dependency safety with related archive batch rows (Priority: P2)

**Goal**: Terminal archive jobs with active (non-terminal) child batch rows in `Memora Task Log Archive Batch` are preserved to avoid orphaning

**Independent Test**: Insert Purged archive job with related batch rows in non-terminal status, run cleanup, verify parent job is preserved

### Tests for User Story 4 ⚠️

- [x] T012 [US4] Write test `test_purged_job_with_active_batch_rows_preserved` — insert Purged job (>30 days), insert related `Memora Task Log Archive Batch` row with `status = 'Pending'` and matching `archive_job_id`, run cleanup, assert parent job preserved in `memora_admin/tests/test_archive_job_cleanup.py`
- [x] T013 [P] [US4] Write test `test_purged_job_with_terminal_batch_rows_deleted` — insert Purged job (>30 days), insert related batch row with `status = 'Purged'`, run cleanup, assert parent job deleted in `memora_admin/tests/test_archive_job_cleanup.py`
- [x] T014 [P] [US4] Write test `test_purged_job_with_no_batch_rows_deleted` — insert Purged job (>30 days) with no related batch rows, run cleanup, assert deleted in `memora_admin/tests/test_archive_job_cleanup.py`

### Implementation for User Story 4

- [x] T015 [US4] Add dependency subquery to both cleanup passes in `memora_admin/tasks/archive_job_cleanup.py` — extend SELECT in both Purged and Failed loops with `AND name NOT IN (SELECT DISTINCT archive_job_id FROM tabMemora Task Log Archive Batch WHERE status NOT IN ('Purged', 'Failed'))`. Reference: data-model.md query patterns

**Checkpoint**: Dependency safety verified — no orphaned batch rows can result from cleanup

---

## Phase 7: User Story 5 — Batched deletion with per-batch commits (Priority: P2)

**Goal**: Deletion runs in committed batches of 500 so partial progress is preserved on failure

**Independent Test**: Insert more eligible rows than one batch size, run cleanup, verify multiple batches with commits between them

> **NOTE**: Batch loop is already implemented in T007. This phase adds explicit tests validating batch mechanics, commit behavior, and crash recovery.

### Tests for User Story 5 ⚠️

- [x] T016 [US5] Write test `test_multiple_batches_required` — insert 5 eligible Purged rows, run cleanup with `batch_size=2`, assert `total=5, batches=3` in `memora_admin/tests/test_archive_job_cleanup.py`
- [x] T017 [P] [US5] Write test `test_commits_incrementally_per_batch` — insert 5 eligible Purged rows, patch `frappe.db.commit` with `wraps`, run cleanup with `batch_size=2`, assert `commit` called 3 times in `memora_admin/tests/test_archive_job_cleanup.py`
- [x] T018 [P] [US5] Write test `test_safe_rerun_after_partial_completion` — insert 4 eligible rows, patch `frappe.db.delete` to raise on 2nd call, assert first batch committed, rerun cleanup, assert remaining rows cleaned up in `memora_admin/tests/test_archive_job_cleanup.py`

**Checkpoint**: Batching mechanics verified — crash recovery works correctly

---

## Phase 8: Polish & Cross-Cutting Concerns

**Purpose**: Wrapper function with observability, scheduler registration, and final validation

- [x] T019 Implement wrapper function `cleanup_archive_jobs` in `memora_admin/tasks/archive_job_cleanup.py` — timing via `now_datetime()`, start/completion logging (task name, retention configs, batch/deletion counts), `log_task_run` on success and failure, Prometheus metrics (`TASK_RUNS`, `TASK_DURATION`, `USERS_PROCESSED`), `notify_admins` on failure, re-raise exceptions. Follow exact pattern of `cleanup_task_log_archive_batches` in `task_log_archive_batch_cleanup.py`. Log details should include both retention day values.
- [x] T020 [P] Write test `test_wrapper_emits_logs_and_metrics_on_success` — patch `_do_archive_job_cleanup` to return `(7, 2)`, verify `log_task_run` called with status=Success, metrics incremented, info logs emitted in `memora_admin/tests/test_archive_job_cleanup.py`
- [x] T021 [P] Write test `test_wrapper_emits_logs_and_metrics_on_failure` — patch `_do_archive_job_cleanup` to raise, verify `log_task_run` called with status=Failed, `notify_admins` called, critical log emitted, exception re-raised in `memora_admin/tests/test_archive_job_cleanup.py`
- [x] T022 [P] Write test `test_per_batch_logs_are_emitted` — insert 5 eligible rows, patch logger, run with `batch_size=2`, assert 3 "deleted batch" info messages in `memora_admin/tests/test_archive_job_cleanup.py`
- [x] T023 Add scheduler entry to `memora_admin/hooks.py` — add `"30 6 * * *": ["memora_admin.tasks.archive_job_cleanup.cleanup_archive_jobs"]` with comment `# Daily at 06:30: Delete old terminal Memora Archive Job rows`
- [x] T024 Run full test suite via `bench --site x.conanacademy.com run-tests --app memora_admin --module memora_admin.tests.test_archive_job_cleanup` and validate all tests pass

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: No dependencies — can start immediately
- **US1 (Phase 3)**: Depends on T001 (implementation file) and T002 (test file)
- **US2 (Phase 4)**: Depends on T007 (US1 implementation — extends the function)
- **US3 (Phase 5)**: Depends on T010 (US2 implementation — tests both passes)
- **US4 (Phase 6)**: Depends on T010 (US2 implementation — adds subquery to both passes)
- **US5 (Phase 7)**: Depends on T007 (US1 implementation — tests batch mechanics)
- **Polish (Phase 8)**: Depends on T015 (US4 implementation — all core behavior complete)

### User Story Dependencies

- **US1 (P1)**: MVP — standalone, delivers Purged cleanup
- **US2 (P1)**: Extends US1 — adds Failed pass to existing function
- **US3 (P1)**: Test-only — verifies safety guarantee already built into US1+US2
- **US4 (P2)**: Extends US1+US2 — adds subquery to both passes
- **US5 (P2)**: Test-only — verifies batch mechanics already built into US1
- **US3 and US5 can run in parallel** (both are test-only, different test methods, no implementation overlap)
- **US4 must run after US2** (modifies the same query in both passes)

### Within Each User Story

- Tests written FIRST, verified to FAIL before implementation
- Implementation follows test definition
- Story checkpoint before proceeding

### Parallel Opportunities

Within Phase 1:
```
T001 (implementation skeleton) || T002 (test skeleton)
```

Within US1:
```
T005 (recent test) || T006 (boundary test)  — after T003, T004
```

Within US4:
```
T013 (terminal batch test) || T014 (no batch test)  — after T012
```

Within US5:
```
T017 (commit test) || T018 (rerun test)  — after T016
```

Within Polish:
```
T020 (success wrapper test) || T021 (failure wrapper test) || T022 (batch log test)
```

Cross-story parallelism (after US2 completes):
```
US3 (T011) || US5 (T016-T018)  — both test-only, no impl overlap
```

---

## Implementation Strategy

### MVP First (User Story 1 Only)

1. Complete Phase 1: Setup (T001, T002)
2. Complete Phase 3: US1 — Purged cleanup (T003–T007)
3. **STOP and VALIDATE**: Run tests, verify Purged cleanup works
4. Already useful — old Purged jobs get cleaned up

### Incremental Delivery

1. Setup (T001–T002) → File skeletons ready
2. US1 (T003–T007) → Purged cleanup works → **MVP!**
3. US2 (T008–T010) → Failed cleanup added → Both retention tiers active
4. US3 (T011) → Safety guarantee verified by tests
5. US4 (T012–T015) → Dependency safety added → No orphaned batch rows
6. US5 (T016–T018) → Batch mechanics verified by tests
7. Polish (T019–T024) → Observability + scheduler integration → Production-ready

---

## Notes

- Reference implementation: `memora_admin/tasks/task_log_archive_batch_cleanup.py`
- Reference tests: `memora_admin/tests/test_task_log_archive_batch_cleanup.py`
- US3 and US5 are test-only phases — the implementation is inherent in the core loop (US1) and status filters (US1+US2)
- The `_make_archive_job` helper must set `modified` via direct SQL update after `doc.insert()` since Frappe auto-sets `modified` on insert
- The dependency subquery (US4) uses `NOT IN` pattern per research.md R-002
- Scheduler slot `30 6 * * *` per research.md R-005 — runs after batch cleanup (04:30) to minimize dependency check blocks
