# Data Model: Weekly Structure Progress Snapshots

**Feature**: 043-progress-snapshots | **Date**: 2026-03-11

## Entity Relationship Overview

```
┌──────────────────────────────┐       ┌──────────────────────────────┐
│ tabMemora Structure Progress │       │  tabMemora Player Profile    │
│ (SOURCE — read-only)         │       │  (ENRICHMENT — read-only)    │
├──────────────────────────────┤       ├──────────────────────────────┤
│ name        VARCHAR(140) PK  │       │ name        VARCHAR(140) PK  │
│ player      VARCHAR(140) FK ─┼──────►│ plan        VARCHAR(140) FK  │
│ subject     VARCHAR(140) FK  │       │ grade       VARCHAR(140) FK  │
│ completion_percentage FLOAT  │       │ major       VARCHAR(140) FK  │
│ passed_lessons_bitset TEXT   │       │ season      VARCHAR(140) FK  │
│ creation    DATETIME         │       │ display_name VARCHAR(140)    │
│ modified    DATETIME         │       │ ...                          │
└──────────────────────────────┘       └──────────────────────────────┘
                                                    │
                                                    ▼
                                       ┌──────────────────────────────┐
                                       │  tabMemora Academic Plan     │
                                       │  (REFERENCE — not queried)   │
                                       ├──────────────────────────────┤
                                       │ name        VARCHAR(140) PK  │
                                       │ plan_name   VARCHAR(140)     │
                                       │ grade       VARCHAR(140) FK  │
                                       │ season      VARCHAR(140) FK  │
                                       └──────────────────────────────┘
```

## Output Entity: Structure Progress Snapshot (Parquet)

### Schema

| Column | Arrow Type | Source | Description |
|--------|-----------|--------|-------------|
| `snapshot_date` | `date32` | Pipeline parameter | The ISO date (YYYY-MM-DD) representing the snapshot week. Typically the Sunday when the job runs. |
| `player_id` | `utf8` | `sp.player` | FK to Memora Player Profile. Identifies the student. |
| `plan_id` | `utf8` | `pp.plan` | FK to Memora Academic Plan. The student's active plan at snapshot time. Guaranteed non-null (rejected otherwise). |
| `subject_id` | `utf8` | `sp.subject` | FK to Memora Subject. Identifies the subject. |
| `completion_percentage` | `float64` | `sp.completion_percentage` | Progress percentage (0.0–100.0) for this player-subject combination. |

### Grain

One row per unique `(snapshot_date, player_id, plan_id, subject_id)` combination.

- A student with progress in 5 subjects produces 5 rows per snapshot.
- A student who changes plans mid-week and has progress rebuilt for both plans will have rows under both plan_ids (one from old progress, one from new).
- In practice, a student typically has one plan at a time, so the grain effectively degenerates to `(snapshot_date, player_id, subject_id)`.

### Uniqueness Constraint

The grain columns form a natural composite key. The extraction SQL guarantees uniqueness because `tabMemora Structure Progress` has one row per `(player, subject)` and each player has exactly one active plan at any time.

**DQ enforcement**: A `unique_key` DQ rule on `[player_id, plan_id, subject_id]` validates this invariant post-export.

## Extraction SQL

### Valid rows (fact export)

```sql
SELECT
  %s AS snapshot_date,
  sp.`player`                AS player_id,
  pp.`plan`                  AS plan_id,
  sp.`subject`               AS subject_id,
  sp.`completion_percentage`
FROM `tabMemora Structure Progress` sp
INNER JOIN `tabMemora Player Profile` pp
  ON sp.`player` = pp.`name`
WHERE pp.`plan` IS NOT NULL
ORDER BY sp.`player`, sp.`subject`
```

- `INNER JOIN` excludes students with no player profile (orphaned progress rows).
- `WHERE pp.plan IS NOT NULL` excludes students whose profile has no plan assigned.
- `ORDER BY` ensures deterministic row ordering for byte-identical idempotent output.
- `%s` is parameterized with the `snapshot_date` value.

### Rejected row count (observability)

```sql
SELECT
  SUM(CASE WHEN pp.`name` IS NULL THEN 1 ELSE 0 END) AS no_profile,
  SUM(CASE WHEN pp.`name` IS NOT NULL AND pp.`plan` IS NULL THEN 1 ELSE 0 END) AS null_plan
FROM `tabMemora Structure Progress` sp
LEFT JOIN `tabMemora Player Profile` pp
  ON sp.`player` = pp.`name`
WHERE pp.`name` IS NULL OR pp.`plan` IS NULL
```

Returns two counts:
- `no_profile`: Structure Progress rows where the player has no matching Player Profile.
- `null_plan`: Structure Progress rows where the player profile exists but `plan` is NULL.

Total rejected = `no_profile + null_plan`.

## Output File Layout

```
{snapshot_output_path}/
└── structure_progress/
    ├── 2026-03-08/
    │   ├── fact_structure_progress.parquet
    │   └── manifest.json
    ├── 2026-03-15/
    │   ├── fact_structure_progress.parquet
    │   └── manifest.json
    └── ...
```

- Each `snapshot_date/` directory contains exactly one fact Parquet file and one manifest.
- No dimension files in v1 (dimensions join from live DB or existing archives).
- Directory name is the `snapshot_date` value (ISO format, e.g., `2026-03-08`).

## Manifest Schema

Reuses `archive_executor.manifest.build_manifest()` with snapshot-specific values:

```json
{
  "manifest_version": "1.0",
  "dataset_key": "structure_progress_snapshot",
  "kind": "snapshot",
  "batch_id": "SNAP-2026-03-08",
  "schema_version": "1.0",
  "created_at": "2026-03-08T03:05:12Z",
  "source": "memora_admin",
  "scope_key": "2026-03-08",
  "files": [
    {
      "role": "fact",
      "entity": "structure_progress",
      "filename": "fact_structure_progress.parquet",
      "row_count": 42150,
      "checksum": "sha256:...",
      "size_bytes": 1234567
    }
  ]
}
```

## State Transitions

No state machine in v1. The pipeline is a single run-to-completion operation:

```
[Cron trigger] → Extract → Write Parquet → Build Manifest → Atomic Swap → [Done]
```

If any step fails, the staging directory is left in place (no partial final output). The next cron run cleans up stale staging and retries from scratch.

## Validation Rules (DQ)

Defined in `archive_schemas/snapshot_types/structure_progress.v1.yaml`:

| Rule ID | Type | Target | Description |
|---------|------|--------|-------------|
| DQ-SP-01 | `not_null` | `snapshot_date` | Every row must have a snapshot date |
| DQ-SP-02 | `not_null` | `player_id` | Every row must have a player |
| DQ-SP-03 | `not_null` | `plan_id` | Every row must have a plan (rejects filtered at extraction) |
| DQ-SP-04 | `not_null` | `subject_id` | Every row must have a subject |
| DQ-SP-05 | `not_null` | `completion_percentage` | Completion must be present |
| DQ-SP-06 | `min_value` | `completion_percentage` (min: 0) | Percentage cannot be negative |
| DQ-SP-07 | `max_value` | `completion_percentage` (max: 100) | Percentage cannot exceed 100 |
| DQ-SP-08 | `unique_key` | `[player_id, plan_id, subject_id]` | No duplicate grain within a snapshot |
