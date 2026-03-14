# Feature Specification: Analytics Parquet Dataset Export

**Feature Branch**: `048-analytics-parquet-export`
**Created**: 2026-03-13
**Status**: Draft
**Input**: User description: "Export 18 analytical datasets (5 dimension + 13 fact tables) as Parquet files with SHA-256 manifest for the analytics server, following the existing export infrastructure pattern"

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Export Dimension Datasets for Reference Lookups (Priority: P1)

As an analytics engineer, I want the five core dimension tables (players, content hierarchy, review items, seasons, academic plans) exported as Parquet files so that the analytics server has complete reference data to join against all fact tables when computing reports.

**Why this priority**: Every analytics report depends on dimension data for context. Without players, curriculum hierarchy, seasons, and plans, fact table data is just IDs with no meaning. This is the foundation all other reports build on.

**Independent Test**: Can be fully tested by triggering a dimension export and verifying five valid Parquet files are produced (dim_player, dim_content_hierarchy, dim_review_item, dim_season, dim_academic_plan), each with a manifest.json containing SHA-256 checksum and row count, and each containing the expected columns with no null primary keys.

**Acceptance Scenarios**:

1. **Given** player profiles exist in the system, **When** the dimension export runs, **Then** a `dim_player.parquet` file is produced containing player ID, display name, grade, major, season, gender, language, and registration date — with no sensitive fields (mobile, password) included.
2. **Given** published lessons exist with full curriculum assignments, **When** the dimension export runs, **Then** a `dim_content_hierarchy.parquet` file is produced with each row representing one lesson and its full path (subject, track, unit, topic) including titles at every level, plus stage count and stage type summary.
3. **Given** review items exist, **When** the dimension export runs, **Then** a `dim_review_item.parquet` file is produced with item ID, curriculum linkage (subject, topic, lesson, stage), question text, and correct choice.
4. **Given** seasons exist, **When** the dimension export runs, **Then** a `dim_season.parquet` file is produced with season ID, title, sequence number, start date, end date, and published status.
5. **Given** academic plans exist, **When** the dimension export runs, **Then** a `dim_academic_plan.parquet` file is produced with plan details, grade and major (with titles), season linkage, subject count, lesson count, and subject list.

---

### User Story 2 - Export Core Fact Datasets for Learning and Business Analytics (Priority: P1)

As an analytics engineer, I want the six core fact tables (interactions, memory state, practice log, subscriptions, vouchers, challenges) exported as Parquet files so the analytics server can compute learning performance metrics, spaced repetition analysis, and business/revenue reports.

**Why this priority**: These fact tables contain the primary event and transaction data that drives all analytics reports. Interactions are the largest and most critical (learning events), memory state powers retention analysis, and subscription/voucher data enables revenue reporting.

**Independent Test**: Can be fully tested by triggering a fact export and verifying six datasets are produced (fact_interaction, fact_memory_state, fact_practice, fact_subscription, fact_voucher, fact_challenge_attempt + fact_challenge_detail), each with correct schemas and valid manifest files.

**Acceptance Scenarios**:

1. **Given** interaction log entries exist, **When** the fact export runs with a date range, **Then** a `fact_interaction.parquet` file is produced containing event ID, player, lesson, stage, item, event type, time spent, errors, timestamp, and client metadata — filtered to the specified date range.
2. **Given** memory state records exist for a season, **When** the fact export runs for that season, **Then** a `fact_memory_state.parquet` file is produced with binary item IDs converted to readable UUID text, decimal stability/difficulty values converted to standard floating point, and all records scoped to the target season.
3. **Given** practice log records exist, **When** the fact export runs, **Then** a `fact_practice.parquet` file is produced with player-item pairs, timestamps, result, and attempt/correct counts.
4. **Given** player subscriptions exist with payment transactions, **When** the fact export runs, **Then** a `fact_subscription.parquet` file is produced joining subscription details with their payment method, amount, and transaction status.
5. **Given** voucher cards exist with batch and allocation data, **When** the fact export runs, **Then** a `fact_voucher.parquet` file is produced with card serial, batch details, status, redemption info, and allocation data.
6. **Given** challenge attempts exist with per-question details, **When** the fact export runs, **Then** two files are produced: `fact_challenge_attempt.parquet` (attempt-level summary with score, pass/fail, XP) and `fact_challenge_detail.parquet` (question-level results with correctness and chosen answer).

---

### User Story 3 - Export Supplementary Datasets for Specialized Reports (Priority: P2)

As an analytics engineer, I want seven additional datasets exported (structure progress, player wallet, lesson stages, content reports, live challenges, archive jobs, task runs) so the analytics server can power specialized reports: subject completion tracking, learning velocity, stage effectiveness, content quality, live events, and system health monitoring.

**Why this priority**: These datasets serve specific reports that are valuable but not foundational. The core dimension and fact exports (P1) must work first. These extend coverage to the full report catalog.

**Independent Test**: Can be fully tested by triggering the supplementary export and verifying seven datasets are produced with correct schemas, valid manifests, and non-null primary keys.

**Acceptance Scenarios**:

1. **Given** structure progress records exist, **When** the export runs, **Then** a `fact_structure_progress.parquet` file is produced with player, subject, completion percentage, and passed lessons bitset.
2. **Given** player wallet records exist, **When** the export runs, **Then** a `fact_player_wallet.parquet` file is produced with XP totals, lesson counts, time spent, streak data, daily XP breakdown, and last sync timestamp.
3. **Given** lesson stages exist with settings, **When** the export runs, **Then** a `dim_lesson_stage.parquet` file is produced with stage ID, lesson linkage, stage type, skippability, default time, and time calculation flag.
4. **Given** content reports exist, **When** the export runs, **Then** a `fact_content_report.parquet` file is produced with player, curriculum linkage, report type, description, status, and timestamps.
5. **Given** live challenge events exist with participations, **When** the export runs, **Then** two files are produced: `fact_live_challenge_event.parquet` (event-level details) and `fact_live_challenge_participation.parquet` (per-player results with score, rank, XP).
6. **Given** archive jobs exist, **When** the export runs, **Then** a `fact_archive_job.parquet` file is produced with job ID, source doctype, status, timing, row count, file size, retry count, and error log.
7. **Given** task run logs and build queue entries exist, **When** the export runs, **Then** two files are produced: `fact_task_run_log.parquet` (task execution history) and `fact_build_queue.parquet` (content build pipeline records).

---

### User Story 4 - Verify Export Integrity via Manifests (Priority: P1)

As an analytics engineer, I want every Parquet file accompanied by a manifest.json containing a SHA-256 checksum and row count so the analytics server can verify file integrity and detect corruption or incomplete transfers before ingesting data.

**Why this priority**: Without integrity verification, the analytics server has no way to detect corrupted or truncated files. A single corrupted file can produce silently wrong reports. The manifest is the trust boundary between production and analytics.

**Independent Test**: Can be fully tested by triggering any dataset export, reading the manifest.json, computing the SHA-256 of the Parquet file independently, and confirming it matches the manifest checksum and that the row count matches the actual Parquet row count.

**Acceptance Scenarios**:

1. **Given** any dataset export completes, **When** the manifest.json is read, **Then** it contains at minimum the SHA-256 checksum of the Parquet file and the row count.
2. **Given** a manifest.json exists for an export, **When** the SHA-256 of the Parquet file is computed independently, **Then** it matches the checksum in the manifest exactly.
3. **Given** a manifest.json exists for an export, **When** the Parquet file row count is read, **Then** it matches the row count in the manifest exactly.
4. **Given** a zero-row dataset is exported, **When** the manifest is generated, **Then** it reports row count of 0 and a valid SHA-256 checksum for the empty-but-schema-valid Parquet file.

---

### Edge Cases

- What happens when a dataset source table is empty (zero rows)? A valid Parquet file with correct schema and zero rows MUST still be produced, with a manifest reporting row count 0.
- What happens when memory state contains binary `item_id` values? They MUST be converted to human-readable UUID text before export — raw binary bytes must never appear in Parquet output.
- What happens when stability/difficulty values are high-precision decimals? They MUST be converted to standard floating-point representation suitable for analytics consumption.
- What happens when the memory state table is range-partitioned by season? Each season MUST be exportable independently to respect the partition structure and avoid cross-partition scans.
- What happens when an interaction log export spans a period where students are actively learning? The export MUST NOT acquire locks that block concurrent student activity on the production database.
- What happens when a multi-table join (e.g., voucher + batch + allocation) has missing related records? LEFT JOINs ensure the primary record is still exported with null values for unmatched related fields.
- What happens when the challenge dataset produces two output files but one fails? Both files for a multi-file dataset MUST succeed or both MUST be marked as failed — partial dataset exports are not acceptable.
- What happens when a dataset export is interrupted mid-write? No partial Parquet file should be left in the export directory. The manifest MUST only be written after the Parquet file is fully written and verified.

## Requirements *(mandatory)*

### Functional Requirements

**Dimension Dataset Exports (5 datasets)**

- **FR-001**: System MUST export player profiles as `dim_player.parquet` with fields: player ID, display name, grade, major, current season, gender, language, and registration date.
- **FR-002**: System MUST exclude sensitive player data (mobile numbers, passwords) from the player dimension export.
- **FR-003**: System MUST export a denormalized content hierarchy as `dim_content_hierarchy.parquet` where each row represents one published lesson with its full curriculum path (subject, track, unit, topic) including titles at every level, plus stage count and comma-separated stage types.
- **FR-004**: System MUST export review items as `dim_review_item.parquet` with fields: item ID, subject, topic, lesson, stage ID, stage type, question text, and correct choice.
- **FR-005**: System MUST export seasons as `dim_season.parquet` with fields: season ID, title, sequence number, start date, end date, and published status.
- **FR-006**: System MUST export academic plans as `dim_academic_plan.parquet` with fields: plan ID, plan name, grade (with title), major (with title), season, published status, total subjects, total lessons, and subject list.

**Core Fact Dataset Exports (6 datasets, 8 files)**

- **FR-007**: System MUST export interaction log events as `fact_interaction.parquet` filtered by a configurable date range, with fields: event ID, player, lesson, stage, item, event type, time spent (seconds), error count, event timestamp, and client metadata.
- **FR-008**: System MUST export memory state as `fact_memory_state.parquet` scoped to a target season, with fields: state ID, player, item ID (as UUID text), season sequence, subject, lesson, stability (floating point), difficulty (floating point), next review date, last review timestamp, FSRS state, and FSRS step.
- **FR-009**: System MUST convert binary item IDs in the memory state table to human-readable UUID text format before export.
- **FR-010**: System MUST convert high-precision decimal values (stability, difficulty) to standard floating-point representation before export.
- **FR-011**: System MUST export practice log summaries as `fact_practice.parquet` with fields: player ID, item ID, first seen timestamp, last seen timestamp, last result, attempt count, and correct count.
- **FR-012**: System MUST export subscriptions as `fact_subscription.parquet` by joining subscription records with their payment transactions, including: player, access key, active status, expiry date, subscription date, payment method, amount paid, and transaction status.
- **FR-013**: System MUST export voucher cards as `fact_voucher.parquet` by joining card, batch, and allocation records, including: serial number, batch details (ID, name, purpose, face value), card status, library, sale model, redemption info, and allocation details.
- **FR-014**: System MUST export challenge data as two files: `fact_challenge_attempt.parquet` (attempt ID, player, topic, subject, season, attempt number, question count, correct count, score percentage, pass/fail, time spent, XP earned, submission timestamp) and `fact_challenge_detail.parquet` (attempt ID, item ID, correctness, time spent, chosen answer).

**Supplementary Dataset Exports (7 datasets, 9 files)**

- **FR-015**: System MUST export structure progress as `fact_structure_progress.parquet` with fields: player, subject, completion percentage, and passed lessons bitset.
- **FR-016**: System MUST export player wallet summaries as `fact_player_wallet.parquet` with fields: player, total XP, total lessons, total time (minutes), current streak, daily XP breakdown, and last sync timestamp.
- **FR-017**: System MUST export lesson stages as `dim_lesson_stage.parquet` by joining stage records with their settings, including: stage ID, lesson, stage type, skippability, default stage time, and time calculation flag.
- **FR-018**: System MUST export content reports as `fact_content_report.parquet` with fields: player, subject, lesson, report type, description, status, created-at timestamp, and resolved-at timestamp.
- **FR-019**: System MUST export live challenge data as two files: `fact_live_challenge_event.parquet` (event details, capacity, participant counts, paid status) and `fact_live_challenge_participation.parquet` (event, player, join/submit timestamps, score, rank, XP awarded).
- **FR-020**: System MUST export archive job records as `fact_archive_job.parquet` with fields: job ID, source doctype, status, archive scope, timing, row count, file size, retry count, and error log.
- **FR-021**: System MUST export task run data as two files: `fact_task_run_log.parquet` (task name, run date, timing, status, trigger, processed/failed counts, error message) and `fact_build_queue.parquet` (target type/name, status, timing, files generated, trigger reason).

**Export Integrity & Delivery**

- **FR-022**: Every exported Parquet file MUST be accompanied by a `manifest.json` containing at minimum the SHA-256 checksum of the file and the row count.
- **FR-023**: The manifest MUST only be written after the Parquet file is fully written and verified — no partial files with manifests.
- **FR-024**: All exports MUST follow the same infrastructure pattern as existing exports (same directory structure, manifest format, and delivery mechanism).
- **FR-025**: Exports MUST NOT acquire table locks or use queries that block concurrent production writes.
- **FR-026**: Multi-file datasets (challenge, live challenge, task run) MUST either succeed completely or fail completely — no partial dataset exports.
- **FR-027**: All new datasets MUST be integrated into the existing scheduled export job.

### Key Entities

- **Dimension Dataset**: A reference/lookup table exported as a single Parquet file. Contains slowly-changing descriptive attributes (player profiles, curriculum hierarchy, seasons). Exported as full snapshots.
- **Fact Dataset**: An event or transaction table exported as one or more Parquet files. Contains measures and foreign keys to dimension tables. May support date-range or season-scoped filtering for large tables.
- **Export Manifest**: A JSON sidecar file accompanying each Parquet file. Contains SHA-256 checksum and row count for integrity verification by the analytics server before ingestion.
- **Dataset Key**: A stable string identifier for each dataset (e.g., `dim_player`, `fact_interaction`) used for scheduling, logging, and manifest metadata.
- **Content Hierarchy**: A denormalized view of the curriculum structure: Subject > Track > Unit > Topic > Lesson > Stage. Exported as a flat table with IDs and titles at every level.
- **Multi-file Dataset**: A dataset that produces two related Parquet files (e.g., challenge attempts + details). Both files share the same dataset key and must be exported atomically.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: All 18 datasets are exported successfully, producing ~22 Parquet files, each with a valid manifest.json — verified by the analytics server accepting all files without integrity errors.
- **SC-002**: Every exported Parquet file's SHA-256 checksum matches its manifest value with 100% accuracy across all export runs.
- **SC-003**: Row counts in manifest files match actual Parquet file row counts for every dataset on every export run.
- **SC-004**: The analytics server can join any fact table to its corresponding dimension tables using exported foreign keys, with zero unresolvable references for active records.
- **SC-005**: Memory state exports contain only human-readable UUID text for item IDs and standard floating-point values for stability/difficulty — no binary blobs or precision-loss artifacts.
- **SC-006**: No production database write operations are blocked or delayed during dataset exports — verified by zero lock-wait timeouts attributable to export queries.
- **SC-007**: All 18 datasets run on the existing export schedule without manual intervention after initial setup.
- **SC-008**: Multi-file datasets (challenge, live challenge, task run) never leave partial outputs — either all files for a dataset exist with valid manifests, or none do.

## Assumptions

- An existing export infrastructure is already in place on the production server (directory structure, manifest format, cron scheduling, file transfer mechanism). All new datasets follow this pattern exactly.
- The interaction log may already be partially handled by the existing archive pipeline; the new exporter covers it as a dataset alongside the others.
- Dimension tables (curriculum, seasons, plans) are small enough (hundreds to low thousands of rows) that full-snapshot exports on every run are acceptable.
- Large fact tables (interaction log, memory state) support scoping by date range or season to manage export size and duration.
- The practice log table is a custom non-Frappe table with a composite primary key `(player_id, item_id)` and no `name` column — the exporter must handle this schema.
- The memory state table is range-partitioned by `season_seq` — season-scoped exports respect partition boundaries.
- The analytics server is responsible for data transformation, aggregation, and report generation. The production server's role is limited to exporting clean, verified data files.
- File transfer from the production server to the analytics server is handled by an existing mechanism (not in scope for this feature).
- Sensitive data (mobile numbers, passwords) is never exported. Only the fields specified per dataset are included.
