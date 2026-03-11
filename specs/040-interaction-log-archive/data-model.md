# Data Model: Interaction Log Archiving

**Feature Branch**: `040-interaction-log-archive`
**Date**: 2026-03-11

## Source Entity: Interaction Log Record

**Table**: `tabMemora Interaction Log` (Frappe DocType)

| Column | Type | Nullable | Notes |
|--------|------|----------|-------|
| `name` | VARCHAR(140) | NO | PK, auto-named `LOG-#####.`, deduplication key on analytics |
| `player` | VARCHAR(140) | NO | FK → `tabMemora Player Profile`.name |
| `lesson` | VARCHAR(140) | NO | FK → `tabMemora Lesson`.name |
| `stage_id` | VARCHAR(140) | NO | Stage identifier within the lesson |
| `item_id` | VARCHAR(140) | YES | Optional review item reference |
| `event_type` | ENUM('Started','Completed','Failed','Skipped') | NO | Interaction outcome |
| `time_spent` | INT | YES | Seconds spent (default 0) |
| `errors_count` | INT | YES | Error count (default 0) |
| `timestamp` | DATETIME | NO | Event timestamp — **scope column** for archiving |
| `client_metadata` | LONGTEXT (JSON) | YES | Browser/device info |
| `creation` | DATETIME | NO | Frappe auto |
| `modified` | DATETIME | NO | Frappe auto |
| `owner` | VARCHAR(140) | NO | Frappe auto |

**Required index**: `idx_timestamp` on `timestamp` for efficient date-range scans.

---

## Archive Type Schema: `interaction_log.v1.yaml`

New file at `archive_schemas/archive_types/interaction_log.v1.yaml`.

### Fact Columns (exported to Parquet)

| Column | Source | Type |
|--------|--------|------|
| `name` | `il.name` | VARCHAR(140) |
| `player` | `il.player` | VARCHAR(140) |
| `lesson` | `il.lesson` | VARCHAR(140) |
| `stage_id` | `il.stage_id` | VARCHAR(140) |
| `item_id` | `il.item_id` | VARCHAR(140) |
| `event_type` | `il.event_type` | VARCHAR(20) |
| `time_spent` | `il.time_spent` | INT |
| `errors_count` | `il.errors_count` | INT |
| `timestamp` | `il.timestamp` | DATETIME |
| `season_id` | `pp.season` (derived via player JOIN) | VARCHAR(140) |
| `plan_id` | `pp.plan` (derived via player JOIN) | VARCHAR(140) |

### Scope Configuration

- **scope_column**: `timestamp`
- **filter_column**: `timestamp`
- **retention_window**: 14 days (configurable to 7)

### Fact SQL

```sql
-- filtered (for archive jobs with date range)
SELECT
  il.`name`, il.`player`, il.`lesson`, il.`stage_id`, il.`item_id`,
  il.`event_type`, il.`time_spent`, il.`errors_count`, il.`timestamp`,
  pp.`season` AS season_id, pp.`plan` AS plan_id
FROM `tabMemora Interaction Log` il
LEFT JOIN `tabMemora Player Profile` pp ON il.`player` = pp.`name`
WHERE il.`{filter_column}` >= %s AND il.`{filter_column}` < %s
ORDER BY il.`{filter_column}`

-- full_snapshot (for live sync, if added later)
SELECT
  il.`name`, il.`player`, il.`lesson`, il.`stage_id`, il.`item_id`,
  il.`event_type`, il.`time_spent`, il.`errors_count`, il.`timestamp`,
  pp.`season` AS season_id, pp.`plan` AS plan_id
FROM `tabMemora Interaction Log` il
LEFT JOIN `tabMemora Player Profile` pp ON il.`player` = pp.`name`
```

### Dimensions

| Entity | Schema Version | Join Column | Scope Source |
|--------|---------------|-------------|--------------|
| player | v3 | player | direct |
| lesson | v1 | lesson | direct |
| season | v1 | — | derived (from player) |
| plan | v1 | — | derived (from player) |

### Export Metadata (injected columns)

Same as Practice Log: `archive_scope`, `archive_job_id`, `schema_version`, `exported_at`.

---

## New Dimension: Lesson (`lesson.v1.yaml`)

New file at `archive_schemas/dimensions/lesson.v1.yaml`.

| Field | Source | Type |
|-------|--------|------|
| `lesson_id` | `l.name` | VARCHAR(140) |
| `lesson_title` | `l.lesson_title` | VARCHAR(140) |
| `topic` | `l.topic` | VARCHAR(140) |
| `topic_title` | `t.topic_title` | VARCHAR(140) |
| `subject` | `l.subject` | VARCHAR(140) |
| `track` | `l.track` | VARCHAR(140) |
| `unit` | `l.unit` | VARCHAR(140) |
| `base_xp` | `l.base_xp` | INT |
| `is_published` | `l.is_published` | TINYINT |
| `is_reviewable` | `l.is_reviewable` | TINYINT |

### Dimension SQL

```sql
SELECT
  l.`name` AS lesson_id,
  l.`lesson_title`,
  l.`topic`,
  t.`topic_title`,
  l.`subject`,
  l.`track`,
  l.`unit`,
  l.`base_xp`,
  l.`is_published`,
  l.`is_reviewable`
FROM `tabMemora Lesson` l
LEFT JOIN `tabMemora Topic` t ON l.`topic` = t.`name`
WHERE l.`name` IN ({placeholders})
```

---

## Archive Job (`tabMemora Archive Job`)

Reuses the existing DocType. Job creation for Interaction Log:

| Field | Value |
|-------|-------|
| `source_doctype` | `Memora Interaction Log` |
| `archive_type` | `interaction_log` |
| `archive_scope` | Date string, e.g. `2026-02-25` |
| `schema_version` | `v1` |
| `status` | `Pending` |
| `post_archive_action` | `Delete` |
| `job_meta` | JSON (see below) |

### job_meta Structure

```json
{
  "query_filter": {
    "date_from": "2026-02-25",
    "date_to": "2026-02-26",
    "filter_column": "timestamp"
  },
  "export_columns": [
    "name", "player", "lesson", "stage_id", "item_id",
    "event_type", "time_spent", "errors_count", "timestamp"
  ],
  "schema_snapshot": {
    "columns": [
      {"name": "name", "type": "VARCHAR(140)"},
      {"name": "player", "type": "VARCHAR(140)"},
      {"name": "lesson", "type": "VARCHAR(140)"},
      {"name": "stage_id", "type": "VARCHAR(140)"},
      {"name": "item_id", "type": "VARCHAR(140)"},
      {"name": "event_type", "type": "VARCHAR(20)"},
      {"name": "time_spent", "type": "INT"},
      {"name": "errors_count", "type": "INT"},
      {"name": "timestamp", "type": "DATETIME"},
      {"name": "season_id", "type": "VARCHAR(140)"},
      {"name": "plan_id", "type": "VARCHAR(140)"}
    ],
    "primary_key": ["name"]
  },
  "related_tables": [
    {
      "entity": "player",
      "schema_version": "v3",
      "join_column": "player",
      "fact_column": "player"
    },
    {
      "entity": "lesson",
      "schema_version": "v1",
      "join_column": "lesson",
      "fact_column": "lesson"
    },
    {
      "entity": "season",
      "schema_version": "v1",
      "scope_source": "derived"
    },
    {
      "entity": "plan",
      "schema_version": "v1",
      "scope_source": "derived"
    }
  ]
}
```

---

## Analytics-Side Tables (DuckDB)

### Historical Raw Layer: `interaction_log_raw`

Append-only. Deduplicated by `name`.

| Column | Type | Notes |
|--------|------|-------|
| `name` | VARCHAR | PK, deduplication key |
| `player` | VARCHAR | |
| `lesson` | VARCHAR | |
| `stage_id` | VARCHAR | |
| `item_id` | VARCHAR | |
| `event_type` | VARCHAR | |
| `time_spent` | INTEGER | |
| `errors_count` | INTEGER | |
| `timestamp` | TIMESTAMP | |
| `season_id` | VARCHAR | |
| `plan_id` | VARCHAR | |
| `archive_scope` | VARCHAR | |
| `archive_job_id` | VARCHAR | |
| `schema_version` | VARCHAR | |
| `exported_at` | TIMESTAMP | |

### Recent Detailed Layer: `interaction_log_recent`

Rolling 90-day window. Rebuilt/refreshed after each ingestion.

Same schema as `interaction_log_raw`. Populated by:
```sql
CREATE OR REPLACE TABLE interaction_log_recent AS
SELECT * FROM interaction_log_raw
WHERE timestamp >= CURRENT_DATE - INTERVAL 90 DAY
```

### Daily Aggregates: `interaction_log_daily_agg`

| Column | Type | Notes |
|--------|------|-------|
| `day` | DATE | Aggregation date |
| `player` | VARCHAR | |
| `lesson` | VARCHAR | |
| `event_type` | VARCHAR | |
| `interaction_count` | INTEGER | COUNT(*) |
| `total_time_spent` | INTEGER | SUM(time_spent) |
| `total_errors` | INTEGER | SUM(errors_count) |
| `completed_count` | INTEGER | COUNT(*) WHERE event_type='Completed' |
| `total_events` | INTEGER | COUNT(*) for completion rate denominator |

**Unique key**: `(day, player, lesson, event_type)`

Populated by:
```sql
CREATE OR REPLACE TABLE interaction_log_daily_agg AS
SELECT
  CAST(timestamp AS DATE) AS day,
  player, lesson, event_type,
  COUNT(*) AS interaction_count,
  COALESCE(SUM(time_spent), 0) AS total_time_spent,
  COALESCE(SUM(errors_count), 0) AS total_errors,
  COUNT(*) FILTER (WHERE event_type = 'Completed') AS completed_count,
  COUNT(*) AS total_events
FROM interaction_log_raw
GROUP BY 1, 2, 3, 4
```

### Monthly Aggregates: `interaction_log_monthly_agg`

Same schema as daily but with `month DATE` (first of month) instead of `day`.

---

## DQ Rules for Interaction Log

Defined in `interaction_log.v1.yaml` under `dq_rules`:

| Rule | Type | Column(s) | Condition |
|------|------|-----------|-----------|
| DQ-01 | not_null | name | |
| DQ-02 | not_null | player | |
| DQ-03 | not_null | lesson | |
| DQ-04 | not_null | stage_id | |
| DQ-05 | not_null | event_type | |
| DQ-06 | not_null | timestamp | |
| DQ-07 | enum_values | event_type | {Started, Completed, Failed, Skipped} |
| DQ-08 | min_value | time_spent | >= 0 |
| DQ-09 | min_value | errors_count | >= 0 |
| DQ-10 | scope_range | timestamp | within job date range |
| DQ-11 | referential | player → dim_player | |
| DQ-12 | referential | lesson → dim_lesson | |
| DQ-13 | unique_key | name | no duplicates |

---

## State Transitions

Same pipeline as Practice Log — no changes to the state machine:

```
Pending → Processing → Exported → Transferred → Ingested → Completed → Purged
                                                                ↓
                                                             Failed (any stage)
```

Post-ingestion addition: analytics-side aggregation refresh (triggered by executor after marking Ingested, before Completed).
