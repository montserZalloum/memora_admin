# Tasks: Production Archival and Purge for Memora Task Run Log

**Input**: Design documents from `/specs/042-task-log-archive/`
**Prerequisites**: plan.md, spec.md, research.md, data-model.md, contracts/

**Organization**: Tasks are grouped by user story to enable independent implementation and testing of each story.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependencies)
- **[Story]**: Which user story this task belongs to
- Exact file paths included in every description

---

## Phase 1: Setup

**Purpose**: Deploy the new archive schema so the executor can load it.

- [X] T001 Copy `specs/042-task-log-archive/contracts/task_run_log.v1.yaml` to `archive_schemas/archive_types/task_run_log.v1.yaml`

**Checkpoint**: Verify with `load_archive_type('archive_schemas', 'task_run_log', 'v1')` — should print `task_run_log tabMemora Task Run Log`

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Register the `Memora Task Log Archive Batch` DocType and add the covering index. MUST be complete before any user story work.

**⚠️ CRITICAL**: No archive task or purge task can insert or query batch records until the DocType is migrated into the database.

- [X] T002 [P] Create DocType definition with all 14 fields (`source_doctype`, `date_from`, `date_to`, `cutoff_date`, `row_count`, `file_path`, `file_checksum`, `status`, `exported_at`, `synced_at`, `purged_at`, `last_error`, `retry_count`, `archive_job_id`), `autoname: TLBATCH-.#####.`, `engine: InnoDB`, `track_changes: 0`, and permissions for System Manager (read/export/report) and Task Admin (full CRUD) in `memora_admin/memora_admin/doctype/memora_task_log_archive_batch/memora_task_log_archive_batch.json`
- [X] T003 [P] Create minimal DocType controller (pass-through, no custom logic needed at this stage) in `memora_admin/memora_admin/doctype/memora_task_log_archive_batch/memora_task_log_archive_batch.py`
- [X] T004 [P] Add `idx_task_log_archive (status, completed_at, name)` covering index via `ALTER TABLE \`tabMemora Task Run Log\` ADD INDEX IF NOT EXISTS` in `before_migrate` hook in `memora_admin/memora_admin/setup.py`

**Checkpoint**: Run `bench migrate`, then `SHOW INDEX FROM \`tabMemora Task Run Log\` WHERE Key_name = 'idx_task_log_archive'` — must return one row; `frappe.get_doc('Memora Task Log Archive Batch', {'name': None})` must not raise

---

## Phase 3: User Story 1 - Daily Archive Run (Priority: P1) — MVP

**Goal**: Archive task identifies eligible task run log rows (terminal status, `completed_at` older than 90 days), creates `tabMemora Archive Job` records via the existing scheduler, creates linked `Memora Task Log Archive Batch` records, and on each subsequent run syncs `Exported` batches to `Synced` when the linked archive job is `Completed`.

**Independent Test**: Seed `tabMemora Task Run Log` with terminal-status rows spanning 120 days, run `archive_task_log()`, verify a `Memora Task Log Archive Batch` record is created in `Exported` status with correct `archive_job_id`; seed a matching `Completed` archive job, re-run task, verify batch transitions to `Synced`.

- [X] T005 [US1] Implement `archive_task_log(triggered_by="Scheduler")` entry point with `RUNTIME_CAP_SECONDS = 300`, `TERMINAL_STATUSES = ('Success', 'Failed', 'Partial')`, `RETENTION_DAYS = 90` constants; wire Phase 1 (call `_sync_batch_statuses()`) and Phase 2 (call `scheduler.create_pending_jobs()` for `archive_type="task_run_log"` and for each new job call `_create_batch_for_job()`) with runtime cap check after each job; log task run via `log_task_run()` in `memora_admin/tasks/archive_task_log.py`
- [X] T006 [US1] Implement `_create_batch_for_job(job_name, source_doctype, job_meta)` that extracts `date_from`, `date_to`, `cutoff_date` from `job_meta.query_filter`, creates a `Memora Task Log Archive Batch` Frappe doc in `Pending` status with `archive_job_id` set, and returns the new batch name in `memora_admin/tasks/archive_task_log.py`
- [X] T007 [US1] Implement `_sync_batch_statuses()` that queries `Memora Task Log Archive Batch` records with `status IN ('Pending', 'Exported')` and a non-null `archive_job_id`, looks up each linked `tabMemora Archive Job`, and transitions the batch to `Synced` (setting `synced_at`) when the job is `Completed`; returns `(synced_count, failed_count)` in `memora_admin/tasks/archive_task_log.py`
- [X] T008 [US1] Add archive cron entry `"0 2 * * *": ["memora_admin.tasks.archive_task_log.archive_task_log"]` to `scheduler_events` in `memora_admin/hooks.py`

**Checkpoint**: After seeding rows and running the task, one `Memora Task Log Archive Batch` with `status='Exported'` exists; re-running with the archive job set to `Completed` produces `status='Synced'` and a populated `synced_at`; re-running again creates no duplicate jobs (idempotent)

---

## Phase 4: User Story 2 - Safe Purge Run (Priority: P2)

**Goal**: Purge task scans `Synced` batches and deletes their source rows from production in bounded sub-batches of 10,000, each committed independently, with a 5-second lock timeout and a 300-second runtime cap. No row within the 90-day retention window is ever deleted.

**Independent Test**: Insert a `Memora Task Log Archive Batch` in `Synced` status and matching production rows. Run `purge_task_log()`. Verify: rows deleted in sub-batches ≤10,000; rows with `completed_at` within the last 90 days are untouched; batch transitions to `Purged` with `purged_at` populated; a batch in `Exported` (not `Synced`) is skipped.

- [X] T009 [US2] Implement `purge_task_log(triggered_by="Scheduler")` entry point with `RUNTIME_CAP_SECONDS = 300`; query `Memora Task Log Archive Batch` records with `status='Synced'`; for each batch call `_purge_batch()` in a loop; after all rows are deleted set `status='Purged'` and `purged_at`; enforce runtime cap after each sub-batch; log task run in `memora_admin/tasks/purge_task_log.py`
- [X] T010 [US2] Implement `_purge_sub_batch(conn, source_table, date_from, date_to, retention_days, terminal_statuses)` that: (1) opens a fresh connection, (2) runs `SET SESSION innodb_lock_wait_timeout = 5`, (3) `SELECT name FROM \`tabMemora Task Run Log\` WHERE status IN (...) AND completed_at >= %s AND completed_at < %s AND completed_at < DATE_SUB(NOW(), INTERVAL %s DAY) ORDER BY completed_at LIMIT 10000`, (4) if no rows returns 0, (5) `DELETE FROM \`tabMemora Task Run Log\` WHERE name IN (...)`, (6) commits immediately, (7) returns rows deleted count; on `OperationalError` (lock timeout) rolls back and re-raises in `memora_admin/tasks/purge_task_log.py`
- [X] T011 [US2] Add purge cron entry `"30 3 * * *": ["memora_admin.tasks.purge_task_log.purge_task_log"]` to `scheduler_events` in `memora_admin/hooks.py`

**Checkpoint**: After running purge on a `Synced` batch, `tabMemora Task Run Log` contains zero rows from that batch's date range; batch `status='Purged'`; rows within the current 90-day window are unaffected; a `Exported`-status batch is unchanged

---

## Phase 5: User Story 3 - Failure Recovery (Priority: P3)

**Goal**: Failed batches are automatically retried on the next archive run — re-exporting if no Parquet file exists or reusing the existing file if export already succeeded. Batches that exceed `MAX_RETRY_COUNT` are skipped and an alert is logged. Interrupted purges resume from remaining un-deleted rows without re-deletion.

**Independent Test**: Set a batch to `Failed` with `retry_count=0`, re-run archive task, verify retry attempt and retry_count incremented; set `retry_count=3`, re-run, verify batch skipped with alert in log; interrupt purge with rows partially deleted, re-run, verify only remaining rows are selected for deletion (no `OperationalError` or double-delete).

- [X] T012 [US3] Add `Failed` batch retry path to the Phase 2 loop in `archive_task_log()`: before creating new jobs, query `Memora Task Log Archive Batch` records with `status='Failed'` and `retry_count < MAX_RETRY_COUNT` (default 3); for each, attempt re-export — skip creating a new archive job if one already exists (use `_job_exists()` check), update `retry_count += 1` and clear `last_error` on success, set `last_error` and leave `status='Failed'` on exception in `memora_admin/tasks/archive_task_log.py`
- [X] T013 [US3] Add `MAX_RETRY_COUNT = 3` constant and max-retry guard: when `retry_count >= MAX_RETRY_COUNT`, set `last_error` to `"Max retry count reached — manual intervention required"`, leave `status='Failed'`, and log a `frappe.log_error()` alert; do not retry or create a new archive job in `memora_admin/tasks/archive_task_log.py`

**Checkpoint**: A `Failed` batch with `retry_count=2` is retried and transitions to `Exported` on success; a `Failed` batch with `retry_count=3` is skipped and `frappe.log_error` is called; re-running purge on a partially purged batch (some rows already deleted) completes cleanly without errors

---

## Phase 6: User Story 4 - Operational Visibility (Priority: P4)

**Goal**: Every `Memora Task Log Archive Batch` record has fully and accurately populated metadata after each lifecycle transition: `row_count`, `file_path`, `file_checksum`, `exported_at`, `synced_at`, `purged_at`, `last_error` (on failure), and `retry_count`. All batches are independently queryable by status, date range, and source table.

**Independent Test**: Run a full archive+purge cycle including one injected failure. Inspect the batch record and verify every field matches expectations: `row_count` equals actual Parquet row count, `file_checksum` is a valid 64-char hex string, all timestamps are non-null at their respective stages, `last_error` is non-null and descriptive on the failed batch, `status` is `Purged` on the completed batch.

- [X] T014 [P] [US4] Ensure `_sync_batch_statuses()` and `_create_batch_for_job()` in `archive_task_log.py` populate `exported_at` (when archive job reaches `Exported`), `file_path` (from archive job's `file_path`), `file_checksum` (from job_meta), and `row_count` (from job_meta) when transitioning to `Exported`; populate `last_error` and increment `retry_count` on any exception path in `memora_admin/tasks/archive_task_log.py`
- [X] T015 [P] [US4] Ensure `purge_task_log()` sets `purged_at = frappe.utils.now()` and transitions `status='Purged'` only after the sub-batch loop confirms zero rows remain; on `OperationalError` (lock timeout) sets `last_error` with the exception message and leaves `status='Synced'` for retry in `memora_admin/tasks/purge_task_log.py`

**Checkpoint**: After a full cycle, every batch field is populated as specified; querying `frappe.get_list('Memora Task Log Archive Batch', filters={'status': 'Purged'})` returns the completed batch; filtering by `date_from` and `date_to` returns the correct subset

---

## Phase 7: Integration Tests

**Purpose**: Verify the full pipeline end-to-end against the real DB using the existing `archive_executor/tests/` pattern.

- [X] T016 Write integration tests in `archive_executor/tests/test_task_log_pipeline.py` covering:
  - Happy-path: seed eligible rows → run archive task → verify batch `Exported` → simulate archive job `Completed` → verify batch `Synced` → run purge task → verify batch `Purged` and source rows deleted
  - No-eligible-rows: all rows within retention window → archive task → no batch created, graceful exit
  - Idempotency: re-running archive task when all windows already have jobs → no duplicate jobs or batches created
  - Retention window guard: purge task never deletes rows with `completed_at` within 90 days regardless of batch scope
  - Runtime cap: mock `time.monotonic()` to exceed 300s → task exits cleanly, remaining batches deferred
  - Failure retry: set batch `status='Failed'`, `retry_count=1` → re-run archive task → batch retried, `retry_count=2`
  - Max retry skip: set `retry_count=3` → re-run → batch skipped, `frappe.log_error` called
  - Sub-batch commit: verify each 10,000-row chunk is individually committed (partial purge leaves no orphans)

**Run command**:
```bash
DB_HOST=127.0.0.1 DB_PORT=3306 DB_USER=_9be6802bfff1e8ca \
DB_PASSWORD=zjAACevKaH5VGVP2 DB_NAME=_9be6802bfff1e8ca \
SCHEMA_REGISTRY_PATH=$(pwd)/archive_schemas \
ARCHIVE_OUTPUT_PATH=/tmp/memora-archive-test \
python3 -m pytest archive_executor/tests/test_task_log_pipeline.py -v
```

---

## Final Phase: Polish & Cross-Cutting Concerns

- [X] T017 Run quickstart.md verification checklist: (1) `load_archive_type` schema load, (2) DocType migration, (3) covering index `SHOW INDEX` confirms `idx_task_log_archive`, (4) `EXPLAIN SELECT` shows `Using index`, (5) manual trigger via `bench execute`, (6) idempotency re-run, (7) full archive+purge cycle in test environment

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: No dependencies — start immediately
- **Foundational (Phase 2)**: Depends on Phase 1 — BLOCKS all user stories (DocType must exist in DB before tasks can insert batches)
- **US1 (Phase 3)**: Depends on Phase 2 — MVP deliverable
- **US2 (Phase 4)**: Depends on Phase 2; integrates with US1 output (Synced batches)
- **US3 (Phase 5)**: Depends on Phase 3 (modifies archive_task_log.py); Phase 4 already handles purge resume naturally
- **US4 (Phase 6)**: Depends on Phase 3 and Phase 4 (verifies completeness of field population)
- **Integration Tests (Phase 7)**: Depends on all implementation phases complete
- **Polish (Final)**: Depends on all phases complete

### User Story Dependencies

- **US1 (P1)**: Starts after Phase 2 — no story dependencies
- **US2 (P2)**: Starts after Phase 2 — independent of US1 but requires Synced batches (produced by US1) for meaningful end-to-end
- **US3 (P3)**: Modifies US1 output (`archive_task_log.py`) — logically depends on US1 completing the base implementation
- **US4 (P4)**: Verifies completeness of US1+US2 metadata — logically depends on both

### Within Each User Story

- T005 before T006 (entry point before helper — same file, ordered)
- T006 before T007 (batch creation before status sync — same file)
- T009 before T010 (entry point before helper — same file)
- T014 and T015 are parallel (different files)
- T002, T003, T004 are all parallel (different files)

### Parallel Opportunities

- Phase 2: T002, T003, T004 — all parallel (three separate new files)
- Phase 6: T014, T015 — parallel (archive_task_log.py vs purge_task_log.py)
- US1 (T005-T008) and US2 (T009-T011) can proceed in parallel once Phase 2 is complete if staffed separately

---

## Parallel Example: Phase 2 (Foundational)

```
# Run all three in parallel:
Task T002: Create memora_task_log_archive_batch.json
Task T003: Create memora_task_log_archive_batch.py
Task T004: Add covering index to setup.py
```

## Parallel Example: Phase 6 (Operational Visibility)

```
# Run in parallel (different files):
Task T014: Populate exported_at/file_path/file_checksum/row_count in archive_task_log.py
Task T015: Populate purged_at and last_error on failure in purge_task_log.py
```

---

## Implementation Strategy

### MVP First (User Story 1 Only)

1. Complete Phase 1: Setup (T001)
2. Complete Phase 2: Foundational (T002-T004 in parallel) → `bench migrate`
3. Complete Phase 3: US1 (T005-T008 sequentially)
4. **STOP and VALIDATE**: Seed rows, run archive task, verify batch created and synced
5. Demo or defer US2-US4

### Incremental Delivery

1. Phase 1+2 → Foundation ready (DocType + schema + index)
2. Phase 3 (US1) → Archive scheduling works → MVP
3. Phase 4 (US2) → Purge works → Space reclaimed
4. Phase 5 (US3) → Failures auto-recover → Production-safe
5. Phase 6 (US4) → Full observability → Ops-ready
6. Phase 7 → Test coverage → CI-ready

### Parallel Team Strategy

With two developers after Phase 2 completes:
- Developer A: US1 (Phase 3) → US3 (Phase 5)
- Developer B: US2 (Phase 4) → US4 (Phase 6)
- Together: Phase 7 integration tests

---

## Summary

| Phase | Tasks | Story | Parallel? |
|-------|-------|-------|-----------|
| 1: Setup | T001 | — | No |
| 2: Foundational | T002-T004 | — | All 3 parallel |
| 3: US1 Archive | T005-T008 | P1 | Sequential (same file) |
| 4: US2 Purge | T009-T011 | P2 | Sequential (same file) |
| 5: US3 Recovery | T012-T013 | P3 | Sequential (same file) |
| 6: US4 Visibility | T014-T015 | P4 | Both parallel |
| 7: Integration Tests | T016 | — | No |
| Final: Polish | T017 | — | No |

**Total tasks**: 17
**Parallel opportunities**: Phase 2 (3-way), Phase 6 (2-way), Phase 3+4 (cross-team)
**MVP scope**: Phases 1-3 (T001-T008) — archive scheduling fully operational
