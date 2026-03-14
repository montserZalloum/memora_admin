# Data Model: Analytics Parquet Dataset Export

**Branch**: `048-analytics-parquet-export` | **Date**: 2026-03-13

## Overview

No new database tables are created. This feature reads from existing MariaDB tables and writes ~22 Parquet files across 18 datasets. The data model below describes:
1. Output Parquet schemas (the "analytical data model")
2. Source table mapping
3. Entity relationships across output files

---

## Dimension Datasets (5 datasets, 5 files)

### 1. `dim_player.parquet`

**Source**: `tabMemora Player Profile`
**Mode**: Full snapshot
**Primary Key**: `player_id`

| Column | Type | Source Column | Notes |
|--------|------|---------------|-------|
| `player_id` | `string` | `name` | PLAYER-XXXXX |
| `display_name` | `string` | `display_name` | |
| `grade_id` | `string` | `grade` | FK -> dim_academic_plan |
| `major_id` | `string` | `major` | FK -> dim_academic_plan |
| `season_id` | `string` | `season` | FK -> dim_season |
| `gender` | `string` | `gender` | Male/Female/NULL |
| `language` | `string` | `preferred_lang` | ar/en |
| `registered_at` | `timestamp[us]` | `creation` | Registration date |

**Excluded**: `mobile`, `password` (sensitive data — FR-002)

**DQ Rules**:
- No duplicate `player_id`
- No null `player_id`
- `min_rows: 1`

---

### 2. `dim_content_hierarchy.parquet`

**Source**: `tabMemora Lesson` JOIN Subject/Track/Unit/Topic + Lesson Stage subqueries
**Mode**: Full snapshot
**Primary Key**: `lesson_id`

| Column | Type | Source Column | Notes |
|--------|------|---------------|-------|
| `lesson_id` | `string` | `l.name` | PK |
| `lesson_title` | `string` | `l.lesson_title` | |
| `subject_id` | `string` | `l.subject` | FK -> dim_season via academic plans |
| `subject_title` | `string` | `sub.subject_title` | Denormalized |
| `track_id` | `string` | `l.track` | |
| `track_title` | `string` | `t.track_title` | Denormalized |
| `unit_id` | `string` | `l.unit` | |
| `unit_title` | `string` | `u.unit_title` | Denormalized |
| `topic_id` | `string` | `l.topic` | |
| `topic_title` | `string` | `tp.topic_title` | Denormalized |
| `base_xp` | `int64` | `l.base_xp` | |
| `max_hearts` | `int64` | `l.max_hearts` | |
| `is_reviewable` | `int64` | `l.is_reviewable` | 0/1 |
| `bit_index` | `int64` | `l.bit_index` | |
| `stage_count` | `int64` | Subquery COUNT(*) | Number of stages |
| `stage_types` | `string` | Subquery GROUP_CONCAT | Comma-separated |

**Filter**: `WHERE l.is_published = 1`

**DQ Rules**:
- No duplicate `lesson_id`
- No null `lesson_id`
- `min_rows: 1`

---

### 3. `dim_review_item.parquet`

**Source**: `tabMemora Review Item`
**Mode**: Full snapshot
**Primary Key**: `item_id`

| Column | Type | Source Column | Notes |
|--------|------|---------------|-------|
| `item_id` | `string` | `item_id` | UUID |
| `subject_id` | `string` | `subject` | FK |
| `topic_id` | `string` | `topic` | FK |
| `lesson_id` | `string` | `lesson` | FK -> dim_content_hierarchy |
| `stage_id` | `string` | `stage_id` | |
| `stage_type` | `string` | `stage_type` | |
| `question_text` | `string` | `question_text` | |
| `correct_choice` | `int64` | `correct_choice` | 1-4 |

**DQ Rules**:
- No duplicate `item_id`
- No null `item_id`

---

### 4. `dim_season.parquet`

**Source**: `tabMemora Season`
**Mode**: Full snapshot
**Primary Key**: `season_id`

| Column | Type | Source Column | Notes |
|--------|------|---------------|-------|
| `season_id` | `string` | `name` | PK |
| `season_title` | `string` | `season_title` | |
| `season_seq` | `int64` | `season_seq` | Integer ordering key |
| `start_date` | `date32` | `start_date` | |
| `end_date` | `date32` | `end_date` | |
| `is_published` | `int64` | `is_published` | 0/1 |

**DQ Rules**:
- No duplicate `season_id`
- No duplicate `season_seq`
- No null `season_id`, `season_seq`
- `min_rows: 1`

---

### 5. `dim_academic_plan.parquet`

**Source**: `tabMemora Academic Plan` JOIN Grade/Major + Plan Subject subquery
**Mode**: Full snapshot
**Primary Key**: `plan_id`

| Column | Type | Source Column | Notes |
|--------|------|---------------|-------|
| `plan_id` | `string` | `ap.name` | PK |
| `plan_name` | `string` | `ap.plan_name` | |
| `grade_id` | `string` | `ap.grade` | FK |
| `grade_title` | `string` | `g.grade_title` | Denormalized |
| `major_id` | `string` | `ap.major` | FK |
| `major_title` | `string` | `m.major_title` | Denormalized |
| `season_id` | `string` | `ap.season` | FK -> dim_season |
| `is_published` | `int64` | `ap.is_published` | 0/1 |
| `total_subjects` | `int64` | `ap.total_subjects` | |
| `total_lessons` | `int64` | `ap.total_lessons` | |
| `subject_list` | `string` | Subquery GROUP_CONCAT | Comma-separated |

**DQ Rules**:
- No duplicate `plan_id`
- No null `plan_id`

---

## Core Fact Datasets (6 datasets, 8 files)

### 6. `fact_interaction.parquet`

**Source**: `tabMemora Interaction Log`
**Mode**: Date-range filtered snapshot
**Primary Key**: `event_id`

| Column | Type | Source Column | Notes |
|--------|------|---------------|-------|
| `event_id` | `string` | `name` | LOG-XXXXX |
| `player_id` | `string` | `player` | FK -> dim_player |
| `lesson_id` | `string` | `lesson` | FK -> dim_content_hierarchy |
| `stage_id` | `string` | `stage_id` | |
| `item_id` | `string` | `item_id` | FK -> dim_review_item (nullable) |
| `event_type` | `string` | `event_type` | Started/Completed |
| `time_spent_sec` | `int64` | `time_spent` | Seconds |
| `errors_count` | `int64` | `errors_count` | |
| `event_ts` | `timestamp[us]` | `timestamp` | DATETIME(6) |
| `client_metadata` | `string` | `client_metadata` | JSON as string |

**Filter**: `WHERE timestamp BETWEEN %s AND %s`

**DQ Rules**:
- No duplicate `event_id`
- No null `event_id`, `player_id`

---

### 7. `fact_memory_state.parquet`

**Source**: `tabMemora Memory State`
**Mode**: Full snapshot (all seasons)
**Primary Key**: `ms_id`

| Column | Type | Source Column | Notes |
|--------|------|---------------|-------|
| `ms_id` | `int64` | `name` | BIGINT PK |
| `player_id` | `string` | `player` | FK -> dim_player |
| `item_id` | `string` | `BIN_TO_UUID(item_id)` | Binary -> UUID text in SQL |
| `season_seq` | `int64` | `season_seq` | Partition key |
| `subject_id` | `string` | `subject` | FK |
| `lesson_id` | `string` | `lesson` | FK |
| `stability` | `float64` | `CAST(stability AS DOUBLE)` | DECIMAL -> float in SQL |
| `difficulty` | `float64` | `CAST(difficulty AS DOUBLE)` | DECIMAL -> float in SQL |
| `next_review` | `date32` | `next_review` | |
| `last_review` | `timestamp[us]` | `last_review` | |
| `fsrs_state` | `int64` | `state` | 0=New,1=Learning,2=Review,3=Relearning |
| `fsrs_step` | `int64` | `step` | |

**DQ Rules**:
- No duplicate `ms_id`
- No null `ms_id`, `player_id`, `item_id`

---

### 8. `fact_practice.parquet`

**Source**: `tabMemora Practice Log` (custom non-Frappe table)
**Mode**: Incremental watermark (same as 047)
**Primary Key**: `(player_id, item_id)`

| Column | Type | Source Column | Notes |
|--------|------|---------------|-------|
| `player_id` | `string` | `player_id` | Composite PK part 1 |
| `item_id` | `string` | `item_id` | Composite PK part 2 |
| `first_seen_at` | `timestamp[us]` | `first_seen_at` | |
| `last_seen_at` | `timestamp[us]` | `last_seen_at` | Watermark column |
| `last_result` | `string` | `last_result` | Correct/Incorrect |
| `attempt_count` | `int64` | `attempt_count` | |
| `correct_count` | `int64` | `correct_count` | |

**DQ Rules**:
- No duplicate `(player_id, item_id)`
- No null `player_id`, `item_id`
- `min_value: attempt_count >= 0`
- `min_value: correct_count >= 0`

---

### 9. `fact_subscription.parquet`

**Source**: `tabMemora Player Subscription` LEFT JOIN `tabMemora Subscription Transaction`
**Mode**: Full snapshot
**Primary Key**: `(player_id, access_key)`

| Column | Type | Source Column | Notes |
|--------|------|---------------|-------|
| `player_id` | `string` | `ps.player` | FK -> dim_player |
| `access_key` | `string` | `ps.access_key` | SUB-SUBJ-XXX or TRK-Track-XXX |
| `is_active` | `int64` | `ps.is_active` | 0/1 |
| `expires_at` | `date32` | `ps.expires_at` | |
| `subscribed_at` | `timestamp[us]` | `ps.creation` | |
| `payment_method` | `string` | `st.payment_method` | Nullable |
| `amount_paid` | `float64` | `st.amount_paid` | Nullable; DECIMAL -> float |
| `txn_status` | `string` | `st.status` | Nullable |

**DQ Rules**:
- No null `player_id`, `access_key`

---

### 10. `fact_voucher.parquet`

**Source**: `tabMemora Voucher Card` JOIN Batch + LEFT JOIN Allocation
**Mode**: Full snapshot
**Primary Key**: `serial_no`

| Column | Type | Source Column | Notes |
|--------|------|---------------|-------|
| `serial_no` | `string` | `vc.serial_no` | PK |
| `batch_id` | `string` | `vc.batch` | FK |
| `batch_name` | `string` | `vb.batch_name` | Denormalized |
| `batch_purpose` | `string` | `vb.batch_purpose` | |
| `face_value` | `float64` | `vb.face_value` | DECIMAL -> float |
| `card_status` | `string` | `vc.status` | State machine value |
| `library` | `string` | `vc.library` | B2B customer |
| `sale_model` | `string` | `vc.sale_model` | Prepaid/Consignment |
| `redeemed_by` | `string` | `vc.redeemed_by` | Nullable |
| `redeemed_at` | `timestamp[us]` | `vc.redeemed_at` | Nullable |
| `allocation_date` | `date32` | `va.allocation_date` | Nullable |
| `allocated_to` | `string` | `va.customer` | Nullable |

**DQ Rules**:
- No duplicate `serial_no`
- No null `serial_no`, `batch_id`
- `min_rows: 1`

---

### 11a. `fact_challenge_attempt.parquet`

**Source**: `tabMemora Challenge Attempt`
**Mode**: Full snapshot
**Primary Key**: `attempt_id`

| Column | Type | Source Column | Notes |
|--------|------|---------------|-------|
| `attempt_id` | `string` | `name` | PK |
| `player_id` | `string` | `player` | FK -> dim_player |
| `topic_id` | `string` | `topic` | FK |
| `subject_id` | `string` | `subject` | FK |
| `season_id` | `string` | `season` | FK -> dim_season |
| `attempt_number` | `int64` | `attempt_number` | |
| `total_questions` | `int64` | `total_questions` | |
| `correct_count` | `int64` | `correct_count` | |
| `score_pct` | `float64` | `score_pct` | |
| `passed` | `int64` | `passed` | 0/1 |
| `time_spent_sec` | `int64` | `time_spent` | Seconds |
| `xp_earned` | `int64` | `xp_earned` | |
| `submitted_at` | `timestamp[us]` | `submitted_at` | |

### 11b. `fact_challenge_detail.parquet`

**Source**: `tabMemora Challenge Attempt Detail`
**Mode**: Full snapshot
**Primary Key**: `(attempt_id, item_id)`

| Column | Type | Source Column | Notes |
|--------|------|---------------|-------|
| `attempt_id` | `string` | `parent` | FK -> fact_challenge_attempt |
| `item_id` | `string` | `item_id` | FK -> dim_review_item |
| `is_correct` | `int64` | `correct` | 0/1 |
| `time_spent_sec` | `int64` | `time_spent` | Seconds |
| `chosen_answer` | `int64` | `chosen_answer` | 1-4 |

**DQ Rules (both files)**:
- No duplicate `attempt_id` in attempt file
- No null `attempt_id`, `player_id` in attempt file
- No null `attempt_id`, `item_id` in detail file

---

## Supplementary Datasets (7 datasets, 9 files)

### 12. `fact_structure_progress.parquet`

**Source**: `tabMemora Structure Progress`
**Mode**: Full snapshot
**Primary Key**: `(player_id, subject_id)`

| Column | Type | Source Column | Notes |
|--------|------|---------------|-------|
| `player_id` | `string` | `player` | FK -> dim_player |
| `subject_id` | `string` | `subject` | FK |
| `completion_pct` | `float64` | `completion_percentage` | DECIMAL -> float |
| `passed_lessons_bitset` | `string` | `passed_lessons_bitset` | LONGTEXT |

**DQ Rules**:
- No null `player_id`, `subject_id`

---

### 13. `fact_player_wallet.parquet`

**Source**: `tabMemora Player Wallet`
**Mode**: Full snapshot
**Primary Key**: `player_id`

| Column | Type | Source Column | Notes |
|--------|------|---------------|-------|
| `player_id` | `string` | `player` | FK -> dim_player |
| `total_xp` | `int64` | `total_xp` | |
| `total_lessons` | `int64` | `total_lessons` | |
| `total_time_min` | `int64` | `total_time_min` | Minutes |
| `current_streak` | `int64` | `current_streak` | |
| `daily_xp_json` | `string` | `daily_xp_json` | JSON as string |
| `last_sync_at` | `timestamp[us]` | `last_sync_at` | |

**DQ Rules**:
- No duplicate `player_id`
- No null `player_id`

---

### 14. `dim_lesson_stage.parquet`

**Source**: `tabMemora Lesson Stage` LEFT JOIN `tabMemora Lesson Stage Settings`
**Mode**: Full snapshot
**Primary Key**: `stage_id`

| Column | Type | Source Column | Notes |
|--------|------|---------------|-------|
| `stage_id` | `string` | `ls.stage_id` | PK |
| `lesson_id` | `string` | `ls.parent` | FK -> dim_content_hierarchy |
| `stage_type` | `string` | `ls.stage_type` | |
| `is_skippable` | `int64` | `ls.is_skippable` | 0/1 |
| `default_stage_time` | `int64` | `lss.default_stage_time` | Seconds; nullable |
| `is_time_calculated` | `int64` | `lss.is_time_calculated` | 0/1; nullable |

**DQ Rules**:
- No duplicate `stage_id`
- No null `stage_id`, `lesson_id`

---

### 15. `fact_content_report.parquet`

**Source**: `tabMemora Content Report`
**Mode**: Full snapshot
**Primary Key**: Frappe `name` (implicit, not exported — use row uniqueness)

| Column | Type | Source Column | Notes |
|--------|------|---------------|-------|
| `player_id` | `string` | `player` | FK -> dim_player |
| `subject_id` | `string` | `subject` | FK |
| `lesson_id` | `string` | `lesson` | FK |
| `report_type` | `string` | `report_type` | |
| `description` | `string` | `description` | |
| `status` | `string` | `status` | |
| `created_at` | `timestamp[us]` | `creation` | |
| `resolved_at` | `timestamp[us]` | `modified` | |

**DQ Rules**:
- No null `player_id`

---

### 16a. `fact_live_challenge_event.parquet`

**Source**: `tabMemora Live Challenge Event`
**Mode**: Full snapshot
**Primary Key**: `event_id`

| Column | Type | Source Column | Notes |
|--------|------|---------------|-------|
| `event_id` | `string` | `lce.name` | PK |
| `event_name` | `string` | `lce.event_name` | |
| `status` | `string` | `lce.status` | |
| `scheduled_start` | `timestamp[us]` | `lce.scheduled_start` | |
| `exam_duration` | `int64` | `lce.exam_duration` | Minutes |
| `capacity` | `int64` | `lce.capacity` | |
| `participant_count` | `int64` | `lce.participant_count` | |
| `submitted_count` | `int64` | `lce.submitted_count` | |
| `is_paid` | `int64` | `lce.is_paid` | 0/1 |

### 16b. `fact_live_challenge_participation.parquet`

**Source**: `tabMemora Live Challenge Participation`
**Mode**: Full snapshot
**Primary Key**: `(event_id, player_id)`

| Column | Type | Source Column | Notes |
|--------|------|---------------|-------|
| `event_id` | `string` | `lcp.event` | FK -> fact_live_challenge_event |
| `player_id` | `string` | `lcp.player` | FK -> dim_player |
| `joined_at` | `timestamp[us]` | `lcp.joined_at` | |
| `submitted_at` | `timestamp[us]` | `lcp.submitted_at` | Nullable |
| `score` | `int64` | `lcp.score` | |
| `rank` | `int64` | `lcp.rank` | |
| `xp_awarded` | `int64` | `lcp.xp_awarded` | |

**DQ Rules (both files)**:
- No duplicate `event_id` in event file
- No null `event_id` in both files

---

### 17. `fact_archive_job.parquet`

**Source**: `tabMemora Archive Job`
**Mode**: Full snapshot
**Primary Key**: `job_id`

| Column | Type | Source Column | Notes |
|--------|------|---------------|-------|
| `job_id` | `string` | `name` | PK |
| `source_doctype` | `string` | `source_doctype` | |
| `status` | `string` | `status` | State machine value |
| `archive_scope` | `string` | `archive_scope` | |
| `started_at` | `timestamp[us]` | `started_at` | Nullable |
| `completed_at` | `timestamp[us]` | `completed_at` | Nullable |
| `duration_seconds` | `int64` | `duration_seconds` | Nullable |
| `row_count` | `int64` | `row_count` | Nullable |
| `file_size_bytes` | `int64` | `file_size_bytes` | Nullable |
| `retry_count` | `int64` | `retry_count` | |
| `error_log` | `string` | `error_log` | Nullable |

**DQ Rules**:
- No duplicate `job_id`
- No null `job_id`

---

### 18a. `fact_task_run_log.parquet`

**Source**: `tabMemora Task Run Log`
**Mode**: Full snapshot
**Primary Key**: `(task_name, run_date)`

| Column | Type | Source Column | Notes |
|--------|------|---------------|-------|
| `task_name` | `string` | `task_name` | |
| `run_date` | `date32` | `run_date` | |
| `started_at` | `timestamp[us]` | `started_at` | |
| `completed_at` | `timestamp[us]` | `completed_at` | Nullable |
| `duration_sec` | `int64` | `duration_sec` | Nullable |
| `status` | `string` | `status` | |
| `triggered_by` | `string` | `triggered_by` | |
| `processed_count` | `int64` | `processed_count` | Nullable |
| `failed_count` | `int64` | `failed_count` | Nullable |
| `error_message` | `string` | `error_message` | Nullable |

### 18b. `fact_build_queue.parquet`

**Source**: `tabMemora Build Queue`
**Mode**: Full snapshot
**Primary Key**: Frappe `name` (implicit)

| Column | Type | Source Column | Notes |
|--------|------|---------------|-------|
| `target_type` | `string` | `target_type` | |
| `target_name` | `string` | `target_name` | |
| `status` | `string` | `status` | Pending/Processing/Completed/Failed |
| `started_at` | `timestamp[us]` | `started_at` | Nullable |
| `completed_at` | `timestamp[us]` | `completed_at` | Nullable |
| `duration_sec` | `int64` | `duration_sec` | Nullable |
| `files_generated` | `int64` | `files_generated` | Nullable |
| `trigger_reason` | `string` | `trigger_reason` | |

**DQ Rules (both files)**:
- No null `task_name` in task run log
- No null `target_type`, `target_name` in build queue

---

## Entity Relationship Diagram

```
dim_player.parquet
  player_id (PK) ◄──── fact_practice.player_id
                  ◄──── fact_interaction.player_id
                  ◄──── fact_memory_state.player_id
                  ◄──── fact_subscription.player_id
                  ◄──── fact_challenge_attempt.player_id
                  ◄──── fact_structure_progress.player_id
                  ◄──── fact_player_wallet.player_id
                  ◄──── fact_content_report.player_id
                  ◄──── fact_live_challenge_participation.player_id

dim_content_hierarchy.parquet
  lesson_id (PK) ◄──── fact_interaction.lesson_id
                 ◄──── fact_memory_state.lesson_id
                 ◄──── dim_lesson_stage.lesson_id

dim_review_item.parquet
  item_id (PK) ◄──── fact_practice.item_id
               ◄──── fact_interaction.item_id
               ◄──── fact_memory_state.item_id
               ◄──── fact_challenge_detail.item_id

dim_season.parquet
  season_id (PK) ◄──── dim_player.season_id
                 ◄──── dim_academic_plan.season_id
                 ◄──── fact_challenge_attempt.season_id

dim_academic_plan.parquet
  plan_id (PK)
  grade_id ◄──── dim_player.grade_id
  major_id ◄──── dim_player.major_id

fact_challenge_attempt.parquet
  attempt_id (PK) ◄──── fact_challenge_detail.attempt_id

fact_live_challenge_event.parquet
  event_id (PK) ◄──── fact_live_challenge_participation.event_id
```

---

## Source Table -> Output File Mapping

| Output File | Source Table(s) | SQL Mode | Filter |
|---|---|---|---|
| `dim_player.parquet` | tabMemora Player Profile | Direct SELECT | None |
| `dim_content_hierarchy.parquet` | Lesson + Subject + Track + Unit + Topic + Lesson Stage | JOINs + subqueries | `is_published = 1` |
| `dim_review_item.parquet` | tabMemora Review Item | Direct SELECT | None |
| `dim_season.parquet` | tabMemora Season | Direct SELECT | None |
| `dim_academic_plan.parquet` | Academic Plan + Grade + Major + Plan Subject | JOINs + subquery | None |
| `fact_interaction.parquet` | tabMemora Interaction Log | Direct SELECT | `timestamp BETWEEN` |
| `fact_memory_state.parquet` | tabMemora Memory State | Direct SELECT w/ casts | None (all seasons) |
| `fact_practice.parquet` | tabMemora Practice Log | Direct SELECT | Incremental watermark |
| `fact_subscription.parquet` | Player Subscription + Subscription Transaction | LEFT JOIN | None |
| `fact_voucher.parquet` | Voucher Card + Batch + Allocation | JOINs | None |
| `fact_challenge_attempt.parquet` | tabMemora Challenge Attempt | Direct SELECT | None |
| `fact_challenge_detail.parquet` | tabMemora Challenge Attempt Detail | Direct SELECT | None |
| `fact_structure_progress.parquet` | tabMemora Structure Progress | Direct SELECT | None |
| `fact_player_wallet.parquet` | tabMemora Player Wallet | Direct SELECT | None |
| `dim_lesson_stage.parquet` | Lesson Stage + Lesson Stage Settings | LEFT JOIN | None |
| `fact_content_report.parquet` | tabMemora Content Report | Direct SELECT | None |
| `fact_live_challenge_event.parquet` | tabMemora Live Challenge Event | Direct SELECT | None |
| `fact_live_challenge_participation.parquet` | tabMemora Live Challenge Participation | Direct SELECT | None |
| `fact_archive_job.parquet` | tabMemora Archive Job | Direct SELECT | None |
| `fact_task_run_log.parquet` | tabMemora Task Run Log | Direct SELECT | None |
| `fact_build_queue.parquet` | tabMemora Build Queue | Direct SELECT | None |

---

## Manifest Model

Each dataset produces a sidecar manifest file at `{output_dir}/{dataset_key}.manifest.json`:

```json
{
  "manifest_version": "1.0",
  "dataset_key": "<dataset_key>",
  "kind": "analytics",
  "schema_version": "1.0",
  "created_at": "2026-03-13T12:00:00Z",
  "source": "memora_admin",
  "files": [
    {
      "filename": "<file>.parquet",
      "row_count": 364,
      "checksum": "sha256:<hex_digest>",
      "size_bytes": 12345
    }
  ]
}
```

Multi-file datasets (fact_challenge, fact_live_challenge, fact_task_run) include multiple entries in the `files` array.
