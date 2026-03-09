# Tasks: Memora Archive System

**Input**: Design documents from `/specs/039-archive-system/`
**Prerequisites**: plan.md, spec.md, research.md, data-model.md, contracts/

**Organization**: Tasks are grouped by user story to enable independent implementation and testing of each story.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependencies)
- **[Story]**: Which user story this task belongs to (e.g., US1, US2, US3)
- Include exact file paths in descriptions

## Path Conventions

- **DocType**: `memora_admin/memora_admin/doctype/memora_archive_job/`
- **Frappe tasks**: `memora_admin/tasks/`
- **Executor**: `archive_executor/`
- **Schema registry**: `archive_schemas/`
- **Patches**: `memora_admin/patches/`

---

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: Create directory structure and dependency files for the two-plane architecture

- [x] T001 Create project directory structure: `memora_admin/memora_admin/doctype/memora_archive_job/__init__.py`, `archive_executor/__init__.py`, `archive_schemas/dimensions/`, `archive_schemas/archive_types/`
- [x] T002 [P] Create executor Python dependencies file listing pyarrow, pymysql, pyyaml in `archive_executor/requirements.txt`

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Core schemas, DocType, and executor infrastructure that MUST be complete before ANY user story can be implemented

**Why blocking**: US1 needs the DocType + YAML schemas to create jobs. US2 needs the executor modules + YAML schemas to process jobs. All downstream stories depend on these.

- [x] T003 [P] Create player dimension schema YAML defining fields (name, display_name, grade, academic_plan, mobile) with source_table and id_column in `archive_schemas/dimensions/player.v1.yaml`
- [x] T004 [P] Create review_item dimension schema YAML defining fields (item_id, player_id, topic, lesson, stage, question_text) with source_table and id_column in `archive_schemas/dimensions/review_item.v1.yaml`
- [x] T005 [P] Create practice_log archive type YAML defining fact_columns, scope_column (last_seen_at), dimension references (player.v1 via player_id, review_item.v1 via item_id), and schema_snapshot in `archive_schemas/archive_types/practice_log.v1.yaml`
- [x] T006 Create DocType JSON schema with all fields from data-model.md (identity, status lifecycle, execution tracking, output metadata, retry/error, behavior, transfer lifecycle, meta JSON, retry button), autoname `ARCH-.#####.`, all fields read_only=1, status Select with Pending/Processing/Completed/Purged/Failed in `memora_admin/memora_admin/doctype/memora_archive_job/memora_archive_job.json`
- [x] T007 Create DocType Python class with VALID_TRANSITIONS dict (Pending→Processing, Processing→Completed, Processing→Failed, Completed→Purged, Failed→Pending), validate() enforcing transitions, and before_insert() preventing manual creation from UI in `memora_admin/memora_admin/doctype/memora_archive_job/memora_archive_job.py`
- [x] T008 [P] Create migration patch executing `CREATE UNIQUE INDEX idx_archive_job_unique ON tabMemora Archive Job (source_doctype(100), archive_scope(100), schema_version(50))` and register in `patches.txt` as `memora_admin.patches.039_archive_job_unique_index` in `memora_admin/patches/039_archive_job_unique_index.py`
- [x] T009 [P] Create executor config module loading env vars (DB_HOST, DB_PORT, DB_USER, DB_PASSWORD, DB_NAME, ARCHIVE_OUTPUT_PATH default `/data/memora/archives/`, SCHEMA_REGISTRY_PATH, LOG_PATH, LOCK_FILE default `/var/run/memora-archive.lock`, CHUNK_SIZE default 50000, STUCK_TIMEOUT_HOURS default 1) with dataclass in `archive_executor/config.py`
- [x] T010 [P] Create executor DB module with pymysql connection factory, server-side cursor context manager for streaming large result sets, and helper for atomic UPDATE statements in `archive_executor/db.py`
- [x] T011 [P] Create executor structured JSON logger writing to `{LOG_PATH}/archive.log` with fields: ts, level, event, plus arbitrary kwargs in `archive_executor/logger.py`
- [x] T012 [P] Create executor YAML schema registry loader: `load_dimension_schema(entity, version)` and `load_archive_type(type_name, version)` reading from SCHEMA_REGISTRY_PATH, returning parsed dicts in `archive_executor/schemas.py`

**Checkpoint**: Foundation ready — DocType exists in DB (after migrate), YAML schemas defined, executor modules ready for integration

---

## Phase 3: User Story 1 - Automatic Season Archive Creation (Priority: P1)

**Goal**: When a season ends, the system auto-creates Pending archive jobs for each registered archive type

**Independent Test**: End a season (set `is_published=0`, `end_date` in the past), run the scheduled task, verify an Archive Job record exists with status "Pending", correct scope, and populated `meta` JSON

### Implementation for User Story 1

- [x] T013 [US1] Create archive trigger task: query ended seasons (`is_published=0 AND end_date < CURDATE()`), load archive type YAMLs from registry, create Archive Job per type with populated meta JSON (query_filter from season dates, export_columns, related_tables, schema_snapshot from YAML), catch DuplicateEntryError silently in `memora_admin/tasks/archive_trigger.py`
- [x] T014 [US1] Register `check_seasons_for_archive` as daily cron at 01:20 in scheduler_events section of `memora_admin/hooks.py`

**Checkpoint**: Ended seasons produce Pending Archive Jobs automatically. Duplicates prevented by DB unique constraint.

---

## Phase 4: User Story 2 - Standalone Archive Execution (Priority: P1)

**Goal**: A standalone Python script picks up Pending jobs, exports fact + dimension Parquet files, builds manifest, and marks jobs Completed

**Independent Test**: Create a Pending Archive Job manually, run the executor script, verify Parquet files exist at `{ARCHIVE_OUTPUT_PATH}/{job_name}/` with correct row counts, manifest.json present, job status is "Completed" with populated output metadata

### Implementation for User Story 2

- [x] T015 [US2] Create fact data exporter: build SQL query from job meta (query_filter, export_columns), stream rows via server-side cursor in CHUNK_SIZE batches, write to Parquet via pyarrow.ParquetWriter, return row count and file path in `archive_executor/exporter.py`
- [x] T016 [P] [US2] Create manifest.json builder: accept batch_id, source_doctype, archive_scope, schema_version, snapshot_taken_at, list of file entries (role, filename, row_count, sha256 checksum, size_bytes), write to staging directory in `archive_executor/manifest.py`
- [x] T017 [P] [US2] Create file validator: verify row count matches expected, compute SHA-256 checksum, verify file size, return validation result dict in `archive_executor/validator.py`
- [x] T018 [US2] Create executor entry point: acquire file lock (exit if held), detect and fail stuck jobs (Processing > STUCK_TIMEOUT_HOURS), query Pending jobs ordered by priority DESC + creation ASC, for each job: atomic claim via UPDATE WHERE status='Pending', create staging dir, export fact data (T015), record snapshot_taken_at, export dimension snapshots (scoped to referenced IDs from fact data), validate files (T017), build manifest (T016), atomic rename staging→final, set permissions 0700, update job to Completed with metadata; on failure: clean staging dir, set status='Failed' with error_log; update execution_stage at each step in `archive_executor/run.py`

**Checkpoint**: Full archive pipeline works end-to-end. Pending jobs → Parquet files + manifest → Completed status. File lock prevents concurrent runs. Stuck jobs auto-detected.

---

## Phase 5: User Story 3 - Archive Job Monitoring & Retry (Priority: P2)

**Goal**: Admin can view archive jobs in Frappe, see statuses/errors, and retry failed jobs with a button click

**Independent Test**: Create a Failed Archive Job, open in Frappe admin, click Retry button, verify status resets to "Pending" with retry_count=0 and cleared error_log

### Implementation for User Story 3

- [x] T019 [US3] Add `retry_archive_job` whitelisted server action: validate status is "Failed" (throw ValidationError otherwise), reset status to "Pending", set retry_count=0, clear error_log and execution_stage, save with ignore_permissions=True in `memora_admin/memora_admin/doctype/memora_archive_job/memora_archive_job.py`
- [x] T020 [US3] Create DocType JS: add retry button click handler calling `frappe.call` to `retry_archive_job` with confirmation dialog, enforce all fields read-only on refresh, show/hide retry button based on status=='Failed' in `memora_admin/memora_admin/doctype/memora_archive_job/memora_archive_job.js`

**Checkpoint**: Admin has full visibility into archive jobs. Failed jobs can be manually retried. All fields are read-only.

---

## Phase 6: User Story 4 - Automatic Retry with Failure Escalation (Priority: P2)

**Goal**: Failed jobs auto-retry up to 3 times. After exhausting retries, permanently fail and notify admin.

**Independent Test**: Simulate a failure (e.g., invalid query filter), run executor 4 times, verify retry_count increments to 3 then status becomes "Failed", and admin receives email notification

### Implementation for User Story 4

- [x] T021 [US4] Enhance executor failure handler: on exception, if retry_count < 3 then UPDATE status='Pending' with retry_count+1 and error_log; if retry_count >= 3 then UPDATE status='Failed' with error_log and completed_at; always clean up staging directory in `archive_executor/run.py`
- [x] T022 [P] [US4] Create notification task: query jobs with status='Failed' that haven't been notified (use error_log or add tracking), send email to System Manager role users via frappe.sendmail with job details (name, source, scope, error), publish Desk realtime notification in `memora_admin/tasks/archive_notify.py`
- [x] T023 [US4] Register `notify_failed_archive_jobs` as daily cron at 06:00 in scheduler_events section of `memora_admin/hooks.py`

**Checkpoint**: Transient failures self-heal (up to 3 retries). Permanent failures trigger admin notification. No manual intervention needed for recoverable errors.

---

## Phase 7: User Story 5 - Post-Archive Source Data Purge (Priority: P3)

**Goal**: After archive completion, purge source data in small batches to avoid locking the production database

**Independent Test**: Create a Completed Archive Job with `post_archive_action="Delete"`, run the purge process, verify rows are deleted in 10k batches with 2s pauses, job transitions to "Purged" with `source_deleted=1`

### Implementation for User Story 5

- [x] T024 [US5] Create purge module: query Completed jobs with post_archive_action='Delete' and source_deleted=0, for each job read meta.query_filter for date range and purge_progress for resume point, execute `DELETE ... WHERE filter ORDER BY ... LIMIT 10000` in a loop with 2-second sleeps, update purge_progress JSON after each batch, set status='Purged' and source_deleted=1 when 0 rows affected in `archive_executor/purge.py`
- [x] T025 [US5] Integrate purge step into executor run loop: after processing all pending archive jobs, call purge module to process eligible completed jobs in `archive_executor/run.py`

**Checkpoint**: Archived source data can be safely purged in small batches. Interrupted purges resume from last checkpoint. Production DB latency unaffected.

---

## Phase 8: User Story 6 - Schema Registry Extensibility (Priority: P3)

**Goal**: Adding a new archivable table requires only adding YAML files — no executor code changes

**Independent Test**: Add a hypothetical new dimension YAML and archive type YAML, verify the executor correctly discovers and uses them without code modifications

### Implementation for User Story 6

- [x] T026 [US6] Add version validation to schema loader: verify all dimension versions referenced in archive type YAML exist as files, raise clear error if version file missing, ensure loader discovers schemas dynamically (no hardcoded entity names) in `archive_executor/schemas.py`

**Checkpoint**: Schema registry is fully extensible. New archive targets added via YAML only.

---

## Phase 9: User Story 7 - Transfer Verification & Local Retention (Priority: P3)

**Goal**: After batch transfer, verify integrity via checksums. Local copies retained until transfer is verified.

**Independent Test**: Copy a batch directory to a second location, run verification, confirm checksums match and transfer_status updates correctly

### Implementation for User Story 7

- [x] T027 [US7] Add transfer verification function: given a job and destination path, compute SHA-256 of each file at destination, compare against manifest checksums, verify file count matches, update transfer_status to 'Transferred' or 'Transfer Failed' with transferred_at timestamp in `archive_executor/validator.py`
- [x] T028 [US7] Add local copy cleanup function: query jobs with transfer_status='Transferred', delete local batch directory, record local_deleted_at; skip jobs with transfer_status != 'Transferred' in `archive_executor/purge.py`

**Checkpoint**: Transfer integrity verified via checksums. Local copies safely retained until transfer confirmed. No data loss possible.

---

## Phase 10: Polish & Cross-Cutting Concerns

**Purpose**: Final validation and cross-cutting improvements

- [x] T029 Verify all DocType fields are read-only in both JSON schema and JS enforcement in `memora_archive_job.json` and `memora_archive_job.js`
- [x] T030 Run quickstart.md end-to-end validation: create test job, run executor, verify Parquet output and manifest

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: No dependencies — can start immediately
- **Foundational (Phase 2)**: Depends on Setup — BLOCKS all user stories
- **US1 (Phase 3)**: Depends on Foundational (DocType + YAML schemas)
- **US2 (Phase 4)**: Depends on Foundational (executor modules + YAML schemas)
- **US3 (Phase 5)**: Depends on Foundational (DocType exists)
- **US4 (Phase 6)**: Depends on US2 (enhances executor failure handler)
- **US5 (Phase 7)**: Depends on US2 (extends executor with purge step)
- **US6 (Phase 8)**: Depends on Foundational (enhances schema loader)
- **US7 (Phase 9)**: Depends on US2 (extends validator + purge modules)
- **Polish (Phase 10)**: Depends on all desired user stories being complete

### User Story Dependencies

- **US1 (P1)**: Independent after Foundational — Frappe-side only
- **US2 (P1)**: Independent after Foundational — executor-side only
- **US3 (P2)**: Independent after Foundational — Frappe-side only, can parallel with US1/US2
- **US4 (P2)**: Depends on US2 (modifies executor run.py failure handler)
- **US5 (P3)**: Depends on US2 (extends executor run.py with purge step)
- **US6 (P3)**: Independent after Foundational — enhances schemas.py only
- **US7 (P3)**: Depends on US2 (extends validator.py and purge.py)

### Within Each User Story

- Models/schemas before services
- Services before entry points
- Core implementation before integration
- Story complete before moving to next priority

### Parallel Opportunities

- **Phase 2**: T003, T004, T005 (YAML schemas) all parallel. T008-T012 (executor modules + patch) all parallel. T006→T007 sequential (JSON before Python).
- **Phase 3 + Phase 4**: US1 and US2 can run in parallel (different codebases: Frappe vs executor)
- **Phase 4**: T016, T017 parallel (manifest + validator). T015→T018 sequential (exporter before run.py).
- **Phase 5**: T019, T020 parallel (Python + JS for same DocType)
- **Phase 6**: T022 parallel with T021 (notification task independent of executor changes)
- **Phase 8 + Phase 5**: US6 can parallel with US3 (different files)

---

## Parallel Example: After Foundational

```
# US1 and US2 can run in parallel (different codebases):
Stream A (Frappe): T013 → T014          # Archive trigger
Stream B (Executor): T015 + T016 + T017 → T018   # Core executor

# US3 can also run in parallel (Frappe-side, no overlap):
Stream C (Frappe): T019 + T020          # Retry UI
```

---

## Implementation Strategy

### MVP First (US1 + US2 Only)

1. Complete Phase 1: Setup
2. Complete Phase 2: Foundational (CRITICAL — blocks all stories)
3. Complete Phase 3: US1 (archive job creation) + Phase 4: US2 (archive execution) — **in parallel**
4. **STOP and VALIDATE**: Create a test season, trigger archive, run executor, verify Parquet output
5. Deploy — core archival pipeline operational

### Incremental Delivery

1. Setup + Foundational → Foundation ready
2. US1 + US2 (parallel) → Core pipeline works → **MVP!**
3. US3 → Admin can monitor and retry → Deploy
4. US4 → Auto-retry + notifications → Deploy
5. US5 → Purge capability → Deploy
6. US6 → Extensibility validated → Deploy
7. US7 → Transfer verification ready → Deploy (when analytics server available)

### Key Risk: US2 Complexity

US2 (executor entry point, T018) is the most complex single task. It integrates all executor modules into a coherent pipeline with error handling, staging, atomic publish, and execution stage tracking. Consider breaking T018 into sub-steps during implementation:
1. File locking + stuck job detection
2. Job claiming + staging directory
3. Fact export integration
4. Dimension snapshot export
5. Validation + manifest + publish
6. Error handling + cleanup

---

## Notes

- [P] tasks = different files, no dependencies
- [Story] label maps task to specific user story for traceability
- Executor code (archive_executor/) runs outside Frappe — no `import frappe` allowed
- YAML schemas are the single source of truth for export definitions
- All DocType fields are programmatically set — no user editing from admin UI
- Practice Log has no `name` column and no season column — scope via date-range in meta
- Staging→final directory move is atomic (same filesystem assumed, fallback to copy+verify)
- File permissions: directories 0700, files 0600
