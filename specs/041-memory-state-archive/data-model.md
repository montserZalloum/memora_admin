# Data Model: Memory State Archive Lifecycle

**Feature Branch**: `041-memory-state-archive`
**Date**: 2026-03-11

## Source Entity: Memory State Row

**Table**: `tabMemora Memory State` (Frappe DocType, RANGE-partitioned by `season_seq`)

| Column | Type | Nullable | Notes |
|--------|------|----------|-------|
| `name` | BIGINT | NO | PK (auto-increment via Frappe sequence), composite PK with `season_seq` |
| `season_seq` | INT | NO | Partition key, FK → Season.season_seq |
| `subject` | VARCHAR(140) | YES | Subject area |
| `player` | VARCHAR(140) | NO | FK → `tabMemora Player Profile`.name |
| `item_id` | BINARY(16) | NO | UUID stored as binary, exported as UUID string via `BIN_TO_UUID()` |
| `stage_id` | VARCHAR(140) | YES | Stage identifier |
| `stability` | FLOAT | NO | FSRS stability parameter |
| `difficulty` | FLOAT | NO | FSRS difficulty parameter (0–1) |
| `next_review` | DATETIME | YES | Next scheduled review date |
| `lesson` | VARCHAR(140) | YES | FK → Lesson |
| `state` | TINYINT | NO | FSRS state (virtual, managed by setup.py) |
| `step` | TINYINT | YES | FSRS step (virtual, managed by setup.py) |
| `last_review` | DATETIME | YES | Last review timestamp (virtual, managed by setup.py) |
| `creation` | DATETIME | NO | Frappe standard — row creation time |
| `modified` | DATETIME | NO | Frappe standard — last modification time, used for incremental sync |

**Indexes**:
- `PRIMARY KEY (name, season_seq)` — composite for partition pruning
- `idx_player_item_season (player, item_id, season_seq)` — UNIQUE
- `idx_review_query (player, subject, next_review, season_seq)`

**Partitioning**:
```
RANGE(season_seq):
  p_season_1   VALUES LESS THAN (2)
  p_season_2   VALUES LESS THAN (3)
  ...
  p_season_N   VALUES LESS THAN (N+1)
  p_future     VALUES LESS THAN MAXVALUE
```

---

## Related Entity: Season

**Table**: `tabMemora Season` (Frappe DocType)

| Column | Type | Notes |
|--------|------|-------|
| `name` | VARCHAR(140) | PK, auto-named `SEAS-#####.` |
| `season_title` | VARCHAR(140) | Display name |
| `season_seq` | INT | Sequential integer, unique, used as partition key |
| `start_date` | DATE | Season start |
| `end_date` | DATE | Season end — **archive eligibility trigger** |
| `is_published` | TINYINT | Published flag |

**Archive eligibility**: A season is archive-eligible when `end_date < CURDATE()`.

---

## Related Entity: Player Profile (for active-linkage check)

**Table**: `tabMemora Player Profile`

| Column | Type | Notes |
|--------|------|-------|
| `name` | VARCHAR(140) | PK, auto-named `PLAYER-#####.` |
| `season` | VARCHAR(140) | FK → Season.name — **active-linkage check target** |
| `plan` | VARCHAR(140) | FK → Academic Plan.name |

**Safety gate query**: `SELECT COUNT(*) FROM tabMemora Player Profile WHERE season = %s`

---

## Related Entity: Academic Plan (for active-linkage check)

**Table**: `tabMemora Academic Plan`

| Column | Type | Notes |
|--------|------|-------|
| `name` | VARCHAR(140) | PK, auto-named `PLAN-#####.` |
| `season` | VARCHAR(140) | FK → Season.name |
| `is_published` | TINYINT | Only published plans block cleanup |

**Safety gate query**: `SELECT COUNT(*) FROM tabMemora Academic Plan WHERE season = %s AND is_published = 1`

---

## Archive Type Schema: `memory_state.v1.yaml`

New file at `archive_schemas/archive_types/memory_state.v1.yaml`.

### Fact Columns (exported to Parquet)

| Column | Source | Type |
|--------|--------|------|
| `name` | `ms.name` | BIGINT |
| `season_seq` | `ms.season_seq` | INT |
| `subject` | `ms.subject` | VARCHAR(140) |
| `player` | `ms.player` | VARCHAR(140) |
| `item_id` | `BIN_TO_UUID(ms.item_id)` | VARCHAR(36) |
| `stage_id` | `ms.stage_id` | VARCHAR(140) |
| `stability` | `ms.stability` | FLOAT |
| `difficulty` | `ms.difficulty` | FLOAT |
| `next_review` | `ms.next_review` | DATETIME |
| `lesson` | `ms.lesson` | VARCHAR(140) |
| `state` | `ms.state` | TINYINT |
| `step` | `ms.step` | TINYINT |
| `last_review` | `ms.last_review` | DATETIME |
| `modified` | `ms.modified` | DATETIME |

### Scope Configuration

- **scope_type**: `season` (not date-based)
- **scope_column**: `season_seq`
- **archive_scope format**: `season_N` (e.g., `season_3`)

### Fact SQL

```sql
-- Full season export (for archive)
SELECT
  ms.`name`, ms.`season_seq`, ms.`subject`, ms.`player`,
  BIN_TO_UUID(ms.`item_id`) AS item_id,
  ms.`stage_id`, ms.`stability`, ms.`difficulty`, ms.`next_review`,
  ms.`lesson`, ms.`state`, ms.`step`, ms.`last_review`, ms.`modified`
FROM `tabMemora Memory State` ms
WHERE ms.`season_seq` = %s
ORDER BY ms.`name`

-- Incremental sync (for active season sync)
SELECT
  ms.`name`, ms.`season_seq`, ms.`subject`, ms.`player`,
  BIN_TO_UUID(ms.`item_id`) AS item_id,
  ms.`stage_id`, ms.`stability`, ms.`difficulty`, ms.`next_review`,
  ms.`lesson`, ms.`state`, ms.`step`, ms.`last_review`, ms.`modified`
FROM `tabMemora Memory State` ms
WHERE ms.`season_seq` = %s AND ms.`modified` >= %s
ORDER BY ms.`modified`
```

### Dimensions

| Entity | Schema Version | Join Column | Scope Source |
|--------|---------------|-------------|--------------|
| player | v3 | player | direct |
| season | v1 | — | derived (season IS the scope) |

### DQ Rules

| Rule | Type | Column(s) | Condition |
|------|------|-----------|-----------|
| DQ-01 | not_null | name | |
| DQ-02 | not_null | season_seq | |
| DQ-03 | not_null | player | |
| DQ-04 | not_null | item_id | |
| DQ-05 | not_null | stability | |
| DQ-06 | not_null | difficulty | |
| DQ-07 | min_value | stability | >= 0 |
| DQ-08 | min_value | difficulty | >= 0 |
| DQ-09 | max_value | difficulty | <= 1 |
| DQ-10 | unique_key | [name, season_seq] | composite uniqueness |
| DQ-11 | referential | player → dim_player | player exists in dimension |

### Export Metadata (injected columns)

Same pattern as other archive types: `archive_scope`, `archive_job_id`, `schema_version`, `exported_at`.

For incremental sync, use: `synced_at` instead of `exported_at`.

---

## Archive Job for Memory State

Reuses the existing `tabMemora Archive Job` DocType.

| Field | Value |
|-------|-------|
| `source_doctype` | `Memora Memory State` |
| `archive_type` | `memory_state` |
| `archive_scope` | `season_N` (e.g., `season_3`) |
| `schema_version` | `v1` |
| `status` | `Pending` |
| `post_archive_action` | `Delete` |
| `job_meta` | JSON (see below) |

### job_meta Structure

```json
{
  "query_filter": {
    "season_seq": 3,
    "season_name": "SEAS-00003",
    "filter_column": "season_seq",
    "filter_type": "season"
  },
  "export_columns": [
    "name", "season_seq", "subject", "player", "item_id",
    "stage_id", "stability", "difficulty", "next_review",
    "lesson", "state", "step", "last_review", "modified"
  ],
  "schema_snapshot": {
    "columns": [
      {"name": "name", "type": "BIGINT"},
      {"name": "season_seq", "type": "INT"},
      {"name": "subject", "type": "VARCHAR(140)"},
      {"name": "player", "type": "VARCHAR(140)"},
      {"name": "item_id", "type": "VARCHAR(36)"},
      {"name": "stage_id", "type": "VARCHAR(140)"},
      {"name": "stability", "type": "FLOAT"},
      {"name": "difficulty", "type": "FLOAT"},
      {"name": "next_review", "type": "DATETIME"},
      {"name": "lesson", "type": "VARCHAR(140)"},
      {"name": "state", "type": "TINYINT"},
      {"name": "step", "type": "TINYINT"},
      {"name": "last_review", "type": "DATETIME"},
      {"name": "modified", "type": "DATETIME"}
    ],
    "primary_key": ["name", "season_seq"]
  },
  "related_tables": [
    {
      "entity": "player",
      "schema_version": "v3",
      "join_column": "player",
      "fact_column": "player"
    },
    {
      "entity": "season",
      "schema_version": "v1",
      "scope_source": "derived"
    }
  ],
  "fact_sql": {
    "filtered": "SELECT ms.`name`, ms.`season_seq`, ms.`subject`, ms.`player`, BIN_TO_UUID(ms.`item_id`) AS item_id, ms.`stage_id`, ms.`stability`, ms.`difficulty`, ms.`next_review`, ms.`lesson`, ms.`state`, ms.`step`, ms.`last_review`, ms.`modified` FROM `tabMemora Memory State` ms WHERE ms.`season_seq` = %s ORDER BY ms.`name`",
    "incremental": "SELECT ms.`name`, ms.`season_seq`, ms.`subject`, ms.`player`, BIN_TO_UUID(ms.`item_id`) AS item_id, ms.`stage_id`, ms.`stability`, ms.`difficulty`, ms.`next_review`, ms.`lesson`, ms.`state`, ms.`step`, ms.`last_review`, ms.`modified` FROM `tabMemora Memory State` ms WHERE ms.`season_seq` = %s AND ms.`modified` >= %s ORDER BY ms.`modified`"
  },
  "scope_column": "season_seq"
}
```

---

## Analytics-Side Tables (DuckDB)

### Current Mirror: `memory_state_current`

Holds the latest state for all **active** seasons. Updated via upsert during incremental sync. Cleaned up (season removed) after archival.

| Column | Type | Notes |
|--------|------|-------|
| `name` | BIGINT | Part of composite PK |
| `season_seq` | INTEGER | Part of composite PK |
| `subject` | VARCHAR | |
| `player` | VARCHAR | |
| `item_id` | VARCHAR | UUID string |
| `stage_id` | VARCHAR | |
| `stability` | DOUBLE | |
| `difficulty` | DOUBLE | |
| `next_review` | TIMESTAMP | |
| `lesson` | VARCHAR | |
| `state` | TINYINT | |
| `step` | TINYINT | |
| `last_review` | TIMESTAMP | |
| `modified` | TIMESTAMP | |
| `synced_at` | TIMESTAMP | Injection metadata |

**Unique constraint**: `(name, season_seq)`

**Upsert**: `INSERT OR REPLACE INTO memory_state_current ...`

**Season cleanup**: `DELETE FROM memory_state_current WHERE season_seq = N`

### Archived Season Parquet: `archive/memory_state/season_{N}/`

One directory per archived season. Contains:
- `facts.parquet` — Final snapshot of all Memory State rows for the season
- `dim_player.parquet` — Player dimension snapshot
- `dim_season.parquet` — Season dimension snapshot
- `manifest.json` — Row count, checksums, export timestamp, archive job ID

Parquet files use Snappy compression (consistent with existing archive types).

---

## Sync Checkpoint (executor-side)

Not a database table. Local JSON file per season.

**Path**: `{config.sync_state_path}/memory_state/season_{N}.json`

```json
{
  "season_seq": 3,
  "season_name": "SEAS-00003",
  "last_checkpoint": "2026-03-11T14:30:00",
  "last_sync_rows": 1250,
  "total_rows_synced": 45200,
  "last_sync_at": "2026-03-11T14:45:00"
}
```

---

## State Transitions

### Archive Pipeline (same stages as existing)

```
Pending → Processing → Exported → Transferred → Ingested → Completed → Purged
                                                                 ↓
                                                              Failed (any stage)
```

**Differences from Practice Log / Interaction Log**:
- **Pre-Pending**: Season scheduler creates job when `end_date < TODAY`
- **Between Completed and Purged**: Safety gates must pass (archive validated, no active linkage)
- **Purge method**: `DROP PARTITION` instead of batched DELETE

### Incremental Sync (new, independent of archive pipeline)

```
Idle → Extracting → Exporting → Transferring → Ingesting → Checkpoint Updated → Idle
```

Runs on a schedule (default every 15 minutes) for each active season. Not tracked via Memora Archive Job — uses the lightweight checkpoint file. Errors are logged but don't create Failed archive jobs (sync is ongoing, not a one-shot operation).

### Season Lifecycle (orchestration view)

```
Season Created → [Partition Created]
       ↓
Active Season → [Incremental Sync Running]
       ↓ (end_date passes)
Ended Season → [Sync Paused] → [Archive Job Created]
       ↓
Archive Export → Validate → Transfer → Ingest Archive
       ↓
Mirror Cleanup (remove from current mirror)
       ↓
Safety Gates Check → [If all pass] → DROP PARTITION → Purged
                   → [If blocked]  → Wait (retry later)
```
