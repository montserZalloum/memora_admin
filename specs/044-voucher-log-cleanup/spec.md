# Feature Specification: Voucher Redemption Log Cleanup

**Feature Branch**: `044-voucher-log-cleanup`
**Created**: 2026-03-11
**Status**: Draft
**Input**: User description: "Implement a production cleanup task that permanently deletes old records from Memora Voucher Redemption Log, keeping the most recent 100 days."

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Automatic Cleanup of Old Redemption Logs (Priority: P1)

As a system operator, old voucher redemption log records (older than 100 days) are automatically deleted from production on a daily schedule, keeping the database clean without manual intervention.

**Why this priority**: This is the core and only purpose of the feature — without automatic cleanup, the table grows unbounded and degrades database performance over time.

**Independent Test**: Can be fully tested by inserting rows with `creation` dates older and newer than 100 days, running the cleanup task, and verifying only old rows are removed.

**Acceptance Scenarios**:

1. **Given** the database contains voucher redemption log rows with `creation` older than 100 days, **When** the scheduled cleanup task runs, **Then** those rows are permanently deleted.
2. **Given** the database contains voucher redemption log rows with `creation` within the last 100 days, **When** the scheduled cleanup task runs, **Then** those rows remain untouched.
3. **Given** the database contains no voucher redemption log rows older than 100 days, **When** the cleanup task runs, **Then** it exits cleanly with zero deletions logged.

---

### User Story 2 - Batched Deletion for Safe Operation (Priority: P1)

As a system operator, the cleanup task deletes rows in small batches of 1000, committing after each batch, so that a failure mid-run does not lose progress and database locks are kept short.

**Why this priority**: Without batching, a single large delete could lock the table for extended periods, impacting production workloads. Batch-commit ensures restart safety.

**Independent Test**: Can be tested by inserting more than 1000 eligible rows, running the task, and verifying multiple batch commits occur and all eligible rows are deleted.

**Acceptance Scenarios**:

1. **Given** 2500 rows are eligible for deletion, **When** the cleanup task runs, **Then** it processes 3 batches (1000 + 1000 + 500), committing after each.
2. **Given** the task fails after committing the first batch, **When** the task is re-run, **Then** it continues deleting remaining eligible rows without re-processing already deleted rows.

---

### User Story 3 - Operational Logging (Priority: P2)

As a system operator, the cleanup task emits structured logs showing start, cutoff datetime, batch counts, total deleted, and duration, so I can monitor and audit cleanup runs.

**Why this priority**: Logging is essential for operational visibility but secondary to the core deletion functionality.

**Independent Test**: Can be tested by running the task and inspecting log output for expected summary fields.

**Acceptance Scenarios**:

1. **Given** the cleanup task runs and deletes rows, **When** the run completes, **Then** logs include: task start, cutoff datetime, batch size, per-batch deleted count, total deleted count, and execution duration.
2. **Given** the cleanup task encounters an error during a batch, **When** the error occurs, **Then** the error is logged with details before the task stops.

---

### Edge Cases

- What happens when a row has `creation` exactly 100 days ago? It is **not** deleted — the rule is strictly `creation < NOW() - INTERVAL 100 DAY`.
- What happens when the `timestamp` (business field) is old but `creation` is recent? The row is kept — eligibility is based solely on `creation`.
- What happens when the task is run twice in quick succession? The second run finds no eligible rows and exits cleanly (idempotent).
- What happens when a batch delete fails mid-run? Already committed batches remain deleted; the exception is raised and logged; next run picks up where it left off.
- What happens to other DocTypes in the database? They are never touched — the task only targets `Memora Voucher Redemption Log`.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: System MUST provide a scheduled cleanup task registered in `hooks.py` under `scheduler_events`, running daily.
- **FR-002**: System MUST delete only `Memora Voucher Redemption Log` rows where `creation < NOW() - INTERVAL 100 DAY`.
- **FR-003**: System MUST use `creation` (Frappe insert timestamp) as the sole eligibility field — never the business `timestamp` field.
- **FR-004**: System MUST delete in fixed batches of exactly 1000 rows per batch.
- **FR-005**: System MUST call `frappe.db.commit()` after each batch deletion.
- **FR-006**: System MUST select candidates ordered by `creation ASC, name ASC` (oldest first, deterministic).
- **FR-007**: System MUST continue batching until no more eligible rows remain.
- **FR-008**: System MUST exit cleanly with zero deletions logged when no eligible rows exist.
- **FR-009**: System MUST log: task start, cutoff datetime, batch size, per-batch deleted count, total deleted count, and total execution duration.
- **FR-010**: System MUST log error details and raise the exception if a batch delete fails, stopping the current run.
- **FR-011**: System MUST NOT affect any other DocType or table beyond `Memora Voucher Redemption Log`.
- **FR-012**: System MUST be idempotent — safe to run multiple times without side effects.
- **FR-013**: System MUST be restart-safe — already committed batches persist across failures.

### Key Entities

- **Memora Voucher Redemption Log**: Insert-only audit table recording voucher redemption attempts (successful and failed). Key field for cleanup: `creation`. Not read by runtime flows; used for admin inspection, support review, and dispute investigation.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: After a cleanup run, no `Memora Voucher Redemption Log` rows with `creation` older than 100 days remain in the database.
- **SC-002**: Rows with `creation` within the last 100 days are never deleted.
- **SC-003**: Each cleanup run completes without holding database locks for more than the duration of a single 1000-row batch delete.
- **SC-004**: The task can be interrupted and restarted at any point without data corruption or requiring manual recovery.
- **SC-005**: All 12 specified automated test cases pass.
- **SC-006**: Operational logs provide sufficient information to confirm cleanup ran, how many rows were deleted, and how long it took.

## Assumptions

- The `Memora Voucher Redemption Log` table follows standard Frappe DocType conventions with a `name` primary key and `creation` timestamp column.
- The table is not read by any runtime voucher redemption or access grant flow, making old row deletion safe.
- A 100-day retention window is sufficient for financial disputes, support review, and security investigations.
- The daily schedule (recommended ~03:00 server time) provides adequate cleanup frequency for current data volume.
- No archive or export step is needed before deletion in v1.

## Out of Scope

- Archive/export pipeline before deletion
- Analytics ingestion or dashboarding
- Schema redesign of the voucher redemption log table
- Changes to voucher redemption behavior
- Metrics framework beyond logging
