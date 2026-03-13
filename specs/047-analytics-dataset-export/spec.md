# Feature Specification: Educational Analytics Dataset Export

**Feature Branch**: `047-analytics-dataset-export`
**Created**: 2026-03-13
**Status**: Draft
**Input**: User description: "Export clean datasets (Practice Log, Item→Curriculum Mapping, Content Hierarchy, Academic Context) to Parquet for the analytics server to compute educational performance reports."

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Export Practice Log for Learning Analytics (Priority: P1)

As an analytics engineer, I want the full practice log — player attempts, correct counts, and timestamps — exported to Parquet so that the analytics server can compute learning performance metrics (success rate, retention behavior, avg attempts) without touching the production database.

**Why this priority**: The practice log is the authoritative source of learning outcomes. Every analytics report (Curriculum Difficulty Map, Memory Retention, Lesson Performance, Topic Mastery) depends on this data. Without it, no educational metrics can be computed.

**Independent Test**: Can be fully tested by triggering a practice log export and verifying a valid Parquet file appears at `analytics_exports/practice_log.parquet` containing all required fields (`player_id`, `item_id`, `attempt_count`, `correct_count`, `first_seen_at`, `last_seen_at`, `last_result`) with no duplicate `(player_id, item_id)` rows.

**Acceptance Scenarios**:

1. **Given** the practice log table contains student attempt records, **When** the export runs, **Then** a Parquet file is produced at `analytics_exports/practice_log.parquet` containing all seven required fields with correct types and no duplicate `(player_id, item_id)` rows.
2. **Given** a previous export exists, **When** the export runs again incrementally, **Then** only rows with `last_seen_at` newer than the previous export watermark are added/updated, and the total row count reflects the current state of the source table.
3. **Given** a student has zero correct attempts (`correct_count = 0`) or `last_result = 0`, **When** the export runs, **Then** those rows are included without modification (zeroes are valid data, not errors).
4. **Given** the export runs while students are actively reviewing, **When** the export reads the table, **Then** no table locks are acquired and concurrent student activity is not blocked.

---

### User Story 2 - Export Item → Curriculum Mapping (Priority: P1)

As an analytics engineer, I want a mapping from each review item to its full curriculum path (lesson, topic, unit, track, subject) exported to Parquet, so that the analytics server can roll up practice log metrics to any curriculum level.

**Why this priority**: Without this mapping, the analytics server can compute per-item metrics but cannot aggregate to lesson, topic, unit, track, or subject level. This is a prerequisite for all curriculum-level reports.

**Independent Test**: Can be fully tested by exporting `analytics_exports/item_mapping.parquet` and verifying that each `item_id` maps to a non-null `lesson_id`, `topic_id`, `unit_id`, `track_id`, and `subject_id`, and that joining with `practice_log.parquet` on `item_id` produces zero unmatched rows for active items.

**Acceptance Scenarios**:

1. **Given** review items exist with full curriculum assignments, **When** the export runs, **Then** `analytics_exports/item_mapping.parquet` contains one row per `item_id` with all six fields populated (`item_id`, `lesson_id`, `topic_id`, `unit_id`, `track_id`, `subject_id`).
2. **Given** a practice log row references an `item_id`, **When** joined with `item_mapping.parquet` on `item_id`, **Then** every active practice log item resolves to a valid curriculum path.
3. **Given** an item exists but is not yet assigned to a lesson, **When** the export runs, **Then** that item is excluded from the mapping (null curriculum paths are omitted).

---

### User Story 3 - Export Content Hierarchy for Curriculum Rollups (Priority: P2)

As an analytics engineer, I want the curriculum hierarchy (subjects, tracks, units, topics, lessons) exported as separate Parquet files so that the analytics server can perform rollups and compute named curriculum metrics.

**Why this priority**: Without the hierarchy, the analytics server can join items to their IDs but cannot display human-readable curriculum names or compute rollups at each level. Depends on item mapping (P1).

**Independent Test**: Can be fully tested by exporting all five hierarchy files and verifying that parent references form a valid tree: each lesson links to a topic, each topic to a unit, each unit to a track, and each track to a subject.

**Acceptance Scenarios**:

1. **Given** curriculum hierarchy data exists, **When** the export runs, **Then** five Parquet files are produced: `subjects.parquet`, `tracks.parquet`, `units.parquet`, `topics.parquet`, `lessons.parquet`, each with at least `id` and `name` fields plus appropriate parent references.
2. **Given** a lesson in `lessons.parquet`, **When** traversing `topic_id → topics.parquet → unit_id → units.parquet → track_id → tracks.parquet → subject_id → subjects.parquet`, **Then** every foreign key resolves without orphaned references.
3. **Given** a curriculum entity is unpublished or inactive, **When** the export runs, **Then** it is still included (analytics needs full hierarchy for historical data joins, even on inactive curriculum items).

---

### User Story 4 - Export Academic Context for Performance Segmentation (Priority: P2)

As an analytics engineer, I want academic context data (seasons, grades, majors, academic plans, grade-major links) exported to Parquet so that the analytics server can segment and filter performance metrics by season, grade, major, and academic plan.

**Why this priority**: Without academic context, the analytics server cannot answer "how did Grade 10 students in the Science major perform in Season 3?" — the key segmentation question for institutional reporting.

**Independent Test**: Can be fully tested by exporting all five academic context files and verifying that `academic_plans.parquet` references valid `season`, `grade`, and `major` IDs that exist in their respective export files.

**Acceptance Scenarios**:

1. **Given** academic context data exists, **When** the export runs, **Then** five Parquet files are produced: `seasons.parquet`, `grades.parquet`, `majors.parquet`, `academic_plans.parquet`, `grade_majors.parquet`.
2. **Given** `academic_plans.parquet` is joined with `seasons.parquet`, `grades.parquet`, and `majors.parquet`, **Then** every foreign key (`season`, `grade`, `major`) resolves to a row in the corresponding file with no orphaned references.
3. **Given** a plan is not yet published, **When** the export runs, **Then** it is still included (analytics needs all plans for complete segmentation, including draft ones for internal reporting).

---

### Edge Cases

- What happens when a practice log row has a `last_seen_at` in the far future (data corruption)? The row must still be exported as-is; filtering is the analytics server's responsibility.
- What happens when an item exists in the practice log but not in the item mapping (item was deleted from curriculum)? The item mapping export must include a note in documentation that unresolvable items will appear as orphaned rows in the practice log; analytics must handle LEFT JOIN gracefully.
- What happens when the export runs and a new student completes a review mid-export? The export must capture a consistent snapshot; rows added after the export starts are picked up in the next run.
- What happens when a hierarchy node (e.g., a topic) has no children lessons? It is still exported in `topics.parquet` — empty parent nodes are valid.
- What happens when `grade_majors.parquet` (child table of grade) contains no rows for a given grade? That grade still appears in `grades.parquet`; `grade_majors.parquet` simply has no rows with that grade as parent.
- What happens when the export produces a zero-row file? A valid empty Parquet file with correct schema is still produced to signal a successful export of an empty dataset.

## Requirements *(mandatory)*

### Functional Requirements

**Practice Log Export**

- **FR-001**: System MUST export all rows from the practice log as Parquet to `analytics_exports/practice_log.parquet` with the following fields: `player_id`, `item_id`, `attempt_count`, `correct_count`, `first_seen_at`, `last_seen_at`, `last_result`.
- **FR-002**: Practice log export MUST support incremental mode: on subsequent runs, only rows modified (by `last_seen_at`) since the previous export watermark are processed, reducing export duration for large tables.
- **FR-003**: Practice log export MUST produce zero duplicate `(player_id, item_id)` rows in the output (the source has a composite PK; the export must reflect this).
- **FR-004**: Practice log export MUST NOT acquire table locks or use queries that block concurrent writes.

**Item → Curriculum Mapping Export**

- **FR-005**: System MUST export the item-to-curriculum mapping as Parquet to `analytics_exports/item_mapping.parquet` with fields: `item_id`, `lesson_id`, `topic_id`, `unit_id`, `track_id`, `subject_id`.
- **FR-006**: The mapping export MUST source item-curriculum links from the Memora Review Item and related Lesson/Stage tables (the canonical content structure tables).
- **FR-007**: Items without a fully resolved curriculum path (missing `lesson_id` or higher) MUST be excluded from the mapping export.
- **FR-008**: The mapping export is a full snapshot (no incremental mode); it must always reflect the current curriculum assignments.

**Content Hierarchy Export**

- **FR-009**: System MUST export five content hierarchy Parquet files: `analytics_exports/subjects.parquet`, `tracks.parquet`, `units.parquet`, `topics.parquet`, `lessons.parquet`.
- **FR-010**: Each hierarchy file MUST include at minimum: `id` (stable identifier), `name` (display name), and the appropriate parent reference field (`track_id` for tracks, `unit_id` for units, `topic_id` for topics, `lesson_id`'s parent `topic` for lessons).
- **FR-011**: Content hierarchy exports MUST be full snapshots refreshed on each run; incremental mode is not required due to the small size of curriculum tables.
- **FR-012**: Content hierarchy exports MUST include both published and unpublished entities to support historical joins.

**Academic Context Export**

- **FR-013**: System MUST export five academic context Parquet files: `analytics_exports/seasons.parquet`, `grades.parquet`, `majors.parquet`, `academic_plans.parquet`, `grade_majors.parquet`.
- **FR-014**: `seasons.parquet` MUST include: `id`, `name`, `season_seq`, `start_date`, `end_date`.
- **FR-015**: `grades.parquet` MUST include: `id`, `name`, `sort_order`.
- **FR-016**: `majors.parquet` MUST include: `id`, `name`.
- **FR-017**: `academic_plans.parquet` MUST include: `id`, `name`, `season`, `grade`, `major`, `is_published`.
- **FR-018**: `grade_majors.parquet` MUST include: `grade` (parent), `major` — representing the allowed major options per grade.
- **FR-019**: Academic context exports MUST be full snapshots; all records are included regardless of published status.

**Export Format & Delivery**

- **FR-020**: All exports MUST produce valid Parquet files with deterministic column order and stable column names.
- **FR-021**: All exports MUST be placed in the directory layout: `analytics_exports/{dataset}.parquet` as specified in the PRD.
- **FR-022**: System MUST NOT re-implement the Interaction Log export — the existing archive pipeline handles it.
- **FR-023**: Exports MUST use read-committed or snapshot isolation to avoid blocking production writes.

**Data Quality**

- **FR-024**: Each export run MUST validate: no duplicate primary key rows, no null values in required ID fields, row count > 0 for tables expected to have data (configurable minimum).
- **FR-025**: Export failures MUST be logged with enough detail (dataset name, row count at failure, error message) to diagnose and retry.

### Key Entities

- **Practice Log Row**: One row per `(player_id, item_id)` pair representing the cumulative learning history for that student-item combination. Exported verbatim from production.
- **Item Mapping Row**: One row per `item_id` resolving the item to its full curriculum path (`lesson_id` → `topic_id` → `unit_id` → `track_id` → `subject_id`). Derived from the content structure tables.
- **Hierarchy Node**: A single curriculum entity (subject, track, unit, topic, or lesson) with its own `id`, display `name`, and reference to its parent entity.
- **Academic Plan**: A published or draft combination of `season`, `grade`, and `major` that defines an educational cohort context.
- **Season**: An academic period with `season_seq` (integer ordering key), `start_date`, and `end_date` used for segmentation.
- **Grade Major Link**: A child record expressing which majors are valid options for a given grade.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: After all exports complete, the analytics server can compute `attempts`, `correct_attempts`, `success_rate`, `unique_students`, and `avg_attempts_per_student` for any curriculum level (lesson, topic, unit, track, subject) by joining `practice_log.parquet` → `item_mapping.parquet` → hierarchy files.
- **SC-002**: All exports complete with zero duplicate primary key rows (verified by row count = distinct key count for each output file).
- **SC-003**: Every `item_id` in `practice_log.parquet` that has an active curriculum assignment resolves to a row in `item_mapping.parquet` (zero unmatched active items when joined on `item_id`).
- **SC-004**: Every foreign key in `item_mapping.parquet` (`lesson_id`, `topic_id`, `unit_id`, `track_id`, `subject_id`) resolves to a row in the corresponding hierarchy export file.
- **SC-005**: Every foreign key in `academic_plans.parquet` (`season`, `grade`, `major`) resolves to a row in its respective export file (`seasons.parquet`, `grades.parquet`, `majors.parquet`).
- **SC-006**: Practice log export with incremental mode runs measurably faster than a full scan when fewer than 10% of rows have changed since the last export.
- **SC-007**: No production table is locked or experiences write failures attributable to the export process during its run.
- **SC-008**: The analytics server can filter all metrics by `season`, `grade`, `major`, and `plan` using the exported academic context files without requiring any additional production data access.

## Assumptions

- The Interaction Log is already exported via the existing archive pipeline (`interaction_log.v1.yaml`) and is excluded from this feature's scope.
- `tabMemora Review Item` contains the fields `item_id`, `lesson`, `topic`, `unit`, `track`, `subject` as the canonical item-to-curriculum mapping source — no additional join table is needed.
- The content hierarchy tables (`tabMemora Subject`, `tabMemora Track`, `tabMemora Unit`, `tabMemora Topic`, `tabMemora Lesson`) are small enough (thousands of rows) that a full snapshot export on every run is acceptable without incremental tracking.
- `grade_majors.parquet` is sourced from `tabMemora Grade Major` (child table of `tabMemora Grade`), exported as a flat table with `grade` and `major` columns.
- The export destination (`analytics_exports/`) is an accessible directory on the production server (or a mounted share) from which files are subsequently transferred to the analytics server by the existing transfer mechanism from the analytics lakehouse pipeline (046).
- The practice log's composite primary key `(player_id, item_id)` guarantees no duplicate student-item rows in the source; the export must preserve this uniqueness guarantee.
- Incremental practice log export tracks the watermark via `last_seen_at` — all rows with `last_seen_at > previous_watermark` are included in the delta.
- Season `season_seq` is a stable integer identifier usable as a join key in the analytics server (not a Frappe `name` string).
