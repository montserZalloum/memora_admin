# Contract: Structure Progress Snapshot Parquet Schema

**Version**: 1.0 | **Date**: 2026-03-11 | **Kind**: Output data contract

## Schema Definition

| # | Column Name | Arrow Type | Nullable | Description |
|---|------------|-----------|----------|-------------|
| 1 | `snapshot_date` | `date32` | No | ISO date of the snapshot (YYYY-MM-DD) |
| 2 | `player_id` | `utf8` | No | Memora Player Profile name (e.g., `PLAYER-00123`) |
| 3 | `plan_id` | `utf8` | No | Memora Academic Plan name (e.g., `PLAN-00045`) |
| 4 | `subject_id` | `utf8` | No | Memora Subject name (e.g., `SUBJ-00012`) |
| 5 | `completion_percentage` | `float64` | No | Progress percentage, range [0.0, 100.0] |

## pyarrow Schema (code)

```python
import pyarrow as pa

SNAPSHOT_SCHEMA = pa.schema([
    pa.field("snapshot_date", pa.date32(), nullable=False),
    pa.field("player_id", pa.utf8(), nullable=False),
    pa.field("plan_id", pa.utf8(), nullable=False),
    pa.field("subject_id", pa.utf8(), nullable=False),
    pa.field("completion_percentage", pa.float64(), nullable=False),
])
```

## Grain

`(snapshot_date, player_id, plan_id, subject_id)` — one row per student-plan-subject per week.

## Ordering

Rows are ordered by `(player_id ASC, subject_id ASC)` within each snapshot partition. This ordering is guaranteed by the extraction SQL's `ORDER BY` clause and enables deterministic byte-identical output for idempotent reruns.

## Compression

Default pyarrow Parquet compression: Snappy. No custom compression settings.

## Versioning

Schema version is tracked in the manifest's `schema_version` field (`"1.0"`). Breaking changes (column additions, type changes) require a version bump.

## Excluded Columns

Per FR-010, the following source columns are explicitly **NOT** included:
- `passed_lessons_bitset` (Long Text — large binary data, not needed for analytics)
- `name` (Frappe auto-generated row ID — not meaningful for snapshots)
- `creation`, `modified`, `modified_by`, `owner`, `docstatus`, `idx` (Frappe standard columns)
