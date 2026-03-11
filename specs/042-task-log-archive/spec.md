# Feature Specification: Production Archival and Purge for Memora Task Run Log

**Feature Branch**: `042-task-log-archive`
**Created**: 2026-03-11
**Status**: Draft
**Input**: Production archival and safe purge of `tabMemora Task Run Log` using a two-task flow with batch tracking, deterministic Parquet export, and bounded production deletion.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Daily Archive Run (Priority: P1)

As a system operator, I want completed task run log records older than the retention window to be automatically identified and exported to Parquet so that the production table stays lightweight and older terminal records are durably preserved for auditing and analytics.

**Why this priority**: Without the export step, nothing else in the pipeline can proceed. This is the foundational step that identifies eligible rows, batches them, and produces the durable Parquet file. All other stories depend on it completing successfully.

**Independent Test**: Can be fully tested by seeding the production table with terminal-status rows spanning 120 days, running the archive task, and verifying that rows older than 90 days are exported into a Parquet batch with the correct row count and checksum, while rows within the 90-day window remain untouched in production.

**Acceptance Scenarios**:

1. **Given** the task run log contains terminal-status records with `completed_at` older than 90 days, **When** the daily archive task runs, **Then** a new `Memora Task Log Archive Batch` record is created for each batch, the rows are exported to a Parquet file, and the batch status transitions to `Exported`.
2. **Given** no rows are eligible (all records are within the retention window or already archived), **When** the archive task runs, **Then** no batch is created, the task completes gracefully, and a clear log entry records zero rows found.
3. **Given** a previous archive run already exported a batch covering a date range, **When** the archive task runs again, **Then** rows already covered by a `Synced` or later batch are skipped and are not re-exported.
4. **Given** the archive task has been running for longer than the configured runtime cap, **When** the cap is reached, **Then** the current batch is completed or rolled back cleanly, the task exits, and remaining eligible rows are processed on the next scheduled run.

---

### User Story 2 - Safe Purge Run (Priority: P2)

As a system operator, I want confirmed-archived rows to be deleted from production in small, committed batches so that the production table shrinks continuously without locking tables, degrading concurrent queries, or risking data loss.

**Why this priority**: Purge is the step that reclaims production space, but it is irreversible. It must be gated by verified archive status and must be non-disruptive. It is the second-most critical story because the system delivers no space savings until purge runs.

**Independent Test**: Can be tested by creating a batch record in `Synced` status (simulating a completed archive), running the purge task, and verifying that: (a) only rows belonging to that batch are deleted, (b) deletion occurs in sub-batches of ≤10,000 rows, (c) the batch status transitions to `Purged`, and (d) no rows outside the batch scope are affected.

**Acceptance Scenarios**:

1. **Given** a `Memora Task Log Archive Batch` with status `Synced`, **When** the purge task runs, **Then** all rows included in that batch are deleted from production in sub-batches each committed immediately, and the batch transitions to `Purged` when complete.
2. **Given** a batch with status `Exported` or `Failed` (not `Synced`), **When** the purge task evaluates it, **Then** the batch is skipped and no rows are deleted.
3. **Given** the production table's retention window of 90 days, **When** any purge run completes, **Then** no rows with `completed_at` within the last 90 days are deleted, regardless of batch scope.
4. **Given** the purge task has been running for longer than the configured runtime cap, **When** the cap is reached, **Then** the current sub-batch is committed, the task exits, and the remaining rows in the batch are processed on the next run.

---

### User Story 3 - Failure Recovery (Priority: P3)

As a system operator, I want failed or partial archive and purge operations to be safely retryable so that a transient error never causes permanent data loss, double-deletion, or a stuck batch that blocks future runs.

**Why this priority**: Any production data pipeline will encounter failures. Without idempotent retry semantics, a single failure could leave the pipeline permanently stuck or — worse — delete unverified data. Reliable recovery is essential for an unattended daily schedule.

**Independent Test**: Can be tested by injecting failures at each pipeline stage (mid-export, mid-sync, mid-purge), verifying that the batch status is set to `Failed` with an error message, and then retrying the task to confirm it picks up from the correct stage and completes without data loss or duplicates.

**Acceptance Scenarios**:

1. **Given** an archive batch that failed during Parquet export (status `Failed`), **When** the archive task runs again, **Then** the batch is retried from the export stage, any previous partial file is discarded, and on success the batch transitions to `Exported`.
2. **Given** an archive batch that succeeded at export but failed during analytics sync, **When** the archive task runs again, **Then** the existing Parquet file is reused and only the sync step is retried, avoiding redundant re-export.
3. **Given** a purge batch that was interrupted mid-way (some rows already deleted, batch still `Synced`), **When** the purge task resumes, **Then** it selects only the remaining rows using `DELETE WHERE name IN (...)` scoped to un-deleted rows in the batch, without re-deleting already-removed rows.
4. **Given** a failed batch that has exceeded the configured maximum retry count, **When** the archive task evaluates it, **Then** the batch is skipped and an alert is logged, preventing infinite retry loops.

---

### User Story 4 - Operational Visibility (Priority: P4)

As a system operator, I want to be able to inspect the status, file path, row counts, timestamps, and last error for every archive batch so that I can monitor archive health, diagnose failures, and verify data integrity without querying raw database tables.

**Why this priority**: Operational visibility makes the pipeline trustworthy and maintainable. Without it, operators cannot tell whether archiving is healthy, diagnose failures, or confirm that purges were complete.

**Independent Test**: Can be tested by running several archive and purge cycles (including one with an injected failure), then inspecting `Memora Task Log Archive Batch` records and verifying that each batch has a fully populated record with all required fields including status, row count, file path, checksum, and error message when applicable.

**Acceptance Scenarios**:

1. **Given** a completed archive batch, **When** an operator inspects its `Memora Task Log Archive Batch` record, **Then** the record shows: batch ID, source table, cutoff date, row count, Parquet file path, checksum, export timestamp, sync timestamp, and status `Synced`.
2. **Given** a failed archive batch, **When** an operator inspects its record, **Then** the record shows status `Failed`, the pipeline stage where failure occurred, and the full error message.
3. **Given** a completed purge, **When** an operator inspects the batch record, **Then** the status is `Purged`, the purge timestamp is populated, and the total rows deleted matches the original exported row count.
4. **Given** multiple batches across different date ranges, **When** an operator lists all batch records, **Then** batches are independently queryable by status, date range, and source table without ambiguity.

---

### Edge Cases

- What happens when available disk space is insufficient for a Parquet export? The archive task checks disk space before writing; if insufficient, the batch is marked `Failed` with a clear error and no file is written.
- What happens when the production database lock wait timeout is exceeded during purge? Each purge sub-batch sets `innodb_lock_wait_timeout = 5`; if a lock is not acquired within 5 seconds, the sub-batch is rolled back, logged, and retried on the next run.
- What happens when a task run log record has a `NULL` `completed_at`? Only rows with non-null `completed_at` values older than the cutoff are considered archive-eligible; null-completed rows are excluded regardless of status.
- What happens when the analytics pipeline rejects a sync? The batch status is set to `Failed`; the export file is preserved for retry; purge is not permitted until sync succeeds.
- What happens when two archive task instances run simultaneously? Batch creation is guarded so that only one batch can be in-progress per cutoff window; a second concurrent instance detects the in-progress batch and exits without creating a duplicate.
- What happens when a row's `status` is not a recognized terminal value? Non-terminal and unrecognized statuses are excluded from archive eligibility regardless of `completed_at`.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: System MUST use a configurable retention cutoff, defaulting to 90 days, to determine archive eligibility based on `completed_at`.
- **FR-002**: System MUST define archive eligibility as: `status` is a recognized terminal value (Success, Failed, or Skipped) AND `completed_at < NOW() - retention_days` AND `completed_at IS NOT NULL`.
- **FR-003**: System MUST exclude rows with NULL `completed_at` from archive eligibility regardless of status.
- **FR-004**: System MUST cap each archive batch at a maximum of 50,000 rows to bound memory usage and Parquet file size.
- **FR-005**: System MUST cap each purge sub-batch at a maximum of 10,000 rows per DELETE operation.
- **FR-006**: System MUST check available disk space before writing a Parquet export file and abort with a clear error if space is insufficient.
- **FR-007**: System MUST export each archive batch to a Parquet file with embedded schema, compression, row count, and a checksum.
- **FR-008**: System MUST write a manifest file alongside each Parquet batch recording: batch ID, source table, cutoff date, row count, checksum, and export timestamp.
- **FR-009**: System MUST create a `Memora Task Log Archive Batch` DocType record for each archive batch, tracking: batch ID, source table, cutoff date, row count, Parquet file path, checksum, current status, export/sync/purge timestamps, last error message, and retry count.
- **FR-010**: System MUST hand off successfully exported batches to the analytics archive pipeline for ingestion before permitting purge.
- **FR-011**: System MUST transition batch status to `Synced` only after the analytics pipeline confirms successful ingestion of the batch.
- **FR-012**: System MUST NOT delete any row from production unless the corresponding batch has status `Synced`.
- **FR-013**: System MUST delete archived rows using a select-then-delete pattern: first select the target row `name` values into a bounded list, then execute `DELETE WHERE name IN (...)`.
- **FR-014**: System MUST commit after every purge sub-batch so that each bounded deletion is immediately durable and independent of subsequent sub-batches.
- **FR-015**: System MUST set `innodb_lock_wait_timeout = 5` as a session variable for each purge sub-batch connection to prevent long lock waits from blocking production traffic.
- **FR-016**: System MUST enforce a configurable runtime cap (default 300 seconds) for both the archive task and the purge task, exiting cleanly without data loss when the cap is reached.
- **FR-017**: System MUST be idempotent: re-running the archive task skips batches with status `Synced` or `Purged`, and re-running the purge task resumes from remaining un-deleted rows without re-deleting already-removed rows.
- **FR-018**: System MUST retry batches with status `Failed` on the next archive run, up to a configurable maximum retry count, after which the batch is skipped and an alert is logged.
- **FR-019**: System MUST ensure the source table has a covering index on `(status, completed_at, name)` to support efficient archive eligibility queries without full table scans.
- **FR-020**: System MUST preserve all rows with `completed_at` within the configured retention window; purge operations MUST NOT delete any row within the retention window regardless of batch scope.

### Key Entities

- **Memora Task Run Log Record**: A single execution record for a scheduled or triggered task. Key attributes: unique identifier (`name`), task name, run date, started/completed timestamps, duration, terminal status (Success/Failed/Skipped), processed and failed counts, error message. Archive-eligible when status is terminal and `completed_at` is outside the retention window.
- **Memora Task Log Archive Batch**: New Frappe DocType serving as the batch tracker. Represents one bounded set of task run log rows through the full archive lifecycle. Key attributes: batch ID, source table name, cutoff date, row count, Parquet file path, checksum, current status (`Pending` → `Exported` → `Synced` → `Purged` / `Failed`), export/sync/purge timestamps, last error message, retry count.
- **Parquet Export File**: The durable binary file produced during archive export. Contains all rows for the batch in compressed columnar format with embedded schema and checksum. Stored at a configurable path organized by source table and batch date.
- **Batch Manifest**: A small metadata file co-located with the Parquet file. Records batch ID, source table, cutoff date, row count, checksum, and export timestamp for integrity verification.
- **Analytics Archive Pipeline**: The existing downstream ingestion system that receives exported Parquet batches and ingests them into the analytics historical layer. Returns a success or failure confirmation that drives the `Synced` transition.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: Production `tabMemora Task Run Log` table size remains bounded; after steady-state daily runs the table contains no more than approximately 90 days of terminal-status rows.
- **SC-002**: Zero terminal-status rows are lost during the full archive-and-purge lifecycle across 100+ archive cycles; every purged row is verifiable in the corresponding analytics batch.
- **SC-003**: No rows within the 90-day retention window are ever deleted by the purge task, regardless of batch scope or misconfiguration.
- **SC-004**: Each purge sub-batch completes in under 5 seconds under normal production load, with lock waits capped by the configured `innodb_lock_wait_timeout`.
- **SC-005**: Archive and purge tasks respect the 300-second runtime cap; any overrun exits cleanly and remaining work is deferred to the next scheduled run without data loss.
- **SC-006**: A failed batch can be retried and completed successfully on the next run without operator intervention or manual data correction.
- **SC-007**: Every archive batch has a fully populated `Memora Task Log Archive Batch` record with all required metadata fields before any purge is attempted.
- **SC-008**: The archive eligibility query executes in under 1 second for a table with up to 500,000 rows, confirmed by the covering index on `(status, completed_at, name)`.

## Scope Boundaries

### In Scope

- Archive eligibility determination based on terminal status and `completed_at` cutoff
- Batch creation and lifecycle tracking via `Memora Task Log Archive Batch`
- Parquet export with checksum and manifest
- Disk space pre-check before export
- Handoff to the existing analytics archive pipeline
- Bounded purge with sub-batch commits and lock timeout safety
- Runtime cap enforcement for both archive and purge tasks
- Idempotent retry for failed batches
- Covering index on `(status, completed_at, name)` for efficient eligibility queries

### Out of Scope

- Analytics aggregation or reporting on archived task run logs (separate analytics feature)
- Archival of non-terminal-status rows or still-running task executions
- Backfilling historical task run logs that predate this archive system
- Schema changes to `tabMemora Task Run Log` beyond the covering index
- Automated alerting or notification infrastructure when batches fail

## Assumptions

- `completed_at` is reliably populated for all terminal-status task run log records.
- Terminal statuses are: `Success`, `Failed`, `Skipped`. Any new statuses added in future must be explicitly classified as terminal or non-terminal before they affect archive eligibility.
- The existing analytics archive pipeline (used for Practice Log and Interaction Log archives) accepts batches from this source table with minimal configuration changes.
- Parquet storage is writable from the archive task's execution environment at the configured export path.
- The `Memora Task Log Archive Batch` DocType does not require a unique constraint equivalent to `idx_archive_job_unique` — batches are keyed by a generated batch ID and multiple batches can exist for different date ranges.
- The production database supports `innodb_lock_wait_timeout` as a session variable settable without elevated privileges.

## Dependencies

- Existing analytics archive pipeline (must accept and confirm Parquet batch ingestion for this source table)
- Writable Parquet storage accessible from the task execution environment
- MariaDB session-level support for `innodb_lock_wait_timeout`
- Frappe DocType registration for `Memora Task Log Archive Batch`
- Covering index `(status, completed_at, name)` on `tabMemora Task Run Log` (created as part of this feature)
