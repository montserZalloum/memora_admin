# Feature Specification: Memora Archive System

**Feature Branch**: `039-archive-system`
**Created**: 2026-03-09
**Status**: Draft
**Input**: User description: "Season-based data archival system for Memora platform — exports ended-season data to Parquet files with dimension snapshots, managed by a standalone executor script outside Frappe runtime"

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Automatic Season Archive Creation (Priority: P1)

When a season ends, the system automatically detects it and queues an archive job for each archivable table (starting with Practice Log). The admin does not need to manually trigger archival — it happens as part of the season lifecycle.

**Why this priority**: Without automatic job creation, the entire archive pipeline has no input. This is the entry point for all downstream archival work.

**Independent Test**: Can be fully tested by ending a season and verifying that an Archive Job record is created with status "Pending", correct scope (season ID), and populated metadata.

**Acceptance Scenarios**:

1. **Given** a season has ended (end date has passed), **When** the season-check scheduled task runs, **Then** an Archive Job record is created for each registered archivable table with status "Pending", the correct `archive_scope` (season ID), and a fully populated `meta` field containing query filters, export columns, schema snapshot, and related dimension references.
2. **Given** an Archive Job already exists for the same source table, scope, and schema version combination, **When** the season-check task runs again, **Then** no duplicate job is created — enforced by a database-level unique constraint, not just application logic.
3. **Given** a season has not yet ended, **When** the season-check task runs, **Then** no archive job is created for that season.

---

### User Story 2 - Standalone Archive Execution (Priority: P1)

A standalone Python script (outside Frappe runtime) picks up pending archive jobs, exports fact data and dimension snapshots to Parquet files, builds a manifest, and marks the job as completed. The script runs via cron and processes jobs sequentially with proper locking.

**Why this priority**: This is the core archival engine. Without it, queued jobs remain pending forever. It must work independently of Frappe to avoid competing with production workers.

**Independent Test**: Can be tested by manually creating a Pending Archive Job record in the database, running the executor script, and verifying that Parquet files are produced with correct row counts, a manifest.json exists, and the job status is updated to "Completed".

**Acceptance Scenarios**:

1. **Given** an Archive Job with status "Pending" exists, **When** the executor script runs, **Then** it atomically claims the job (status changes to "Processing"), writes all output to a temporary staging directory first, exports fact data to a Parquet file matching the query filter and export columns from `meta`, takes dimension snapshots at claim time (recording `snapshot_taken_at`), exports dimension snapshot Parquet files for each related entity (scoped to referenced IDs only), validates all files, builds a manifest.json with checksums and row counts (including `snapshot_taken_at`), atomically moves the staging directory to the final archive path, and updates the job to "Completed" with `file_path`, `file_checksum`, `file_size_bytes`, `row_count`, `started_at`, `completed_at`, and `execution_stage` populated. The batch is not considered complete until the final publish (atomic move) succeeds.
2. **Given** the executor script is already running (file lock held), **When** a second instance is launched, **Then** it exits immediately without processing any jobs.
3. **Given** a job with status "Processing" has been claimed for over 1 hour, **When** the executor script starts, **Then** it marks the stuck job as "Failed" with an appropriate error message before processing other pending jobs.
4. **Given** no pending jobs exist, **When** the executor runs, **Then** it exits cleanly with no side effects.

---

### User Story 3 - Archive Job Monitoring & Retry (Priority: P2)

The admin can view all archive jobs in Frappe's admin panel, see their statuses, inspect error logs for failed jobs, and manually retry failed jobs with a single button click.

**Why this priority**: Operational visibility and recovery are essential for a production system, but the core archive pipeline (P1) must work first.

**Independent Test**: Can be tested by creating a Failed Archive Job, clicking the retry button, and verifying the job resets to "Pending" with retry count zeroed.

**Acceptance Scenarios**:

1. **Given** a Failed Archive Job, **When** the admin clicks the "Retry" button, **Then** the job status resets to "Pending", retry_count resets to 0, error_log is cleared, and the job becomes eligible for the next executor run.
2. **Given** a job with status other than "Failed", **When** the admin attempts to retry it, **Then** the system shows an error message and prevents the action.
3. **Given** an Archive Job in any status, **When** the admin opens the job record, **Then** all fields are read-only (no manual editing of job data).
4. **Given** an Archive Job in "Processing" status, **When** the admin views the job, **Then** the `execution_stage` field shows the current step (e.g., claiming, exporting_fact, exporting_dimensions, verifying, publishing, done) for diagnostic visibility.

---

### User Story 4 - Automatic Retry with Failure Escalation (Priority: P2)

When an archive job fails during execution, the system automatically retries up to 3 times. After exhausting all retries, it marks the job as permanently failed and notifies the admin.

**Why this priority**: Transient failures (disk full, DB timeout) should self-heal without admin intervention. Permanent failures need human attention.

**Independent Test**: Can be tested by simulating a failure condition (e.g., invalid query filter), running the executor, and verifying the retry count increments and the job eventually reaches "Failed" status with a notification sent.

**Acceptance Scenarios**:

1. **Given** a job fails during execution with retry_count < 3, **When** the executor handles the failure, **Then** the job returns to "Pending" status with retry_count incremented by 1.
2. **Given** a job fails with retry_count = 3, **When** the executor handles the failure, **Then** the job status becomes "Failed", the error_log contains the failure details, and a notification is sent to the System Manager role.
3. **Given** a previously failed job produced partial files in the staging directory, **When** the job is retried, **Then** the entire staging directory is deleted before the fresh export begins — partial outputs are never reused.

---

### User Story 5 - Post-Archive Source Data Purge (Priority: P3)

After an archive is confirmed complete, a separate purge process deletes the archived data from the source table in small batches to avoid locking the production database.

**Why this priority**: Purging is the final cleanup step. The archive must be reliably complete first (P1 + P2). Purging is also the most dangerous operation (data deletion), so it is intentionally deferred and separated.

**Independent Test**: Can be tested by creating a Completed Archive Job with `post_archive_action = "Delete"`, running the purge process, and verifying rows are deleted in batches with the job transitioning to "Purged".

**Acceptance Scenarios**:

1. **Given** an Archive Job with status "Completed" and `post_archive_action = "Delete"`, **When** the purge process runs, **Then** it deletes source rows in batches (default 10,000 per batch) with a pause between batches, tracks progress in the job record, and upon completion sets status to "Purged" and `source_deleted = 1`.
2. **Given** the purge process is interrupted mid-way, **When** it runs again, **Then** it resumes from where it left off (using tracked progress) without re-deleting already purged rows.
3. **Given** an Archive Job with `post_archive_action = "Keep"`, **When** the purge process runs, **Then** it skips this job entirely.

---

### User Story 6 - Schema Registry for Extensibility (Priority: P3)

Dimension snapshot definitions and archive type configurations are stored as versioned YAML files in the repository. Adding support for a new table requires only adding new YAML files — no code changes to the executor.

**Why this priority**: Extensibility is a long-term value proposition. The initial implementation (Practice Log) can work with the registry pattern from day one, enabling painless expansion later.

**Independent Test**: Can be tested by adding a new dimension YAML file and archive type YAML file, then verifying the executor correctly reads them and produces the expected dimension snapshot.

**Acceptance Scenarios**:

1. **Given** a dimension schema YAML file defines fields for a new entity, **When** the executor processes a job referencing that entity, **Then** it queries only the specified fields from the specified source table and exports them to a correctly named Parquet file.
2. **Given** an archive type YAML file references dimension schemas at specific versions, **When** the executor processes a job of that type, **Then** it uses the exact dimension schema versions specified (not latest).
3. **Given** a dimension schema version is updated (e.g., player.v2 to player.v3), **When** old archive jobs reference v2, **Then** they continue to use v2 — version immutability is maintained.

---

### User Story 7 - Transfer Verification & Local Retention (Priority: P3)

After a batch is transferred to the analytics server, the system verifies integrity by comparing checksums at the destination against the manifest. Local copies follow a clear retention policy: kept until transfer is verified, then eligible for deletion.

**Why this priority**: Transfer to the analytics server is a future capability, but the retention policy and verification protocol must be designed now to avoid data loss when the transfer pipeline is built.

**Independent Test**: Can be tested by simulating a transfer (copying batch to a second directory), running the verification check, and confirming the system correctly identifies matching vs mismatching checksums.

**Acceptance Scenarios**:

1. **Given** a Completed archive batch has been transferred to the analytics server, **When** the transfer verification runs, **Then** it computes SHA-256 checksums at the destination for every file listed in the manifest, compares them against the manifest checksums, verifies the file count matches, and only marks `transfer_status` as "Transferred" if all checks pass.
2. **Given** a batch where the destination checksum does not match, **When** the verification runs, **Then** `transfer_status` is set to "Transfer Failed", the local copy is retained, and the admin is notified.
3. **Given** a batch with `transfer_status = "Transferred"` and verified checksums, **When** the local cleanup process runs, **Then** the local batch directory is deleted and `local_deleted_at` is recorded.
4. **Given** a batch that has not yet been transferred, **When** the local cleanup process runs, **Then** the local copy is retained and the batch is skipped.

---

### Edge Cases

- What happens when the source table has zero rows matching the query filter? The job completes successfully with `row_count = 0` and produces valid (empty) Parquet files and manifest.
- What happens when the archive output directory does not exist or has incorrect permissions? The executor fails gracefully with a clear error message before processing any data.
- What happens when disk space runs out during Parquet export? The executor catches the error, cleans up partial files, and marks the job as failed with the error details.
- What happens when the Practice Log table has no season column? The scope filter uses programmatic logic (e.g., player subscriptions or date ranges from the `meta` field) rather than a direct column lookup.
- What happens when a dimension entity (e.g., a player) referenced by fact rows no longer exists in the source table? The dimension snapshot omits the missing entity; the manifest `row_count` reflects only found entities. No error is raised.
- What happens when two archive jobs for different scopes reference the same source table? They run sequentially (enforced by the file lock) and produce separate, independent batch directories.
- What happens when a race condition causes two cron runs to try creating the same archive job simultaneously? The database-level unique constraint on (source_doctype, archive_scope, schema_version) rejects the duplicate — the application catches the constraint violation and ignores it gracefully.
- What happens when a consumer (analytics server) reads the archive directory while the executor is still writing? They never see partial data because all writes happen in a staging directory; only a completed, verified batch is atomically moved to the final path.
- What happens when the executor crashes between staging completion and the atomic move to the final path? On retry, the executor finds the staging directory, deletes it entirely, and re-exports from scratch.
- What happens when the local batch is deleted before transfer verification completes? The retention policy prevents this — local copies are only eligible for deletion after `transfer_status = "Transferred"` with verified checksums.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: System MUST automatically create Archive Job records when a season ends, one per registered archivable table, with status "Pending" and fully populated metadata.
- **FR-002**: System MUST enforce uniqueness of Archive Jobs at the database level via a unique constraint on the combination of source table, archive scope, and schema version — not relying solely on application-level checks.
- **FR-003**: The standalone executor MUST acquire a file lock before processing to prevent concurrent execution on the same server.
- **FR-004**: The executor MUST atomically claim pending jobs via a single database UPDATE statement to prevent race conditions.
- **FR-005**: The executor MUST export fact data to Parquet format using the query filter and column list defined in the job's `meta` field.
- **FR-006**: The executor MUST build batch-scoped dimension snapshots containing only entities referenced by the current batch's fact data.
- **FR-007**: The executor MUST produce a `manifest.json` for each batch containing batch ID, file metadata (checksums, row counts, schema versions), and dimension scope information.
- **FR-008**: The executor MUST validate exported files (row count match, schema structure, SHA-256 checksum, file size) before marking a job as completed.
- **FR-009**: The executor MUST detect stuck jobs (Processing for over 1 hour) and mark them as Failed with an appropriate error message.
- **FR-010**: System MUST retry failed jobs up to 3 times automatically, then permanently fail and notify the admin.
- **FR-011**: System MUST provide a manual retry action (server action button) for failed jobs, visible only when status is "Failed".
- **FR-012**: All Archive Job fields MUST be read-only in the admin interface — data is set programmatically only.
- **FR-013**: The purge process MUST delete source data in configurable batches (default 10,000 rows) with a 2-second pause between batches to minimize production database impact.
- **FR-014**: The purge process MUST track deletion progress so it can resume from where it stopped if interrupted.
- **FR-015**: The purge process MUST only run on Completed jobs with `post_archive_action` set to "Delete".
- **FR-016**: Archive batch directories MUST be stored at the path specified by the `ARCHIVE_OUTPUT_PATH` environment variable (default: `/data/memora/archives/`), outside the Frappe/bench directory to isolate them from deployments.
- **FR-017**: Dimension snapshot schemas and archive type configurations MUST be defined as versioned YAML files within the repository.
- **FR-018**: The executor MUST read schema definitions from the YAML registry (located at the path specified by the `SCHEMA_REGISTRY_PATH` environment variable) to determine which fields to export for each dimension entity.
- **FR-019**: Schema versions referenced by archive jobs MUST be immutable — updating a schema creates a new version, not modifying existing ones.
- **FR-020**: Each archive batch MUST be fully self-contained — all dimension snapshots needed to analyze the fact data are included in the batch directory.
- **FR-021**: The `meta` JSON field MUST contain: `query_filter`, `related_tables`, `export_columns`, `schema_snapshot` (column definitions + primary key), and optional `notes`.
- **FR-022**: The executor MUST clean up the entire staging directory when a job fails — partial outputs are never reused on retry.
- **FR-023**: The executor MUST write all output files to a temporary staging directory first, then atomically move them to the final archive path only after all validation passes. No batch is considered complete until the final publish (move) succeeds.
- **FR-024**: Dimension snapshots MUST be taken at a defined point in time (immediately after job claim), and the `snapshot_taken_at` timestamp MUST be recorded in both the job record and the manifest.
- **FR-025**: The executor MUST track its current execution stage (claiming, exporting_fact, exporting_dimensions, verifying, publishing, done) in the job record's `execution_stage` field, updated in real-time for diagnostic visibility.
- **FR-026**: The batch directory name MUST be deterministically derived from the job identifier to ensure idempotent re-exports produce output at the same path.
- **FR-027**: A Completed or re-run job that already has a published (final path) batch MUST NOT be re-exported — the executor skips it. Only Failed/retried jobs go through full re-export.
- **FR-028**: Archive Jobs MUST track transfer lifecycle separately from archive lifecycle via dedicated fields: `transfer_status` (Pending / Transferred / Transfer Failed), `transferred_at`, and `local_deleted_at`.
- **FR-029**: Transfer verification MUST compute checksums at the destination and compare against the manifest — matching checksums, file count, and manifest integrity are all required before marking transfer as successful.
- **FR-030**: Local archive copies MUST NOT be deleted until `transfer_status = "Transferred"` with verified checksums. Batches pending transfer or with failed transfer are retained locally.
- **FR-031**: The executor MUST produce structured logs (JSON format) for each run, including: job-level metrics (started_at, completed_at, duration_seconds, row_count, retry_count, last_error) and script-level metrics (last successful run time, jobs processed count, jobs failed count).
- **FR-032**: The executor MUST update `execution_stage` to the appropriate value at each processing step, so that stuck job detection can report which stage the job was in when it stalled.
- **FR-033**: Archive batch directories MUST have filesystem permissions set to 0700 (owner-only access). No encryption at rest is required — archived data contains only internal IDs and behavioral data, not PII (names, phone numbers, etc.).

### Key Entities

- **Archive Job**: Central record tracking a single archival operation. Uniquely identified by the combination of source table, archive scope, and schema version (enforced at database level). Contains: archive status lifecycle (Pending / Processing / Completed / Purged / Failed), transfer lifecycle fields (`transfer_status`: Pending / Transferred / Transfer Failed, `transferred_at`, `local_deleted_at`), execution tracking (`execution_stage`, `snapshot_taken_at`, `started_at`, `completed_at`, `duration_seconds`), output metadata (row counts, checksums, file sizes), behavior config (post-archive action), retry tracking, and a JSON metadata field with query instructions.
- **Archive Batch Directory**: Physical output of a completed archive job. A self-contained folder with fact Parquet file(s), dimension snapshot Parquet files, and a manifest.json. Directory name is deterministically derived from the job identifier. Files are first written to a staging directory, then atomically moved to the final archive path upon successful validation.
- **Staging Directory**: Temporary working directory where the executor writes all output files during processing. Contents are considered invalid until fully verified and published (moved to final path). Cleaned up entirely on failure or retry.
- **Manifest**: JSON file describing the contents of a batch directory — batch ID, source table, scope, schema version, creation timestamp, `snapshot_taken_at`, and a list of files with their roles (fact/dimension), row counts, and SHA-256 checksums. Serves as the single source of truth for verifying batch integrity during transfer.
- **Dimension Schema Definition**: Versioned YAML file defining which fields to snapshot for a given entity (e.g., player, review_item). Global per entity — reusable across archive types.
- **Archive Type Definition**: Versioned YAML file defining a type of archive operation — which fact table to export and which dimension schemas (at specific versions) are required.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: Completed archive jobs produce Parquet files whose row counts exactly match the source data filtered by the job's query — zero data loss during export.
- **SC-002**: The archive process for 1 million Practice Log rows completes within 30 minutes (including fact export, dimension snapshots, and manifest generation).
- **SC-003**: No duplicate archive jobs are created for the same source table, scope, and schema version, even under concurrent trigger execution or cron race conditions.
- **SC-004**: Failed jobs are automatically retried within 24 hours (next scheduled cycle) without admin intervention, and permanently failed jobs trigger admin notification within 1 hour of final failure.
- **SC-005**: Source data purge of 1 million rows completes without causing query latency spikes greater than 2x baseline on the source table during the purge window.
- **SC-006**: Adding support for a new archivable table requires only adding YAML configuration files and a trigger function — no changes to the executor script.
- **SC-007**: Each archive batch can be loaded and analyzed independently using standard data analysis tools (any tool supporting Parquet) without needing access to the production database.
- **SC-008**: The archive system operates with zero impact on production response times — the executor runs outside the production application's worker pool and during off-peak hours.
- **SC-009**: No external consumer ever sees a partially written archive batch — files are only visible at the final path after full validation and atomic publish.
- **SC-010**: Transfer verification detects 100% of corrupted or truncated files by comparing checksums computed independently at source and destination.
- **SC-011**: An admin can determine the exact processing step of any in-progress or stuck job by inspecting the `execution_stage` field — no need to read logs.

## Clarifications

### Session 2026-03-09

- Q: How should archive files be protected at rest (encryption)? → A: No encryption — files contain only internal IDs (player_id, item_id) and behavioral data, not PII. Filesystem permissions (0700) suffice.
- Q: What Python environment does the standalone executor use? → A: Completely separate virtualenv with its own dependencies (pyarrow, pandas, pymysql). Reads DB credentials from environment variables.
- Q: How does the executor access versioned YAML schema files from the app repo? → A: Configurable path via environment variable (`SCHEMA_REGISTRY_PATH`) pointing to the YAML directory.
- Q: Where are archive batch directories stored? → A: Configurable via environment variable (`ARCHIVE_OUTPUT_PATH`), default `/data/memora/archives/`.
- Q: What pause duration between purge batches? → A: 2 seconds between each 10,000-row batch.

## Assumptions

- **A-001**: The Practice Log table uses a composite primary key (`player_id + item_id`) with no standard `name` column, and season scoping is determined programmatically (e.g., via player subscription dates or explicit date ranges in the `meta` field).
- **A-002**: The production server has sufficient disk space at the archive output path to hold at least one full season's archive batch before transfer to the analytics server.
- **A-003**: `pyarrow` and `pandas` will be installed on the production server for the standalone executor script.
- **A-004**: The daily cron schedule (2 AM) is acceptable for archival latency — jobs created today are processed by the next morning at latest.
- **A-005**: The built-in notification system (System Manager role) is sufficient for alerting on failed jobs — no external alerting integration (e.g., Slack, PagerDuty) is needed initially.
- **A-006**: The executor script runs in a completely separate Python virtualenv (not the Frappe bench env) with its own dependencies (pyarrow, pandas, pymysql). It connects to MariaDB directly using credentials from environment variables.
- **A-007**: Dimension snapshots are batch-scoped (only entities referenced in the current batch) rather than full table snapshots — this is intentional to keep batches self-contained and reduce storage.
- **A-008**: The `post_archive_action` field defaults to "Keep" — source data deletion requires explicit opt-in per archive job.
- **A-009**: The staging directory and final archive directory are on the same filesystem, enabling atomic `rename()` (move) operations. If they are on different filesystems, the publish step falls back to copy-then-delete with checksum verification.
- **A-010**: Transfer to the analytics server is a future capability. The transfer lifecycle fields (`transfer_status`, `transferred_at`, `local_deleted_at`) are included in the Archive Job from day one to avoid schema migration later, but the actual transfer mechanism is out of scope for the initial implementation.
- **A-011**: Structured JSON logs are written to a dedicated log file (not stdout/stderr) for easy consumption by monitoring tools.
