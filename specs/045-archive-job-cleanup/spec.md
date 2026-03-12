# Feature Specification: Archive Job Cleanup

**Feature Branch**: `045-archive-job-cleanup`
**Created**: 2026-03-12
**Status**: Draft
**Input**: Implement a safe production cleanup task for `Memora Archive Job` to remove old finished archive-job tracking records from the production database.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Automatic cleanup of old successful archive jobs (Priority: P1)

As a system operator, I need old successfully-completed archive jobs (`Purged` status) to be automatically deleted after a retention period so the `tabMemora Archive Job` table does not grow unbounded.

**Why this priority**: This is the core purpose of the feature — preventing unbounded table growth from accumulating operational tracking records that are no longer useful after archive completion.

**Independent Test**: Can be fully tested by inserting archive job rows with `Purged` status and `modified` dates older than 30 days, running the cleanup task, and verifying they are deleted while recent `Purged` rows survive.

**Acceptance Scenarios**:

1. **Given** a `Purged` archive job with `modified` older than 30 days, **When** the cleanup task runs, **Then** the job row is deleted
2. **Given** a `Purged` archive job with `modified` within the last 30 days, **When** the cleanup task runs, **Then** the job row is preserved
3. **Given** an empty `tabMemora Archive Job` table, **When** the cleanup task runs, **Then** the task completes successfully with zero deletions

---

### User Story 2 - Cleanup of old failed archive jobs with extended retention (Priority: P1)

As a system operator, I need old permanently-failed archive jobs (`Failed` status) to be deleted after a longer retention period (90 days) so they remain available for debugging and postmortem review before eventual removal.

**Why this priority**: Failed jobs carry diagnostic value. A longer retention gives operators time to investigate. This is equally critical to get right alongside success cleanup.

**Independent Test**: Can be fully tested by inserting `Failed` archive job rows with varying `modified` dates around the 90-day boundary, running cleanup, and verifying correct retention behavior.

**Acceptance Scenarios**:

1. **Given** a `Failed` archive job with `modified` older than 90 days, **When** the cleanup task runs, **Then** the job row is deleted
2. **Given** a `Failed` archive job with `modified` within the last 90 days, **When** the cleanup task runs, **Then** the job row is preserved

---

### User Story 3 - Active jobs are never deleted (Priority: P1)

As a system operator, I need the cleanup task to never delete archive jobs that are still active or in-progress, regardless of their age.

**Why this priority**: Deleting active jobs would corrupt the archive pipeline. This safety guarantee is non-negotiable.

**Independent Test**: Can be fully tested by inserting archive jobs in every non-terminal status with old `modified` dates and verifying none are deleted.

**Acceptance Scenarios**:

1. **Given** archive jobs in statuses `Pending`, `Processing`, `Exported`, `Transferred`, `Ingested`, or `Completed` with `modified` older than 90 days, **When** the cleanup task runs, **Then** all these jobs are preserved
2. **Given** a mix of active and terminal jobs, **When** the cleanup task runs, **Then** only eligible terminal jobs are deleted

---

### User Story 4 - Dependency safety with related archive batch rows (Priority: P2)

As a system operator, I need the cleanup task to skip deleting a `Purged` or `Failed` archive job if it still has related `Memora Task Log Archive Batch` rows in non-terminal states, so that child tracking records are not orphaned.

**Why this priority**: Prevents inconsistent tracking state where batch rows reference a deleted parent job. Important for operational traceability but less likely to occur in practice since batch rows typically reach terminal state before or alongside their parent.

**Independent Test**: Can be fully tested by inserting a `Purged` archive job with related batch rows in `Pending` status, running cleanup, and verifying the parent job is preserved.

**Acceptance Scenarios**:

1. **Given** a `Purged` archive job older than 30 days with all related batch rows also in terminal states (`Purged` or `Failed`), **When** the cleanup task runs, **Then** the archive job is deleted
2. **Given** a `Purged` archive job older than 30 days with at least one related batch row in a non-terminal state (`Pending`, `Exported`, or `Synced`), **When** the cleanup task runs, **Then** the archive job is preserved
3. **Given** a `Purged` archive job older than 30 days with no related batch rows at all, **When** the cleanup task runs, **Then** the archive job is deleted

---

### User Story 5 - Batched deletion with per-batch commits (Priority: P2)

As a system operator, I need the cleanup to run in small committed batches so that partial progress is preserved on failure and the database is not locked by a single large transaction.

**Why this priority**: Ensures production safety and restart-safety. A crash mid-cleanup should leave the system in a consistent state where rerunning picks up where it left off.

**Independent Test**: Can be fully tested by inserting more eligible rows than one batch size, running cleanup, and verifying multiple batches execute with commits between them.

**Acceptance Scenarios**:

1. **Given** 1200 eligible `Purged` rows (with batch size 500), **When** the cleanup task runs, **Then** rows are deleted in batches of 500 with a commit after each batch
2. **Given** the cleanup task crashes after deleting one batch, **When** the task reruns, **Then** it picks up the remaining eligible rows without duplicating deletions

---

### Edge Cases

- What happens when a `Failed` job is manually retried (status changes back to `Pending`) while cleanup is mid-batch? The job should no longer match the `Failed` filter and is safe — the SELECT already captured its name, but the row's status changed. The DELETE still succeeds (row exists) or the row is simply gone on the next batch.
- What happens if `archive_job_id` references on batch rows use a name that no longer matches any archive job? Orphaned batch rows are not this task's concern — they are handled by the existing `task_log_archive_batch_cleanup` task.
- What happens if retention days are configured to 0? The cleanup should delete all eligible terminal rows regardless of age. The implementation must accept 0 as valid.
- What happens when no eligible rows exist? The task should complete successfully with zero deletions and log accordingly.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: System MUST delete `Purged` archive jobs with `modified` older than 30 days (configurable)
- **FR-002**: System MUST delete `Failed` archive jobs with `modified` older than 90 days (configurable)
- **FR-003**: System MUST NOT delete jobs in statuses `Pending`, `Processing`, `Exported`, `Transferred`, `Ingested`, or `Completed` regardless of age
- **FR-004**: System MUST NOT delete a terminal archive job if any related `Memora Task Log Archive Batch` row (matched by `archive_job_id`) is in a non-terminal state (`Pending`, `Exported`, or `Synced`)
- **FR-005**: System MUST delete eligible rows in batches of 500 (configurable), committing after each batch
- **FR-006**: System MUST delete oldest eligible rows first, ordered by `modified ASC`
- **FR-007**: System MUST be idempotent — rerunning after partial completion or full completion produces correct results without data corruption
- **FR-008**: System MUST log task start, retention configuration, batch counts, deletion counts per status category, total deletions, and task completion
- **FR-009**: System MUST record task metrics using the existing observability pattern (task run counter, task duration histogram, processed items counter, task run log entry)
- **FR-010**: System MUST be registered in the scheduler to run once daily during low-traffic hours

### Key Entities

- **Memora Archive Job**: Tracking DocType for archive pipeline execution. Autonamed `ARCH-.#####.`. Has 8 statuses: `Pending`, `Processing`, `Exported`, `Transferred`, `Ingested`, `Completed`, `Purged`, `Failed`. Terminal states for cleanup: `Purged` (success), `Failed` (failure). Key fields: `status`, `modified`, `name`.
- **Memora Task Log Archive Batch**: Tracking DocType for individual archive batches. References parent archive job via `archive_job_id` (plain text field, not a relational link). Has 5 statuses: `Pending`, `Exported`, `Synced`, `Purged`, `Failed`. Terminal states: `Purged`, `Failed`.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: Old `Purged` archive jobs (>30 days) are automatically removed without manual intervention
- **SC-002**: Old `Failed` archive jobs (>90 days) are automatically removed without manual intervention
- **SC-003**: Active and in-progress archive jobs are never deleted, regardless of age
- **SC-004**: Cleanup task completes within 5 minutes for typical production volumes
- **SC-005**: No orphaned batch rows are created — parent jobs with active child batches are preserved
- **SC-006**: A task crash mid-cleanup requires no manual repair; rerunning the task resumes correctly
- **SC-007**: Cleanup execution is observable through logs and metrics consistent with other scheduled tasks in the project

## Assumptions

- `Completed` status is NOT a terminal-success state for cleanup purposes. A `Completed` job can still transition to `Purged` (source row purge pending), so it must be preserved. Only `Purged` is truly terminal-success.
- `Failed` jobs that are manually retried transition back to `Pending` and will no longer match the cleanup filter. No special race condition mitigation is needed beyond the standard batch-select-then-delete pattern.
- The `archive_job_id` field on `Memora Task Log Archive Batch` stores the archive job `name` as a plain string. The dependency check uses a query matching this field.
- The `modified` field on `Memora Archive Job` is used as the age indicator because it reflects the last state change timestamp. This is consistent with the existing cleanup task pattern.
- Deletion of related `Memora Task Log Archive Batch` rows is NOT in scope — that is handled by the existing `task_log_archive_batch_cleanup` task.

## Scope Boundaries

### In Scope
- Cleanup of `Memora Archive Job` rows in `Purged` and `Failed` terminal states
- Dependency check against `Memora Task Log Archive Batch` before deletion
- Scheduler registration and observability integration

### Out of Scope
- Redesigning the archive pipeline or its state machine
- Cleanup of `Memora Sync Log`
- Cleanup of `Memora Task Log Archive Batch` rows (handled by existing task)
- Changing archive business logic or adding new DocTypes
- Adding new analytics datasets
