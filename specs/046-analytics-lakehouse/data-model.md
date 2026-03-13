# Data Model: Analytics Lakehouse

**Feature**: 046-analytics-lakehouse | **Date**: 2026-03-12

## 1. Lake Directory Layout (Analytics Server)

```text
{ANALYTICS_REMOTE_PATH}/
├── lake/
│   ├── practice_log/                   # Archive fact (Hive-partitioned)
│   │   └── year=YYYY/month=MM/day=DD/
│   │       └── part-{job_id}.parquet
│   ├── interaction_log/                # Archive fact
│   │   └── year=YYYY/month=MM/day=DD/
│   │       └── part-{job_id}.parquet
│   ├── memory_state/                   # Archive fact (season-scoped)
│   │   └── season_seq=NN/
│   │       └── part-{job_id}.parquet
│   ├── task_run_log/                   # Archive fact
│   │   └── year=YYYY/month=MM/day=DD/
│   │       └── part-{job_id}.parquet
│   ├── practice_log_live/              # Live snapshot (atomic swap)
│   │   └── latest/
│   │       └── part-0000.parquet
│   ├── memory_state_current/           # Incremental sync mirror
│   │   └── season_{N}/
│   │       └── sync_{ts}.parquet
│   └── structure_progress/             # Weekly snapshots
│       └── snapshot_date=YYYY-MM-DD/
│           └── part-0000.parquet
├── dimensions/
│   ├── dim_player.parquet
│   ├── dim_player_history.parquet      # NEW — SCD2
│   ├── dim_season.parquet
│   ├── dim_plan.parquet
│   ├── dim_review_item.parquet
│   └── dim_lesson.parquet
├── manifests/
│   └── archive/
│       └── {JOB_ID}.json
└── analytics.duckdb                    # DuckDB database file
```

## 2. DuckDB Schema

### 2.1 Archive Views (read directly from Parquet)

```sql
-- FR-016, FR-017: Semantic views over Hive-partitioned Parquet
CREATE OR REPLACE VIEW practice_log_archive AS
SELECT * FROM read_parquet(
  '{lake_path}/practice_log/**/*.parquet',
  hive_partitioning = true,
  union_by_name = true
);

CREATE OR REPLACE VIEW interaction_log_archive AS
SELECT * FROM read_parquet(
  '{lake_path}/interaction_log/**/*.parquet',
  hive_partitioning = true,
  union_by_name = true
);

CREATE OR REPLACE VIEW memory_state_archive AS
SELECT * FROM read_parquet(
  '{lake_path}/memory_state/**/*.parquet',
  hive_partitioning = true,
  union_by_name = true
);

CREATE OR REPLACE VIEW task_run_log_archive AS
SELECT * FROM read_parquet(
  '{lake_path}/task_run_log/**/*.parquet',
  hive_partitioning = true,
  union_by_name = true
);

CREATE OR REPLACE VIEW structure_progress_snapshots AS
SELECT * FROM read_parquet(
  '{lake_path}/structure_progress/**/*.parquet',
  hive_partitioning = true,
  union_by_name = true
);
```

### 2.2 Live/Mirror Tables (DuckDB-managed)

```sql
-- Live practice log — replaced atomically by ingest-live
CREATE TABLE IF NOT EXISTS practice_log_live (
  player_id    VARCHAR,
  item_id      VARCHAR,
  first_seen_at TIMESTAMP,
  last_seen_at  TIMESTAMP,
  last_result   VARCHAR,
  attempt_count INTEGER,
  correct_count INTEGER,
  season_id    VARCHAR,
  plan_id      VARCHAR,
  scope_type   VARCHAR,
  sync_batch_id VARCHAR,
  schema_version VARCHAR,
  synced_at    TIMESTAMP
);

-- Memory state current mirror — upserted by incremental sync
CREATE TABLE IF NOT EXISTS memory_state_current (
  name         BIGINT,
  season_seq   INTEGER,
  subject      VARCHAR,
  player       VARCHAR,
  item_id      VARCHAR,
  stage_id     VARCHAR,
  stability    DOUBLE,
  difficulty   DOUBLE,
  next_review  TIMESTAMP,
  lesson       VARCHAR,
  state        TINYINT,
  step         TINYINT,
  last_review  TIMESTAMP,
  modified     TIMESTAMP
);
```

### 2.3 Combined Views

```sql
-- FR-016: Combined practice log (archive + live, no overlap guaranteed by export exclusion)
CREATE OR REPLACE VIEW practice_log_combined AS
SELECT player_id, item_id, first_seen_at, last_seen_at,
       last_result, attempt_count, correct_count,
       season_id, plan_id, 'archive' AS source
FROM practice_log_archive
UNION ALL
SELECT player_id, item_id, first_seen_at, last_seen_at,
       last_result, attempt_count, correct_count,
       season_id, plan_id, 'live' AS source
FROM practice_log_live;

-- Combined memory state (archived seasons + current mirror)
CREATE OR REPLACE VIEW memory_state_combined AS
SELECT name, season_seq, subject, player, item_id,
       stage_id, stability, difficulty, next_review,
       lesson, state, step, last_review, modified, 'archive' AS source
FROM memory_state_archive
UNION ALL
SELECT name, season_seq, subject, player, item_id,
       stage_id, stability, difficulty, next_review,
       lesson, state, step, last_review, modified, 'current' AS source
FROM memory_state_current;
```

### 2.4 Dimension Views

```sql
-- Dimensions read from single Parquet files
CREATE OR REPLACE VIEW dim_player AS
SELECT * FROM read_parquet('{dimensions_path}/dim_player.parquet');

CREATE OR REPLACE VIEW dim_player_history AS
SELECT * FROM read_parquet('{dimensions_path}/dim_player_history.parquet');

CREATE OR REPLACE VIEW dim_season AS
SELECT * FROM read_parquet('{dimensions_path}/dim_season.parquet');

CREATE OR REPLACE VIEW dim_plan AS
SELECT * FROM read_parquet('{dimensions_path}/dim_plan.parquet');

CREATE OR REPLACE VIEW dim_review_item AS
SELECT * FROM read_parquet('{dimensions_path}/dim_review_item.parquet');

CREATE OR REPLACE VIEW dim_lesson AS
SELECT * FROM read_parquet('{dimensions_path}/dim_lesson.parquet');
```

### 2.5 Aggregate Tables

```sql
-- Rebuilt by refresh-aggregates
CREATE TABLE IF NOT EXISTS practice_daily_agg (
  date         DATE,
  player_id    VARCHAR,
  season_id    VARCHAR,
  plan_id      VARCHAR,
  total_attempts INTEGER,
  total_correct  INTEGER,
  unique_items   INTEGER,
  PRIMARY KEY (date, player_id)
);

CREATE TABLE IF NOT EXISTS practice_monthly_agg (
  year_month   VARCHAR,  -- 'YYYY-MM'
  player_id    VARCHAR,
  season_id    VARCHAR,
  plan_id      VARCHAR,
  total_attempts INTEGER,
  total_correct  INTEGER,
  unique_items   INTEGER,
  active_days    INTEGER,
  PRIMARY KEY (year_month, player_id)
);

-- Rolling recent window — rebuilt by refresh-recent
CREATE TABLE IF NOT EXISTS practice_recent (
  player_id    VARCHAR,
  item_id      VARCHAR,
  first_seen_at TIMESTAMP,
  last_seen_at  TIMESTAMP,
  last_result   VARCHAR,
  attempt_count INTEGER,
  correct_count INTEGER,
  season_id    VARCHAR,
  plan_id      VARCHAR,
  source       VARCHAR   -- 'archive' or 'live'
);
```

## 3. SCD2 Player History Dimension (NEW)

### Source: `tabMemora Player Plan History`

Existing columns used:
- `player` — player ID
- `new_plan`, `new_grade`, `new_major`, `new_season` — state after change
- `changed_at` — timestamp of change
- `trigger_reason` — Season Expired / Voluntary Change

### Exported Dimension: `dim_player_history.parquet`

| Column | Type | Description |
|--------|------|-------------|
| player_id | string | Player identifier |
| plan_id | string | Academic plan at this period |
| plan_name | string | Denormalized plan name |
| grade | string | Grade level |
| major | string | Major/specialization |
| season_id | string | Season reference |
| valid_from | timestamp | Start of this plan period |
| valid_to | timestamp | End of this plan period (NULL = current) |
| is_current | bool | TRUE if this is the active period |
| trigger_reason | string | What caused the transition |

### Derivation Logic

```python
# Pseudo-code for SCD2 export
rows = query("SELECT * FROM `tabMemora Player Plan History` ORDER BY player, changed_at")

for each player group:
    for i, row in enumerate(changes):
        valid_from = row.changed_at
        valid_to = changes[i+1].changed_at if i+1 < len(changes) else None
        is_current = (valid_to is None)
        emit(player_id, plan_id, grade, major, season_id, valid_from, valid_to, is_current)
```

### Schema File: `archive_schemas/dimensions/player_history.v1.yaml`

```yaml
entity: player_history
version: v1
source_table: "tabMemora Player Plan History"
id_column: name
fields:
  - player_id
  - plan_id
  - plan_name
  - grade
  - major
  - season_id
  - valid_from
  - valid_to
  - is_current
  - trigger_reason
query: >
  SELECT
    h.`player` AS player_id,
    h.`new_plan` AS plan_id,
    ap.`plan_name`,
    h.`new_grade` AS grade,
    h.`new_major` AS major,
    h.`new_season` AS season_id,
    h.`changed_at` AS valid_from,
    LEAD(h.`changed_at`) OVER (
      PARTITION BY h.`player` ORDER BY h.`changed_at`
    ) AS valid_to,
    CASE WHEN LEAD(h.`changed_at`) OVER (
      PARTITION BY h.`player` ORDER BY h.`changed_at`
    ) IS NULL THEN 1 ELSE 0 END AS is_current,
    h.`trigger_reason`
  FROM `tabMemora Player Plan History` h
  LEFT JOIN `tabMemora Academic Plan` ap ON h.`new_plan` = ap.`name`
  ORDER BY h.`player`, h.`changed_at`
```

## 4. Entity Relationship Diagram (Analytics Side)

```text
┌─────────────────────┐
│ practice_log_archive│──┐
├─────────────────────┤  │
│ player_id (FK)      │  │   ┌──────────────────┐
│ item_id (FK)        │──┼──→│ dim_review_item   │
│ season_id (FK)      │  │   └──────────────────┘
│ plan_id (FK)        │  │
│ year/month/day (PK) │  │   ┌──────────────────┐
└─────────────────────┘  ├──→│ dim_player        │
                         │   └──────────────────┘
┌─────────────────────┐  │
│ practice_log_live   │──┤   ┌──────────────────────┐
├─────────────────────┤  ├──→│ dim_player_history    │
│ (same schema)       │  │   │ (SCD2 temporal join)  │
└─────────────────────┘  │   └──────────────────────┘
                         │
┌─────────────────────┐  │   ┌──────────────────┐
│ memory_state_archive│──┼──→│ dim_season        │
├─────────────────────┤  │   └──────────────────┘
│ season_seq (PK)     │  │
│ player (FK)         │  │   ┌──────────────────┐
└─────────────────────┘  └──→│ dim_plan          │
                              └──────────────────┘
┌─────────────────────┐
│ interaction_log_    │       ┌──────────────────┐
│ archive             │──────→│ dim_lesson        │
├─────────────────────┤       └──────────────────┘
│ player (FK)         │
│ lesson (FK)         │
│ item_id (FK)        │
└─────────────────────┘

┌─────────────────────┐
│ structure_progress_ │
│ snapshots           │
├─────────────────────┤
│ snapshot_date (PK)  │
│ player_id (FK)      │
│ plan_id (FK)        │
│ subject_id          │
└─────────────────────┘
```

## 5. State Transitions

### Analytics CLI does NOT manage Archive Job state.

The production executor (`run.py`) owns state transitions. The analytics CLI only:
- Receives Parquet files via rsync (transfer.py)
- Loads them into DuckDB/views
- Returns JSON success/failure to stdout

State mapping:
```
Production (run.py)          Analytics CLI
─────────────────          ────────────────
Exported → Transferred      (rsync happens)
Transferred → Ingested      ingest-archive / ingest-live → {status: ok}
Ingested → Completed        handoff → {status: ok}
```

## 6. Validation Rules (Analytics Side)

### Health Check: Duplicate Detection (FR-022)

```sql
SELECT player_id, item_id, last_seen_at, COUNT(*) AS cnt
FROM practice_log_combined
GROUP BY player_id, item_id, last_seen_at
HAVING cnt > 1;
```

### Health Check: Dimension Coverage

```sql
SELECT DISTINCT p.player_id
FROM practice_log_combined p
LEFT JOIN dim_player_history h ON p.player_id = h.player_id
WHERE h.player_id IS NULL;
```

### Health Check: Manifest Checksum

For each manifest in `manifests/archive/`:
1. Read `manifest.json` → get expected SHA-256 for each file
2. Compute actual SHA-256 of the referenced Parquet file
3. Compare and report mismatches
