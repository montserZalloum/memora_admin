# Feature Specification: Weekly Structure Progress Snapshots

**Feature Branch**: `043-progress-snapshots`
**Created**: 2026-03-11
**Status**: Draft
**Input**: User description: "Weekly Parquet snapshots for Memora Structure Progress — preserving historical weekly time series of student progress per subject, enriched with plan_id from player profile, to support trend analytics and prevent false regression caused by plan changes."

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Weekly Progress Snapshot Generation (Priority: P1)

The system automatically captures a weekly snapshot of all current structure progress data, enriched with each student's active academic plan, and stores it as a historical record on the analytics server.

**Why this priority**: This is the core pipeline — without it, no historical data exists and none of the analytics use cases are possible.

**Independent Test**: Can be fully tested by triggering one snapshot run and verifying that a complete, correctly-grained dataset appears in storage with the expected columns and row count matching the source data (minus rejected rows).

**Acceptance Scenarios**:

1. **Given** structure progress data exists for multiple students across multiple subjects, **When** the weekly snapshot job runs, **Then** one snapshot record is created per student-plan-subject combination with the correct completion percentage.
2. **Given** the snapshot job has not run this week, **When** the scheduled time arrives (Sunday 03:00 AM Asia/Amman), **Then** the job executes automatically and writes a new weekly partition.
3. **Given** the snapshot job completes successfully, **When** reviewing the output, **Then** every row contains a valid snapshot_date, player_id, plan_id, subject_id, and completion_percentage.

---

### User Story 2 - Plan-Aware Snapshot Correctness (Priority: P1)

Each snapshot row includes the student's current academic plan at the time of capture, so that analytics can distinguish progress under different plans and avoid misinterpreting plan-change resets as regressions.

**Why this priority**: Without plan_id, the entire dataset has a correctness flaw — plan resets look like academic regression, making analytics unreliable.

**Independent Test**: Can be tested by creating snapshot data for a student, changing their plan, running another snapshot, and verifying that the two snapshots show different plan_ids for the same student-subject pair.

**Acceptance Scenarios**:

1. **Given** a student is on Plan A with 80% completion in Math, **When** the weekly snapshot runs, **Then** the snapshot records plan_id = Plan A, subject = Math, completion = 80%.
2. **Given** the same student switches to Plan B and their Math progress resets to 0%, **When** the next weekly snapshot runs, **Then** a new row appears with plan_id = Plan B, subject = Math, completion = 0%, and the previous Plan A row remains unchanged in the prior week's snapshot.
3. **Given** a student changes plan and no new structure progress has been rebuilt yet, **When** the weekly snapshot runs, **Then** the student may be absent from the snapshot for that subject — this is expected behavior, not a failure.

---

### User Story 3 - Idempotent Rerun Safety (Priority: P2)

If the weekly snapshot job is rerun for the same week (e.g., due to a retry or operator intervention), it produces an identical result without creating duplicate rows.

**Why this priority**: Idempotency prevents data corruption from retries and is essential for operational reliability, but it is secondary to the core snapshot creation logic.

**Independent Test**: Can be tested by running the snapshot job twice for the same snapshot_date and verifying row counts and content are identical after both runs.

**Acceptance Scenarios**:

1. **Given** a snapshot for 2026-03-08 already exists, **When** the job is rerun for the same date, **Then** the output contains exactly the same rows as the original run with no duplicates.
2. **Given** source data has changed between the original run and rerun for the same snapshot_date, **When** the rerun completes, **Then** the snapshot reflects the current state (overwrite behavior) and still contains no duplicates.

---

### User Story 4 - Missing Plan Rejection (Priority: P2)

Students whose player profile is missing or has no plan assigned are excluded from the snapshot output, with appropriate warnings logged for observability.

**Why this priority**: Data quality enforcement is critical for analytics reliability, but it's a guardrail on top of the core pipeline logic.

**Independent Test**: Can be tested by inserting structure progress rows for students without a player profile (or with a null plan), running the snapshot, and verifying those rows are excluded from output and warnings are logged.

**Acceptance Scenarios**:

1. **Given** a structure progress row exists for a student with no player profile, **When** the snapshot runs, **Then** that row is excluded from the output and a warning is logged.
2. **Given** a structure progress row exists for a student whose player profile has a null plan, **When** the snapshot runs, **Then** that row is excluded from the output and a warning is logged.
3. **Given** 100 structure progress rows exist and 5 have missing plans, **When** the snapshot runs, **Then** the output contains exactly 95 rows and the job reports 5 rejected rows.

---

### User Story 5 - Weekly Trend Analytics (Priority: P3)

After multiple weekly snapshots have accumulated, analytics consumers can query the dataset to view progress trends over time per student, per subject, or per plan.

**Why this priority**: This is the downstream value that the snapshot dataset enables, but it depends on the pipeline running reliably over multiple weeks first.

**Independent Test**: Can be tested by generating snapshots for 3+ consecutive weeks with varying completion percentages, then querying for a specific student's weekly trend and verifying the time series is continuous and accurate.

**Acceptance Scenarios**:

1. **Given** snapshots exist for weeks 1 through 4, **When** an analyst queries for a specific student's progress in a specific subject, **Then** they see a time series of 4 data points showing completion percentage per week.
2. **Given** snapshots exist across multiple weeks with students on different plans, **When** an analyst queries average completion by plan, **Then** the results correctly group by plan_id and show meaningful trend data.

---

### Edge Cases

- What happens when a student has structure progress but no player profile exists at all? Row is rejected with a warning; not written to snapshot.
- What happens when a student's player profile exists but the plan field is null? Row is rejected with a warning; not written to snapshot.
- What happens when a student changes plan mid-week and new structure progress hasn't been rebuilt yet? Student may be absent from the next snapshot for that subject — this is expected and not treated as a failure.
- What happens when the source table is empty (e.g., start of a new season)? The snapshot job runs successfully and writes an empty partition (zero rows).
- What happens when two students have the same subject but different plans? Each gets their own row — the grain is player_id + plan_id + subject_id.
- What happens when the job fails mid-write? The next run must cleanly overwrite or replace the incomplete partition — no partial data left behind.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: System MUST capture a weekly snapshot of all current `Memora Structure Progress` data enriched with the student's active plan.
- **FR-002**: System MUST resolve each student's `plan_id` by joining `Memora Structure Progress.player` to `Memora Player Profile.name` and reading `Memora Player Profile.plan`.
- **FR-003**: System MUST store each snapshot as a partition keyed by `snapshot_date`, with one row per unique combination of `snapshot_date`, `player_id`, `plan_id`, and `subject_id`.
- **FR-004**: System MUST include exactly these columns in each snapshot row: `snapshot_date`, `player_id`, `plan_id`, `subject_id`, `completion_percentage`.
- **FR-005**: System MUST reject any row where `plan_id` cannot be resolved (missing player profile or null plan) and log a warning for each rejected row.
- **FR-006**: System MUST count rejected rows and make the count available in job-level observability metrics.
- **FR-007**: System MUST ensure idempotent behavior — rerunning the job for the same `snapshot_date` produces identical output with no duplicate rows.
- **FR-008**: System MUST run the snapshot job on a weekly schedule (recommended: Sunday 03:00 AM Asia/Amman).
- **FR-009**: System MUST NOT modify any data in the source `Memora Structure Progress` table.
- **FR-010**: System MUST NOT include `passed_lessons_bitset` in the snapshot output.
- **FR-011**: System MUST NOT implement archive or purge logic for the snapshot dataset in this version.
- **FR-012**: System MUST store snapshots in an efficient columnar format, partitioned by `snapshot_date`.
- **FR-013**: System MUST handle an empty source table gracefully by writing an empty partition without errors.

### Key Entities

- **Structure Progress Snapshot**: A point-in-time record of a student's completion percentage for a subject under a specific plan. Key attributes: snapshot_date, player_id, plan_id, subject_id, completion_percentage.
- **Memora Structure Progress** (source): Current-state table holding the latest progress per student per subject. Does not contain plan information.
- **Memora Player Profile** (enrichment source): Profile table containing the student's current active plan (`plan` field). Used to resolve `plan_id` for each snapshot row.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: Every weekly snapshot captures 100% of structure progress rows that have a resolvable plan_id, with zero data loss for valid rows.
- **SC-002**: Snapshot grain uniqueness is maintained — zero duplicate rows exist for the same snapshot_date + player_id + plan_id + subject_id combination across all partitions.
- **SC-003**: Plan changes are correctly reflected — when a student switches plans, subsequent snapshots show the new plan_id, and prior snapshots retain the original plan_id.
- **SC-004**: All rows with missing or null plan_id are excluded from output, with 100% of rejections logged and counted.
- **SC-005**: Rerunning the snapshot job for the same week produces byte-identical output (idempotent within the same source state).
- **SC-006**: Weekly snapshots are generated reliably on the configured schedule with 99%+ success rate over any rolling 3-month period.
- **SC-007**: After 4 consecutive weekly snapshots, trend queries can produce continuous time series data per student per subject.
- **SC-008**: The snapshot job completes within a reasonable time window without impacting production system responsiveness.

## Assumptions

- The `Memora Player Profile.plan` field always reflects the student's current active plan and is the authoritative source for plan resolution.
- Structure progress data is stable enough at the scheduled snapshot time (early Sunday morning) that a single read provides a consistent view.
- The analytics server has sufficient storage for weekly snapshots growing indefinitely (weekly cadence, columnar format keeps volume manageable).
- Students with no player profile or null plan are edge cases (orphaned data), not a large percentage of the population.
- The scheduled job infrastructure already exists in the system and can run weekly tasks at a specified time and timezone.
- No archive/purge logic is needed for the foreseeable future given the low volume of weekly snapshots in an efficient storage format.

## Scope & Boundaries

### In Scope
- Weekly snapshot capture pipeline
- Plan enrichment via player profile join
- Data quality validation (missing plan rejection with logging)
- Idempotent partition writes
- Scheduled weekly execution
- Job-level observability (rejected row counts, success/failure logging)

### Out of Scope
- Modifications to the source `Memora Structure Progress` table
- Archive, purge, or retention logic for snapshots
- `passed_lessons_bitset` column
- `is_plan_changed_this_week` derived column
- Sub-weekly snapshot frequency
- Lesson-level progression history
- Derived analytics marts or dashboards
- Alerting for stalled or regressing students

## Dependencies

- **Memora Structure Progress** table must be accessible and contain current student progress data.
- **Memora Player Profile** table must be accessible and contain the `plan` field for plan resolution.
- Analytics server storage must be available for writing snapshot partitions.
- Scheduled job infrastructure must support weekly execution with timezone configuration.
