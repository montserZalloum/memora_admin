# Data Model: Educational Analytics Dataset Export

**Branch**: `047-analytics-dataset-export` | **Date**: 2026-03-13

## Overview

No new database tables are created. This feature reads from existing MariaDB tables and writes 12 Parquet files. The data model below describes:
1. Output Parquet schemas (the "analytical data model")
2. Source table mapping
3. Entity relationships across output files

---

## Output Parquet Schemas

### 1. `practice_log.parquet`

**Source**: `tabMemora Practice Log` (direct)
**Mode**: Incremental watermark (full snapshot on first run; delta merge on subsequent runs)
**Primary Key**: `(player_id, item_id)`

| Column | Type | Source Column | Notes |
|--------|------|---------------|-------|
| `player_id` | `string` | `player_id` | FK → not exported in this file; joins to player profiles if needed |
| `item_id` | `string` | `item_id` | FK → `item_mapping.parquet.item_id` |
| `attempt_count` | `int64` | `attempt_count` | Total review attempts |
| `correct_count` | `int64` | `correct_count` | Correct attempts; 0 is valid |
| `first_seen_at` | `timestamp[us]` | `first_seen_at` | First review timestamp |
| `last_seen_at` | `timestamp[us]` | `last_seen_at` | Last review timestamp; watermark column |
| `last_result` | `string` | `last_result` | `'Correct'` or `'Incorrect'`; 0-value rows included |

**DQ Rules**:
- No duplicate `(player_id, item_id)` rows
- No null `player_id` or `item_id`
- No negative `attempt_count` or `correct_count`
- `correct_count <= attempt_count`

---

### 2. `item_mapping.parquet`

**Source**: `tabMemora Review Item` (direct, filtered)
**Mode**: Full snapshot
**Primary Key**: `item_id`

| Column | Type | Source Column | Notes |
|--------|------|---------------|-------|
| `item_id` | `string` | `item_id` | PK |
| `lesson_id` | `string` | `lesson` | FK → `lessons.parquet.id` |
| `topic_id` | `string` | `topic` | FK → `topics.parquet.id` |
| `unit_id` | `string` | `unit` | FK → `units.parquet.id` |
| `track_id` | `string` | `track` | FK → `tracks.parquet.id` |
| `subject_id` | `string` | `subject` | FK → `subjects.parquet.id` |

**Filter**: Items with null/empty `lesson`, `topic`, `unit`, `track`, or `subject` are excluded (FR-007).

**DQ Rules**:
- No duplicate `item_id`
- No null values in any column (enforced by filter)

---

### 3. `subjects.parquet`

**Source**: `tabMemora Subject`
**Mode**: Full snapshot
**Primary Key**: `id`

| Column | Type | Source Column | Notes |
|--------|------|---------------|-------|
| `id` | `string` | `name` | Frappe PK |
| `name` | `string` | `subject_title` | Display name |

**Includes**: Published and unpublished (FR-012).

**DQ Rules**:
- No duplicate `id`
- No null `id` or `name`
- `min_rows: 1`

---

### 4. `tracks.parquet`

**Source**: `tabMemora Track`
**Mode**: Full snapshot
**Primary Key**: `id`

| Column | Type | Source Column | Notes |
|--------|------|---------------|-------|
| `id` | `string` | `name` | Frappe PK |
| `name` | `string` | `track_title` | Display name |
| `subject_id` | `string` | `subject` | FK → `subjects.parquet.id` |

**DQ Rules**:
- No duplicate `id`
- No null `id`, `name`, or `subject_id`

---

### 5. `units.parquet`

**Source**: `tabMemora Unit`
**Mode**: Full snapshot
**Primary Key**: `id`

| Column | Type | Source Column | Notes |
|--------|------|---------------|-------|
| `id` | `string` | `name` | Frappe PK |
| `name` | `string` | `unit_title` | Display name |
| `track_id` | `string` | `track` | FK → `tracks.parquet.id` |
| `subject_id` | `string` | `subject` | Denormalized from Unit DocType (read_only field) |

**DQ Rules**:
- No duplicate `id`
- No null `id`, `name`, or `track_id`

---

### 6. `topics.parquet`

**Source**: `tabMemora Topic`
**Mode**: Full snapshot
**Primary Key**: `id`

| Column | Type | Source Column | Notes |
|--------|------|---------------|-------|
| `id` | `string` | `name` | Frappe PK |
| `name` | `string` | `topic_title` | Display name |
| `unit_id` | `string` | `unit` | FK → `units.parquet.id` |
| `track_id` | `string` | `track` | Denormalized from Topic DocType |
| `subject_id` | `string` | `subject` | Denormalized from Topic DocType |

**DQ Rules**:
- No duplicate `id`
- No null `id`, `name`, or `unit_id`

---

### 7. `lessons.parquet`

**Source**: `tabMemora Lesson`
**Mode**: Full snapshot
**Primary Key**: `id`

| Column | Type | Source Column | Notes |
|--------|------|---------------|-------|
| `id` | `string` | `name` | Frappe PK |
| `name` | `string` | `lesson_title` | Display name |
| `topic_id` | `string` | `topic` | FK → `topics.parquet.id` |
| `unit_id` | `string` | `unit` | Denormalized from Lesson DocType |
| `track_id` | `string` | `track` | Denormalized from Lesson DocType |
| `subject_id` | `string` | `subject` | Denormalized from Lesson DocType |

**Includes**: Published and unpublished (FR-012).

**DQ Rules**:
- No duplicate `id`
- No null `id`, `name`, or `topic_id`

---

### 8. `seasons.parquet`

**Source**: `tabMemora Season`
**Mode**: Full snapshot
**Primary Key**: `id`

| Column | Type | Source Column | Notes |
|--------|------|---------------|-------|
| `id` | `string` | `name` | Frappe PK |
| `name` | `string` | `season_title` | Display name |
| `season_seq` | `int64` | `season_seq` | Stable integer ordering key (join key for analytics) |
| `start_date` | `date32` | `start_date` | Season start |
| `end_date` | `date32` | `end_date` | Season end |

**DQ Rules**:
- No duplicate `id`
- No duplicate `season_seq`
- No null `id`, `name`, or `season_seq`
- `min_rows: 1`

---

### 9. `grades.parquet`

**Source**: `tabMemora Grade`
**Mode**: Full snapshot
**Primary Key**: `id`

| Column | Type | Source Column | Notes |
|--------|------|---------------|-------|
| `id` | `string` | `name` | Frappe PK |
| `name` | `string` | `grade_title` | Display name |
| `sort_order` | `int64` | `sort_order` | Display ordering |

**DQ Rules**:
- No duplicate `id`
- No null `id` or `name`
- `min_rows: 1`

---

### 10. `majors.parquet`

**Source**: `tabMemora Major`
**Mode**: Full snapshot
**Primary Key**: `id`

| Column | Type | Source Column | Notes |
|--------|------|---------------|-------|
| `id` | `string` | `name` | Frappe PK |
| `name` | `string` | `major_title` | Display name |

**DQ Rules**:
- No duplicate `id`
- No null `id` or `name`
- `min_rows: 1`

---

### 11. `academic_plans.parquet`

**Source**: `tabMemora Academic Plan`
**Mode**: Full snapshot
**Primary Key**: `id`

| Column | Type | Source Column | Notes |
|--------|------|---------------|-------|
| `id` | `string` | `name` | Frappe PK |
| `name` | `string` | `plan_name` | Display name |
| `season` | `string` | `season` | FK → `seasons.parquet.id` |
| `grade` | `string` | `grade` | FK → `grades.parquet.id` |
| `major` | `string` | `major` | FK → `majors.parquet.id` |
| `is_published` | `int64` | `is_published` | 0 or 1; drafts included (FR-019) |

**DQ Rules**:
- No duplicate `id`
- No null `id`, `name`, `season`, `grade`, or `major`

---

### 12. `grade_majors.parquet`

**Source**: `tabMemora Grade Major` (Frappe child table of Memora Grade)
**Mode**: Full snapshot
**Primary Key**: `(grade, major)`

| Column | Type | Source Column | Notes |
|--------|------|---------------|-------|
| `grade` | `string` | `parent` | FK → `grades.parquet.id` |
| `major` | `string` | `major` | FK → `majors.parquet.id` |

**Filter**: `WHERE parenttype = 'Memora Grade'`

**DQ Rules**:
- No duplicate `(grade, major)` pairs
- No null `grade` or `major`

---

## Entity Relationship Diagram

```
practice_log.parquet
  item_id ──────────────────────────► item_mapping.parquet
  player_id (not joined in exports)     item_id (PK)
                                        lesson_id ──► lessons.parquet.id
                                        topic_id  ──► topics.parquet.id
                                        unit_id   ──► units.parquet.id
                                        track_id  ──► tracks.parquet.id
                                        subject_id──► subjects.parquet.id

lessons.parquet                                        ▲
  topic_id ──► topics.parquet.id                       │ (denormalized)
                  unit_id ──► units.parquet.id ─────────┘
                                 track_id ──► tracks.parquet.id
                                                subject_id ──► subjects.parquet.id

academic_plans.parquet
  season ──► seasons.parquet.id
  grade  ──► grades.parquet.id
  major  ──► majors.parquet.id

grade_majors.parquet
  grade  ──► grades.parquet.id
  major  ──► majors.parquet.id
```

---

## Source Table → Output File Mapping

| Output File | Source Table | SQL Mode | Incremental? |
|---|---|---|---|
| `practice_log.parquet` | `tabMemora Practice Log` | Direct SELECT | ✅ Watermark on `last_seen_at` |
| `item_mapping.parquet` | `tabMemora Review Item` | Direct SELECT + WHERE filter | ❌ Full snapshot |
| `subjects.parquet` | `tabMemora Subject` | Direct SELECT | ❌ Full snapshot |
| `tracks.parquet` | `tabMemora Track` | Direct SELECT | ❌ Full snapshot |
| `units.parquet` | `tabMemora Unit` | Direct SELECT | ❌ Full snapshot |
| `topics.parquet` | `tabMemora Topic` | Direct SELECT | ❌ Full snapshot |
| `lessons.parquet` | `tabMemora Lesson` | Direct SELECT | ❌ Full snapshot |
| `seasons.parquet` | `tabMemora Season` | Direct SELECT | ❌ Full snapshot |
| `grades.parquet` | `tabMemora Grade` | Direct SELECT | ❌ Full snapshot |
| `majors.parquet` | `tabMemora Major` | Direct SELECT | ❌ Full snapshot |
| `academic_plans.parquet` | `tabMemora Academic Plan` | Direct SELECT | ❌ Full snapshot |
| `grade_majors.parquet` | `tabMemora Grade Major` | Direct SELECT + WHERE parenttype | ❌ Full snapshot |

---

## Watermark State Model

```
analytics_exports/.watermark.json
{
  "practice_log": {
    "last_watermark": "<ISO datetime — max last_seen_at from last merged output>",
    "last_export_at": "<ISO datetime — when export completed>",
    "last_row_count": <int — rows in merged output file>
  }
}
```

**State transitions**:
1. **No watermark file** → full mode: SELECT all rows → write `practice_log.parquet` → save watermark with `max(last_seen_at)`.
2. **Watermark exists** → incremental mode: SELECT rows WHERE `last_seen_at > last_watermark` → load existing Parquet → upsert delta (delta rows replace existing rows with same PK) → write merged Parquet → update watermark.
3. **Incremental delta is empty** → no merge needed; update `last_export_at` only.
4. **Export error** → watermark NOT updated; next run retries safely.

---

## SQL Queries (canonical)

```sql
-- practice_log (full mode)
SELECT `player_id`, `item_id`, `attempt_count`, `correct_count`,
       `first_seen_at`, `last_seen_at`, `last_result`
FROM `tabMemora Practice Log`
ORDER BY `last_seen_at`

-- practice_log (incremental mode)
SELECT `player_id`, `item_id`, `attempt_count`, `correct_count`,
       `first_seen_at`, `last_seen_at`, `last_result`
FROM `tabMemora Practice Log`
WHERE `last_seen_at` > %s
ORDER BY `last_seen_at`

-- item_mapping
SELECT `item_id`,
       `lesson`  AS lesson_id,
       `topic`   AS topic_id,
       `unit`    AS unit_id,
       `track`   AS track_id,
       `subject` AS subject_id
FROM `tabMemora Review Item`
WHERE `lesson`  IS NOT NULL AND `lesson`  != ''
  AND `topic`   IS NOT NULL AND `topic`   != ''
  AND `unit`    IS NOT NULL AND `unit`    != ''
  AND `track`   IS NOT NULL AND `track`   != ''
  AND `subject` IS NOT NULL AND `subject` != ''
ORDER BY `item_id`

-- subjects
SELECT `name` AS id, `subject_title` AS name FROM `tabMemora Subject` ORDER BY `name`

-- tracks
SELECT `name` AS id, `track_title` AS name, `subject` AS subject_id
FROM `tabMemora Track` ORDER BY `name`

-- units
SELECT `name` AS id, `unit_title` AS name, `track` AS track_id, `subject` AS subject_id
FROM `tabMemora Unit` ORDER BY `name`

-- topics
SELECT `name` AS id, `topic_title` AS name, `unit` AS unit_id,
       `track` AS track_id, `subject` AS subject_id
FROM `tabMemora Topic` ORDER BY `name`

-- lessons
SELECT `name` AS id, `lesson_title` AS name, `topic` AS topic_id,
       `unit` AS unit_id, `track` AS track_id, `subject` AS subject_id
FROM `tabMemora Lesson` ORDER BY `name`

-- seasons
SELECT `name` AS id, `season_title` AS name, `season_seq`, `start_date`, `end_date`
FROM `tabMemora Season` ORDER BY `season_seq`

-- grades
SELECT `name` AS id, `grade_title` AS name, `sort_order`
FROM `tabMemora Grade` ORDER BY `sort_order`

-- majors
SELECT `name` AS id, `major_title` AS name FROM `tabMemora Major` ORDER BY `name`

-- academic_plans
SELECT `name` AS id, `plan_name` AS name, `season`, `grade`, `major`, `is_published`
FROM `tabMemora Academic Plan` ORDER BY `name`

-- grade_majors
SELECT `parent` AS grade, `major`
FROM `tabMemora Grade Major`
WHERE `parenttype` = 'Memora Grade'
ORDER BY `parent`, `major`
```
