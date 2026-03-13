# Feature Specification: Memora Analytics Lakehouse

**Feature Branch**: `046-analytics-lakehouse`
**Created**: 2026-03-12
**Status**: Draft
**Input**: User description: "Production Data Export & Analytics Lakehouse — export data to Parquet, transfer via rsync/SSH to Analytics Server, enable DuckDB queries, and purge archived data from production."

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Archive Historical Data to Data Lake (Priority: P1)

As a system administrator, I want historical fact data (Practice Log, Interaction Log, Memory State, Task Run Log) to be automatically exported from the production MySQL database into Parquet files organized by Hive-style partitions, so that the production database remains small and performant while historical data is preserved for analytics.

**Why this priority**: This is the core pipeline. Without archive export, the production database grows unbounded and no analytics data exists. Every other feature depends on Parquet files being produced.

**Independent Test**: Can be fully tested by creating an Archive Job for Practice Log, running the pipeline, and verifying a valid Parquet file appears in the correct partition directory with a matching manifest.

**Acceptance Scenarios**:

1. **Given** a Pending Archive Job for Practice Log with scope `2025-12-01 to 2025-12-31`, **When** the archive pipeline runs, **Then** rows matching that date range are exported to `lake/practice_log/year=2025/month=12/day=DD/part-*.parquet` and a `manifest.json` is produced with correct row count and SHA-256 checksum.
2. **Given** a Pending Archive Job for Memory State with scope `season_seq=5`, **When** the archive pipeline runs, **Then** rows are exported to `lake/memory_state/season_seq=5/part-*.parquet` with correct schema (stability/difficulty as float64, name as int64).
3. **Given** an Archive Job that fails during export, **When** the error occurs, **Then** the job transitions to Failed status, the retry count increments, and the job is retried up to 3 times before remaining Failed.
4. **Given** a Completed Archive Job with `post_archive_action = Delete`, **When** purge runs, **Then** source rows are deleted in batches of 10,000 with 2-second sleep between batches, and the job transitions to Purged.

---

### User Story 2 - Transfer and Verify Parquet Files on Analytics Server (Priority: P1)

As a system administrator, I want exported Parquet files to be transferred to the Analytics Server via rsync/SSH with checksum verification, so that I can trust the data on the analytics side matches what was exported.

**Why this priority**: Without reliable transfer, the data lake is empty and no analytics queries can run. Transfer integrity is foundational.

**Independent Test**: Can be tested by exporting a small Parquet file locally, transferring via rsync, and comparing SHA-256 checksums on both sides.

**Acceptance Scenarios**:

1. **Given** an Exported Archive Job with a local Parquet file and manifest, **When** the transfer stage runs, **Then** the file is rsync'd to the correct Hive-partitioned path on the Analytics Server and the manifest is placed in `manifests/archive/{JOB_ID}.json`.
2. **Given** a transferred file, **When** checksum verification runs, **Then** the remote SHA-256 matches the manifest's checksum and the job transitions to Transferred.
3. **Given** a checksum mismatch after transfer, **When** verification fails, **Then** the transfer is retried up to 3 times before marking the job as Failed.

---

### User Story 3 - Query Analytics Data via DuckDB (Priority: P1)

As a data analyst, I want to query all fact datasets and dimension tables through DuckDB semantic views on the Analytics Server, so that I can perform historical analysis without touching the production database.

**Why this priority**: This is the end-user value — analytics queries. Without queryable views, exported data is useless.

**Independent Test**: Can be tested by loading DuckDB, creating the semantic views, and running `SELECT COUNT(*) FROM practice_log_archive WHERE year=2025` to verify partition pruning and data accessibility.

**Acceptance Scenarios**:

1. **Given** Parquet files exist in `lake/practice_log/year=2025/month=12/day=15/`, **When** a DuckDB query filters `WHERE year=2025 AND month=12`, **Then** only files in that partition are read (partition pruning).
2. **Given** dimension files exist in `dimensions/dim_player_history.parquet`, **When** a query joins `practice_log_archive` with `dim_player_history` on player_id and date range, **Then** the correct plan at the time of each event is returned (SCD2 join).
3. **Given** Parquet files with different schema versions (e.g., v1 missing a column added in v2), **When** queried with `union_by_name=true`, **Then** all files are readable and missing columns appear as NULL.

---

### User Story 4 - Live Sync Unarchived Practice Data (Priority: P2)

As a data analyst, I want current unarchived Practice Log data to be available for near-real-time analytics without duplicating archived data, so that the combined view provides a complete picture.

**Why this priority**: Provides near-real-time analytics. Depends on the archive pipeline (P1) to establish the exclusion boundary.

**Independent Test**: Can be tested by verifying that `practice_log_combined` (UNION ALL of archive + live) contains no duplicate rows and covers the full date range from earliest archived to current.

**Acceptance Scenarios**:

1. **Given** the latest completed Archive Job archived data up to `2025-12-31 23:59:59`, **When** Live Sync runs, **Then** it exports only rows with `last_seen_at > 2025-12-31 23:59:59` to `lake/practice_log_live/latest/`.
2. **Given** a new Archive Job completes covering January 2026, **When** the next Live Sync runs, **Then** the exclusion boundary updates to the end of January 2026 and the live snapshot no longer includes January data.
3. **Given** both archive and live data exist, **When** querying `practice_log_combined`, **Then** there are zero duplicate `(player_id, item_id, last_seen_at)` tuples.

---

### User Story 5 - Capture Structure Progress Snapshots (Priority: P2)

As a data analyst, I want daily snapshots of student structure progress captured as Parquet files partitioned by date, so that I can analyze completion trends over time.

**Why this priority**: Enables trend analysis. Independent of the archive pipeline but follows the same export/transfer pattern.

**Independent Test**: Can be tested by running a snapshot export and verifying a file appears at `lake/structure_progress/snapshot_date=2026-03-12/part-*.parquet` with correct schema.

**Acceptance Scenarios**:

1. **Given** it is 2026-03-12, **When** the daily snapshot pipeline runs, **Then** all Structure Progress rows are exported with an injected `snapshot_date=2026-03-12` column to the correct partition.
2. **Given** snapshots for 5 consecutive days exist, **When** querying with `WHERE snapshot_date='2026-03-10'`, **Then** only that day's partition is read.

---

### User Story 6 - Refresh Dimension Tables (Priority: P2)

As a system administrator, I want dimension tables (Player History, Plan, Season, Review Item, Lesson) to be exported as centralized Parquet files and refreshed on change or daily, so that analytics queries can join fact data with up-to-date contextual information.

**Why this priority**: Dimensions are required for meaningful analytics. Without them, fact tables lack context (player plans, item details, etc.).

**Independent Test**: Can be tested by triggering a dimension refresh and verifying `dimensions/dim_player_history.parquet` contains correct SCD2 rows with valid_from/valid_to boundaries.

**Acceptance Scenarios**:

1. **Given** a player changes their academic plan, **When** the Player History dimension refreshes, **Then** the previous row's `valid_to` is closed and a new row is appended with `is_current=TRUE`.
2. **Given** a daily reconciliation runs, **When** all dimensions are refreshed, **Then** each dimension file (`dim_plan.parquet`, `dim_season.parquet`, etc.) reflects the current state of its source DocType.
3. **Given** a fact query joins with `dim_player_history`, **When** using the SCD2 temporal join (`valid_from <= event_time < valid_to`), **Then** the plan active at the time of the event is returned, not the current plan.

---

### User Story 7 - Run Data Lake Health Checks (Priority: P3)

As a system administrator, I want daily automated health checks on the Analytics Server that verify data integrity, so that I am alerted to any data quality issues before analysts encounter them.

**Why this priority**: Catches data quality issues proactively. Not required for basic functionality but essential for production trust.

**Independent Test**: Can be tested by intentionally introducing a duplicate row and verifying the health check flags it.

**Acceptance Scenarios**:

1. **Given** the data lake contains archive and live practice data, **When** the duplicate check runs, **Then** any duplicate `(player_id, item_id, last_seen_at)` tuples are reported.
2. **Given** a manifest file on disk, **When** the checksum check runs, **Then** it verifies the Parquet file's SHA-256 matches the manifest and flags mismatches.
3. **Given** practice data references a player_id, **When** the dimension gap check runs, **Then** it verifies that `dim_player_history` has at least one row for that player.

---

### ~~User Story 8 - Compact Small Parquet Files~~ (Removed from production contract)

> `compact` is an analytics-only maintenance utility. It is not called by the production executor pipeline and is not part of the production-to-analytics integration contract.

---

### Edge Cases

- What happens when an Archive Job's date range overlaps with a previous completed job? The system must reject or skip already-archived ranges.
- What happens when rsync transfer is interrupted mid-file? The partial file must not be treated as valid; the next retry must re-transfer the complete file.
- What happens when a dimension source DocType has no records? The dimension file should still be created (empty Parquet with correct schema).
- What happens when the production database has rows with NULL scope column values (e.g., `last_seen_at IS NULL`)? These rows must be excluded from both archive and live sync exports.
- What happens when partition directories contain a mix of schema v1 and v2 files? DuckDB must read both correctly via `union_by_name=true`.
- What happens when a purge operation is interrupted (e.g., server restart)? The next run must resume where it left off (only delete rows not yet deleted), since the job remains in Completed status until all rows are purged.

## Requirements *(mandatory)*

### Functional Requirements

**Archive Pipeline**

- **FR-001**: System MUST export fact data (Practice Log, Interaction Log, Memory State, Task Run Log) from MySQL to Parquet format using the SQL-to-Parquet type mapping (INT/BIGINT/TINYINT -> int64, FLOAT/DOUBLE/DECIMAL -> float64, DATETIME/TIMESTAMP -> timestamp_us, DATE -> date32, VARCHAR/TEXT -> string, BOOLEAN -> bool).
- **FR-002**: System MUST organize exported Parquet files into Hive-style partition directories: `year=YYYY/month=MM/day=DD` for date-window datasets and `season_seq=XX` for season-scoped datasets.
- **FR-003**: System MUST produce a `manifest.json` for every export operation containing: batch_id, pipeline_type, source_doctype, schema_version, row_count, file_size_bytes, SHA-256 checksum, archive_scope, archive_job_id, export timestamps.
- **FR-004**: System MUST manage Archive Job lifecycle through stages: Pending -> Processing -> Exported -> Transferred -> Ingested -> Completed (-> Purged if post_archive_action=Delete), with up to 3 retries on failure at any stage.
- **FR-005**: System MUST append export metadata columns (archive_scope, archive_job_id, schema_version, exported_at) to every exported row.
- **FR-006**: System MUST support two trigger modes: `date-window` (date range filter on scope column) and `season` (season_seq filter).
- **FR-007**: System MUST purge archived source rows using batched DELETE (LIMIT 10,000) with 2-second sleep between batches when `post_archive_action=Delete` and the job reaches Completed status.

**Data Transfer**

- **FR-008**: System MUST transfer Parquet and manifest files to the Analytics Server via rsync over SSH using configuration from Memora Settings (analytics_ssh_host, analytics_ssh_user, analytics_ssh_key_path, analytics_remote_path).
- **FR-009**: System MUST verify transfer integrity by comparing the remote file's SHA-256 checksum against the manifest checksum, retrying up to 3 times on mismatch.

**Live Sync Pipeline**

- **FR-010**: System MUST export a full snapshot of unarchived Practice Log rows (WHERE scope_column > archived_until_timestamp) to `lake/practice_log_live/latest/` as a replacement snapshot.
- **FR-011**: System MUST derive the `archived_until_timestamp` from the latest Completed Archive Job's `archive_scope` field for the relevant dataset.

**Snapshot Pipeline**

- **FR-012**: System MUST capture a daily point-in-time snapshot of Structure Progress data, injecting a `snapshot_date` column derived from the export date, and storing in `lake/structure_progress/snapshot_date=YYYY-MM-DD/`.

**Dimension Tables**

- **FR-013**: System MUST export dimension tables (Player History, Plan, Season, Review Item, Lesson) as single Parquet files in `dimensions/`.
- **FR-014**: System MUST maintain Player History as an SCD Type 2 dimension with `valid_from`, `valid_to`, and `is_current` columns, appending a new row and closing the previous row on each plan change.
- **FR-015**: System MUST refresh dimensions both on source change events (DocType save hooks) and via daily full reconciliation.

**DuckDB Analytics**

- **FR-016**: System MUST provide DuckDB semantic views for all fact datasets and the combined practice log view (archive UNION ALL live).
- **FR-017**: All DuckDB views MUST use `union_by_name=true` for forward-compatible schema evolution.

**Data Lake Policies**

- **FR-018**: System MUST operate the data lake in append-only mode — no UPDATE or DELETE on Parquet files.
- **FR-019**: System MUST NOT change data types of existing columns or delete columns in new schema versions. New versions require a new dataset directory (e.g., `practice_log_v2/`).

**Production Safety**

- **FR-020**: System MUST NOT execute DELETE without LIMIT, acquire table locks, perform full table scans during export, or run analytical queries on the production database.
- **FR-021**: Export queries MUST use read-committed isolation.

**Health Checks**

- **FR-022**: System MUST run daily health checks on the Analytics Server: duplicate detection in combined practice log, manifest checksum verification, dimension coverage validation, and partition file size analysis.

### Key Entities

- **Archive Job**: Tracks the lifecycle of a single archive export operation (source_doctype, archive_scope, schema_version, status, retry_count, file_path, post_archive_action). Managed via `Memora Archive Job` DocType.
- **Live Sync Job**: Tracks each live sync export execution (dataset, archived_until boundary, status). Managed via `Memora Live Sync Job` DocType.
- **Manifest**: JSON metadata file accompanying each Parquet export (batch_id, checksums, row counts, timestamps).
- **Dimension (Player History)**: SCD Type 2 table tracking player plan changes over time (player_id, plan_id, grade, major, valid_from, valid_to, is_current).
- **Fact Tables**: Practice Log, Interaction Log, Memory State, Task Run Log, Structure Progress — the core analytical datasets exported to the lake.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: All 5 fact datasets (Practice Log, Interaction Log, Memory State, Task Run Log, Structure Progress) are exportable to Parquet and queryable via DuckDB within the data lake.
- **SC-002**: End-to-end archive pipeline (export -> transfer -> verify -> ingest confirmation) completes successfully for each dataset type with zero data loss (exported row count matches source query count).
- **SC-003**: Combined practice log view (`practice_log_combined`) contains zero duplicate `(player_id, item_id, last_seen_at)` tuples across archive and live data.
- **SC-004**: DuckDB partition pruning is effective: queries filtering by partition key read only the targeted partition directories, not the full dataset.
- **SC-005**: Purge operations never execute a DELETE without LIMIT, never hold locks for more than the time to delete 10,000 rows, and include 2-second sleep between batches.
- **SC-006**: Transfer integrity check catches 100% of corrupted transfers (SHA-256 mismatch detection).
- **SC-007**: SCD2 temporal join on `dim_player_history` returns the correct plan for a player at any historical point in time, validated by test cases with known plan change dates.
- **SC-008**: Production database shows measurable reduction in table size after purge of archived data (Practice Log and Interaction Log rows older than 60 days, Task Run Log older than 90 days, Memory State from previous seasons).
- **SC-009**: Dimension tables refresh within the same day as source changes, with daily reconciliation as a safety net.
- **SC-010**: Health checks run daily and detect at least: duplicate rows, checksum mismatches, and missing dimension coverage.

## Assumptions

- The Analytics Server is accessible from the Production Server via SSH with key-based authentication.
- DuckDB is installed on the Analytics Server and can read Parquet files from the configured `analytics_remote_path`.
- The existing `Memora Archive Job` DocType and archive pipeline infrastructure (stages, retries, purge) is already implemented and this spec extends it to support the full data lake layout with Hive partitioning, dimension management, live sync, snapshots, and health checks.
- `Memora Player Plan History` DocType exists and tracks plan change events with timestamps.
- Structure Progress data is available in `tabMemora Structure Progress` with player_id, plan_id, subject_id, and completion_percentage columns.
- The production database retains sufficient data within retention windows to rebuild the data lake from scratch if needed (per the Data Migration Policy).
- No downstream systems currently depend on any previously exported Parquet files, so a fresh rebuild is safe.
