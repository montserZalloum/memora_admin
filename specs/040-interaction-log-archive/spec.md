# Feature Specification: Interaction Log Archiving

**Feature Branch**: `040-interaction-log-archive`
**Created**: 2026-03-10
**Status**: Draft
**Input**: User description: "Design and implement a safe, cumulative archiving flow for the Interaction Log table"

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Automated Daily Archive of Old Interaction Records (Priority: P1)

As a system operator, I want the system to automatically identify and archive interaction log records older than the retention window so that the production database stays lightweight and performant as the table grows toward hundreds of millions of records per year.

**Why this priority**: Without extraction and export, no other part of the archiving pipeline can function. This is the foundational capability that keeps production viable at scale.

**Independent Test**: Can be fully tested by running an archive cycle against a production-like dataset with records spanning multiple date ranges. The system should export records older than 14 days into verifiable batch files, leaving recent records untouched.

**Acceptance Scenarios**:

1. **Given** the Interaction Log contains records spanning 30 days, **When** the daily archive job runs with a 14-day retention window, **Then** all records older than 14 days are exported into batch files with correct record counts and checksums.
2. **Given** no records exist older than the retention window, **When** the archive job runs, **Then** the job completes gracefully with zero records exported and a clear log entry.
3. **Given** a previous archive job already archived records for a date range, **When** a new job runs, **Then** only records not yet successfully archived are selected for export.
4. **Given** the archive job is running, **When** the export phase fails mid-batch, **Then** the job is marked as failed, no data is lost, and the batch can be retried from the beginning.

---

### User Story 2 - Verified Transfer and Ingestion to Analytics (Priority: P2)

As a system operator, I want archived batches to be transferred to the analytics server and ingested into a cumulative historical raw layer so that full interaction history is preserved for reporting even after production records are deleted.

**Why this priority**: Transfer and ingestion ensure that data is safely stored on analytics before any production deletion occurs. Without verified ingestion, the system cannot safely purge.

**Independent Test**: Can be tested by archiving a batch, transferring it to the analytics server, ingesting it, and then querying the analytics raw layer to confirm all records are present with no duplicates.

**Acceptance Scenarios**:

1. **Given** a successfully exported batch, **When** the transfer phase runs, **Then** the batch file is delivered to the analytics server and its integrity is verified via checksum comparison.
2. **Given** a successfully transferred batch, **When** ingestion runs, **Then** all records are inserted into the historical raw layer cumulatively (appended, not replaced).
3. **Given** the same batch is ingested twice (retry scenario), **When** the second ingestion runs, **Then** no duplicate records appear in the raw layer because each record is deduplicated using its stable unique identifier (the `name` field).
4. **Given** transfer fails, **When** the pipeline resumes, **Then** it retries from the transfer phase using the same batch file without re-exporting.

---

### User Story 3 - Safe Production Deletion After Full Verification (Priority: P3)

As a system operator, I want records to be deleted from production only after all pipeline stages are verified complete so that no data is ever lost during archiving.

**Why this priority**: Deletion is the irreversible step. It must only happen after full verification to guarantee zero data loss.

**Independent Test**: Can be tested by running a complete archive cycle end-to-end and confirming that: (a) production records are deleted only after ingestion is verified, (b) deletion happens in small batches, and (c) an audit log records every deletion.

**Acceptance Scenarios**:

1. **Given** a batch has been successfully exported, transferred, ingested, and the record counts match across all stages, **When** the deletion phase runs, **Then** the archived records are deleted from production in small batches (not a single large DELETE).
2. **Given** a batch failed during ingestion, **When** the deletion phase is attempted, **Then** deletion is blocked and the job remains in a non-purged state.
3. **Given** deletion is in progress, **When** the process is interrupted mid-batch, **Then** the deletion can resume from where it left off without re-deleting already-removed records.
4. **Given** deletion completes, **When** the audit log is checked, **Then** it contains the job ID, total rows deleted, batch size, number of batches, duration, and status.

---

### User Story 4 - Recent Detailed Layer and Aggregations on Analytics (Priority: P4)

As a data analyst, I want a recent detailed layer containing the last 90 days of interaction data and daily/monthly aggregates for all history so that I can run fast queries on recent data and analyze long-term trends without scanning the entire historical archive.

**Why this priority**: Analytics layers make the archived data usable. Without them, archiving preserves data but does not enable efficient reporting.

**Independent Test**: Can be tested by ingesting multiple batches spanning several months, then verifying that the recent detailed layer contains only the last 90 days and that daily and monthly aggregate tables contain summarized data for the full ingested period.

**Acceptance Scenarios**:

1. **Given** the historical raw layer contains 6 months of interaction data, **When** the recent layer refresh runs, **Then** only the most recent 90 days of detailed records are present in the fast query layer.
2. **Given** new batches are ingested, **When** the aggregate refresh runs, **Then** daily aggregates are updated to reflect counts, time spent, and error rates grouped by day.
3. **Given** new batches are ingested, **When** the aggregate refresh runs, **Then** monthly aggregates are updated to reflect the same metrics grouped by month.
4. **Given** the recent layer already contains data, **When** a new refresh runs, **Then** records older than 90 days are removed from the recent layer and records from newly ingested batches are added.

---

### User Story 5 - Batch Logging and Observability (Priority: P5)

As a system operator, I want every archive batch to be logged with detailed metadata so that I can audit the archive process, diagnose failures, and verify data integrity at any time.

**Why this priority**: Traceability is essential for trust in the system, especially given the irreversible nature of production deletion.

**Independent Test**: Can be tested by running archive cycles (both successful and with injected failures) and verifying that every batch has a complete log entry with all required fields.

**Acceptance Scenarios**:

1. **Given** a batch completes all phases successfully, **When** the batch log is inspected, **Then** it contains: batch ID, table name, batch time range, process timestamps, record counts for each phase (extracted, transferred, ingested, deleted), final status, and whether it was a first run or retry.
2. **Given** a batch fails at any phase, **When** the batch log is inspected, **Then** it contains the failure status, the phase where failure occurred, and the error message.
3. **Given** a failed batch is retried, **When** the retry completes, **Then** the log reflects the retry status and updated record counts.

---

### Edge Cases

- What happens when the production database has no records older than the retention window? The job completes with zero records and a clear log entry.
- What happens if the analytics server is unreachable during transfer? The job fails at the transfer phase, no deletion occurs, and the batch file is preserved for retry.
- What happens if the archive job is triggered while a previous job for the same scope is still running? The system prevents concurrent jobs for the same source table and time range.
- What happens if records are inserted into the production table with timestamps older than the retention window (backdated records)? They are picked up by the next archive run.
- What happens if the process crashes mid-deletion? Deletion resumes from the last checkpoint without re-processing already-deleted records.
- What happens if the analytics raw layer already contains some records from a partial previous ingestion? Deduplication by the record's `name` field prevents duplicates.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: System MUST identify and extract all Interaction Log records older than the configured production retention window (initially 14 days) on each archive run.
- **FR-002**: System MUST NOT select records that have already been successfully archived in a previous run.
- **FR-003**: System MUST organize extracted records into time-based batches with defined start and end times.
- **FR-004**: System MUST export each batch into Parquet format with embedded schema, compression, record counts, and checksums.
- **FR-005**: System MUST transfer each batch file to the analytics server via SSH/SCP (encrypted in transit) and verify transfer integrity via checksum comparison.
- **FR-006**: System MUST ingest batch data into a cumulative historical raw layer (append-only, not replace).
- **FR-007**: System MUST deduplicate records during ingestion using the Interaction Log record's `name` field as the stable unique identifier.
- **FR-008**: System MUST NOT delete any records from production unless all of the following are confirmed: extraction succeeded, file creation succeeded, transfer succeeded, ingestion succeeded, record counts match across all phases, and successful completion was logged.
- **FR-009**: System MUST delete archived records from production in small batches (not as a single large DELETE operation).
- **FR-010**: System MUST support resumable deletion that can continue from where it left off after interruption.
- **FR-011**: System MUST maintain a recent detailed analytics layer containing only the last 90 days of interaction records for fast querying.
- **FR-012**: System MUST build daily aggregates containing at minimum: interaction counts, total time spent, error counts, and completion rates, grouped by day, player, lesson, and event type.
- **FR-013**: System MUST build monthly aggregates containing the same metrics grouped by month, player, lesson, and event type.
- **FR-014**: System MUST log every archive batch with: batch ID, table name, batch time range, process start/end timestamps, record counts per phase, status per phase, error messages (if any), and retry indicator.
- **FR-015**: System MUST be safely re-runnable: re-execution of any phase must not create duplicate data on analytics or delete unverified records from production.
- **FR-016**: System MUST prevent concurrent archive jobs for the same source table and overlapping time ranges.
- **FR-017**: System MUST support a configurable retention window, starting at 14 days with the ability to reduce to 7 days.
- **FR-018**: System MUST log every production deletion operation in an audit trail with job ID, row counts, batch size, duration, and status.

### Key Entities

- **Interaction Log Record**: An event recording a student's interaction with a lesson stage. Key attributes: unique record identifier (`name`), player reference, lesson reference, stage, event type (Started/Completed/Failed/Skipped), time spent, error count, timestamp, client metadata.
- **Archive Batch**: A unit of work representing a time-bounded set of interaction records being archived. Key attributes: batch ID, source table, time range, record count, status per pipeline phase, file path, checksum.
- **Historical Raw Layer**: The cumulative archive of all interaction records on the analytics server. Append-only, deduplicated by record identifier.
- **Recent Detailed Layer**: A rolling 90-day window of detailed interaction records on analytics, refreshed after each ingestion.
- **Daily Aggregate**: Summarized interaction metrics grouped by day, player, lesson, and event type, covering all archived history.
- **Monthly Aggregate**: Summarized interaction metrics grouped by month, player, lesson, and event type, covering all archived history.
- **Archive Audit Log**: A record of every deletion operation performed during archive purges, including counts, timing, and status.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: Production Interaction Log table size decreases continuously as daily archives run, staying within approximately 14 days of data volume.
- **SC-002**: Zero records are lost during the archive process across 100+ archive cycles.
- **SC-003**: Zero duplicate records appear on the analytics server after retries or re-runs of any archive batch.
- **SC-004**: Full historical interaction data is queryable on the analytics server at any time.
- **SC-005**: The recent detailed analytics layer contains no records older than 90 days after each refresh.
- **SC-006**: Daily and monthly aggregate tables are updated within one hour of each successful batch ingestion.
- **SC-007**: Every archive batch has a complete log entry with all required metadata fields populated.
- **SC-008**: Production deletion of archived records has no measurable impact on concurrent read/write operations (deletion happens in small batches with pauses).
- **SC-009**: A failed archive batch can be retried and completed without manual intervention or data correction.
- **SC-010**: The system can handle archiving 1 million interaction records per daily run within a maximum of 2 hours wall-clock time (export through ingestion, excluding deletion).

## Clarifications

### Session 2026-03-10

- Q: Should daily/monthly aggregates group by additional dimensions beyond day/month and player? → A: Yes — group by player + lesson + event_type for richer analytics without excessive cardinality.
- Q: What is the maximum acceptable wall-clock time for a daily archive cycle of 1M records? → A: 2 hours (export through ingestion, excluding deletion).
- Q: What export file format should be used for archive batch files? → A: Parquet (columnar, compressed, schema-embedded, analytics-native).
- Q: Should batch files be encrypted in transit and/or at rest during transfer? → A: Encrypted in transit only (SSH/SCP), plaintext on analytics server.
- Q: Should the Interaction Log archive reuse the existing pipeline or be a separate module? → A: Shared generic pipeline — single codebase with doctype-specific config (column mappings, aggregation definitions).

## Assumptions

- The `name` field on the Interaction Log DocType is globally unique and stable, making it suitable as the deduplication key for analytics ingestion.
- The `timestamp` field on the Interaction Log is the appropriate column for determining record age relative to the retention window.
- The existing archive executor pipeline (Pending -> Processing -> Exported -> Transferred -> Ingested -> Completed -> Purged) will be generalized into a shared generic pipeline that handles both Practice Log and Interaction Log via doctype-specific configuration (column mappings, aggregation definitions, retention windows).
- The analytics server infrastructure (remote storage, ingestion endpoint) already exists from the Practice Log archive implementation.
- Daily and monthly aggregates will initially include: interaction count, total time spent, total errors, and completion rate (completed events / total events), grouped by player, lesson, and event type. Additional metrics can be added later.
- The production retention window starts at 14 days and can be configured down to 7 days without code changes.
- Archive batches will be scoped by date range (e.g., one day per batch) to keep batch sizes manageable and allow granular retry.
