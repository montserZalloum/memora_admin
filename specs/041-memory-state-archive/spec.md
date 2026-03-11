# Feature Specification: Memora Memory State Archive Lifecycle

**Feature Branch**: `041-memory-state-archive`
**Created**: 2026-03-11
**Status**: Draft
**Input**: End-to-end lifecycle for Memora Memory State covering production storage, analytics ingestion, season archival, archived data retention, and production cleanup safety.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Incremental Analytics Sync for Active Seasons (Priority: P1)

As an analytics system, I need to incrementally sync current Memory State data for all active seasons so that dashboards reflect the latest learner memory health without storing repeated full snapshots.

**Why this priority**: Without incremental sync, analytics either has stale data or must copy the entire (large) table every cycle, causing storage explosion and slow queries. This is the foundational data flow that all other analytics features depend on.

**Independent Test**: Can be tested by running a sync cycle for an active season and verifying that only changed rows (based on `modified` timestamp with safety overlap) are upserted into the analytics current mirror, and that row counts match expectations.

**Acceptance Scenarios**:

1. **Given** an active season with Memory State rows modified since the last sync checkpoint, **When** a sync cycle runs, **Then** only rows modified within the safe incremental window are extracted and upserted into the analytics current mirror table.
2. **Given** two active seasons running simultaneously, **When** a sync cycle runs, **Then** each season is synced independently with its own per-season checkpoint, and partition pruning is preserved by season-scoped queries.
3. **Given** a sync cycle where timestamp boundaries overlap with the previous cycle, **When** upsert is applied, **Then** duplicate rows are merged (not appended), and no data is lost due to timestamp-boundary gaps.
4. **Given** no rows have been modified since the last sync, **When** a sync cycle runs, **Then** the sync completes without inserting any rows and updates the checkpoint timestamp.

---

### User Story 2 - Season Archive Export and Validation (Priority: P1)

As the archive system, I need to export a final season snapshot when a season becomes archive-eligible and validate its completeness before allowing any production cleanup.

**Why this priority**: This is the critical safety gate. Without validated archive export, production cleanup would risk permanent data loss. Archive eligibility is derived from the season's `end_date` being in the past.

**Independent Test**: Can be tested by marking a season as ended (end_date in the past), triggering the archive export, verifying the Parquet output matches the source row count and integrity checks, and confirming the Memora Archive Job record reflects the validation outcome.

**Acceptance Scenarios**:

1. **Given** a season whose `end_date` has passed, **When** the archive pipeline identifies it as eligible, **Then** a final export of all Memory State rows for that season is created.
2. **Given** a completed archive export, **When** validation runs, **Then** row-count consistency is confirmed between source data and the archived Parquet file, and additional integrity checks (checksum, metadata consistency) pass.
3. **Given** validation succeeds, **When** the archive job status is updated, **Then** the corresponding Memora Archive Job record reflects successful export, transfer, and validation timestamps along with row count, checksum, and file size.
4. **Given** validation fails (row count mismatch or integrity error), **When** the archive job status is updated, **Then** the job is marked as failed with error details, and no production cleanup is permitted.

---

### User Story 3 - Analytics Archive Storage and Current Mirror Cleanup (Priority: P2)

As the analytics system, I need to store archived season data as compressed Parquet files organized by season and remove that season from current mirror tables so that dashboards remain fast and storage-efficient.

**Why this priority**: Once a season is archived, keeping it in current analytics tables bloats storage and slows dashboard queries. Moving to Parquet preserves data for explicit historical analysis while keeping current tables lean.

**Independent Test**: Can be tested by archiving a completed season, verifying Parquet files exist at `archive/memory_state/season_<season_id>/`, confirming the season's rows are removed from the current mirror table, and verifying dashboard queries no longer include that season's data.

**Acceptance Scenarios**:

1. **Given** a successfully validated archive export, **When** the analytics archive transition runs, **Then** the season's data is written to compressed Parquet at `archive/memory_state/season_<season_id>/`.
2. **Given** Parquet storage is complete, **When** the current mirror cleanup runs, **Then** all rows for that season are removed from the analytics current mirror table.
3. **Given** an archived season, **When** a normal dashboard query executes, **Then** it reads only from the current mirror and does not scan archived Parquet files.
4. **Given** multiple archived seasons exist, **When** explicit historical analysis is requested for a specific season, **Then** only that season's Parquet files are read without loading other archived seasons.

---

### User Story 4 - Production Cleanup with Safety Gates (Priority: P2)

As a system administrator, I need production cleanup of archived seasons to be gated by archive validation, dependency review, and active-linkage checks so that no operational workflows are broken.

**Why this priority**: Production cleanup is irreversible. Without safety gates, deleting Memory State rows could break schedulers, APIs, background jobs, or active player workflows that still depend on that season's data.

**Independent Test**: Can be tested by attempting production cleanup for an archived season, verifying that cleanup is blocked when any active player/plan linkage exists, verifying cleanup is blocked when archive validation has not succeeded, and verifying cleanup proceeds (via DROP PARTITION) only when all gates pass.

**Acceptance Scenarios**:

1. **Given** a season with successful archive validation and no active player/plan linkage, **When** production cleanup is triggered, **Then** the season's partition is dropped via MariaDB `DROP PARTITION` (O(1) metadata operation).
2. **Given** a season with successful archive validation but an active player profile still linked to it, **When** production cleanup is attempted, **Then** cleanup is blocked and a clear message indicates the active-linkage blocker.
3. **Given** a season whose archive validation failed, **When** production cleanup is attempted, **Then** cleanup is blocked and the Memora Archive Job record shows the failure reason.
4. **Given** a season being cleaned up, **When** the cleanup completes, **Then** the Memora Archive Job record is updated with source deletion status and purge progress.

---

### User Story 5 - Per-Season Sync and Archive Metadata Tracking (Priority: P3)

As the system, I need to maintain per-season control metadata on both the production and analytics sides so that sync state, archive state, and cleanup eligibility are tracked independently for each season.

**Why this priority**: Without per-season metadata, the system cannot safely manage multiple concurrent active and archived seasons. This supports all other stories by providing the control state they depend on.

**Independent Test**: Can be tested by running sync and archive operations across multiple seasons simultaneously and verifying that each season's metadata (last sync time, export status, validation status, mirror inclusion state, Parquet location) is tracked and updated independently.

**Acceptance Scenarios**:

1. **Given** three active seasons, **When** sync runs for each, **Then** each season has its own last-successful-sync timestamp in the analytics control metadata.
2. **Given** one season transitions from active to archived, **When** its archive completes, **Then** its metadata shows archived state with Parquet location, while other seasons remain marked as active in the current mirror.
3. **Given** the production-side Memora Archive Job for a season, **When** any archive lifecycle event occurs (export, transfer, validation, cleanup), **Then** the corresponding timestamps and status fields are updated in that job record.

---

### Edge Cases

- What happens when a season's `end_date` passes but the archive pipeline is unavailable or delayed? The season remains in production and current analytics until the pipeline successfully runs; no automatic cleanup occurs.
- What happens when a sync cycle fails mid-way through a season's rows? The checkpoint is not advanced, and the next cycle re-extracts from the last successful checkpoint with safety overlap, ensuring no data loss.
- What happens when an active player is linked to a season that ended months ago? The active-linkage blocker prevents cleanup regardless of how long ago the season ended, until the linkage is resolved.
- What happens when multiple archive jobs target the same season simultaneously? The existing `idx_archive_job_unique` constraint on Memora Archive Job prevents duplicate jobs for the same (source_doctype, archive_scope, schema_version) combination.
- What happens when the analytics server restarts mid-archive-transition? The archive transition should be idempotent; re-running it picks up from the last completed step without duplicating Parquet files or double-deleting from the current mirror.
- What happens when a partition DROP fails because the table is not partitioned? The system falls back to a safe alternative or blocks cleanup with an error, never silently performing row-by-row deletion on a large table.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: System MUST incrementally sync Memory State rows for active seasons using a modification-timestamp-based strategy with configurable safety overlap to prevent gap-based data loss.
- **FR-002**: System MUST track sync state per season (not as a single global cursor), supporting multiple simultaneously active seasons.
- **FR-003**: Sync extraction MUST be season-scoped to preserve MariaDB partition pruning, not just timestamp-scoped.
- **FR-004**: Analytics current mirror MUST use upsert/merge behavior so that overlapping sync windows do not create duplicate rows.
- **FR-005**: System MUST determine archive eligibility from existing season metadata (`end_date` in the past) without requiring new production schema fields.
- **FR-006**: System MUST export a final season snapshot as compressed Parquet when a season becomes archive-eligible and the archive pipeline runs.
- **FR-007**: System MUST validate archive completeness through row-count consistency checks, checksum verification, and metadata consistency before marking the archive as successful.
- **FR-008**: Archive validation outcome MUST be reflected in the corresponding Memora Archive Job record on production.
- **FR-009**: System MUST remove archived season data from analytics current mirror tables after successful archive storage.
- **FR-010**: Archived Parquet files MUST be organized by season (e.g., `archive/memory_state/season_<season_id>/`) with each season fully separated.
- **FR-011**: Normal dashboard queries MUST read only from the current mirror and MUST NOT scan archived Parquet data by default.
- **FR-012**: System MUST block production cleanup if archive validation has not succeeded.
- **FR-013**: System MUST block production cleanup if any active player profile or academic-plan linkage still depends on that season's Memory State data.
- **FR-014**: System MUST block production cleanup if a dependency/impact review has not confirmed that no operational workflow relies on the season's rows.
- **FR-015**: Where the production table is season-partitioned, cleanup MUST use MariaDB `DROP PARTITION` (O(1) metadata operation) rather than row-by-row deletion.
- **FR-016**: All archive lifecycle events (export, transfer, validation, cleanup) MUST be recorded in the existing Memora Archive Job as the authoritative production-side audit trail.
- **FR-017**: System MUST NOT store repeated full snapshots of Memory State on analytics; only current-state mirror and final archived Parquet are permitted.
- **FR-018**: System MUST support explicit historical queries against archived Parquet when requested, without requiring the data to be in the current mirror.

### Key Entities

- **Memory State Row**: A single learner-item-season record representing current memory/scheduling state. Key attributes: player_id, item_id, season, stability, difficulty, next_review_date, modified timestamp. Updated in-place as learners interact.
- **Season**: An academic period with a defined `end_date`. Determines archive eligibility. Multiple seasons may be active simultaneously.
- **Memora Archive Job**: Existing Frappe DocType serving as the production-side audit/control record for archive operations. Tracks source, status, timestamps, row count, checksum, file paths, and error information. Unique constraint on (source_doctype, archive_scope, schema_version).
- **Analytics Current Mirror**: The analytics-side representation of Memory State for active seasons only. Incrementally synced via upsert. Cleaned up when a season is archived.
- **Analytics Sync Metadata**: Per-season control state tracking last sync time, export status, validation status, mirror inclusion, and Parquet archive location.
- **Archived Season Parquet**: Compressed Parquet files storing the final snapshot of a season's Memory State data, organized per-season in the archive storage hierarchy.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: Analytics current mirror for active seasons reflects changes within one sync cycle interval (configurable, default target: within 15 minutes of source modification).
- **SC-002**: No duplicate rows exist in the analytics current mirror after any number of sync cycles, even with overlapping extraction windows.
- **SC-003**: Archived season Parquet files match the source data with 100% row-count accuracy and pass checksum validation before production cleanup is allowed.
- **SC-004**: Dashboard query response time does not degrade as seasons are archived, because archived data is excluded from current mirror tables.
- **SC-005**: Production cleanup for a partitioned season completes in under 1 second (DROP PARTITION is O(1)), not proportional to row count.
- **SC-006**: Zero production data loss occurs during archival, verified by archive validation passing before any cleanup is permitted.
- **SC-007**: Active-linkage safety gate prevents cleanup 100% of the time when any player/plan is still linked to the season.
- **SC-008**: System correctly manages at least 3 concurrent active seasons and 10+ archived seasons without metadata conflicts or cross-season data leakage.
- **SC-009**: Analytics storage growth rate is proportional to the number of active-season changes (not to the total table size), confirming no repeated full snapshots occur.

## Scope Boundaries

### In Scope

- Memory State incremental sync to analytics (current mirror)
- Memory State season archive export, validation, and Parquet storage
- Analytics current mirror cleanup after archival
- Production cleanup safety gates (archive validation, active-linkage check, dependency review)
- Production cleanup via DROP PARTITION
- Per-season sync and archive metadata tracking
- Memora Archive Job as production audit trail

### Out of Scope

- Practice Log archival (separate feature, separate lifecycle)
- Historical event-level analytics (belongs to the event-log layer)
- Archived aggregate tables/views (optional future enhancement)
- Production schema changes for archive state tracking
- New production archive-control tables

## Assumptions

- The production `tabMemora Memory State` table is (or will be) partitioned by season, enabling DROP PARTITION cleanup.
- The `modified` timestamp on Memory State rows is reliably updated on every state change and can be used for incremental extraction.
- Season metadata (including `end_date`) is available from existing production tables without new fields.
- The existing Memora Archive Job DocType has sufficient fields to record all archive lifecycle events without schema changes.
- The analytics server has access to a Parquet-compatible storage layer for archived data.
- Active player/plan linkage to a season can be determined from existing production data (player profiles, academic plans) without new fields.
- Multiple seasons may be active simultaneously (e.g., overlapping academic terms).

## Dependencies

- Existing Memora Archive Job DocType and its unique constraint (`idx_archive_job_unique`)
- MariaDB partition management capabilities on the production table
- Season metadata availability (specifically `end_date`) from existing tables
- Analytics storage infrastructure supporting Parquet read/write
- Player profile / academic plan tables for active-linkage checking
