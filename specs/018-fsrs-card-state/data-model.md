# Data Model: FSRS Card State Persistence

**Feature**: 018-fsrs-card-state
**Date**: 2026-02-18

## Entity: Memora Memory State (modified)

### Current Schema (DB columns)

| Column | Type | Nullable | Default | Notes |
|--------|------|----------|---------|-------|
| name | BIGINT | NO | sequence | PK (composite with season_seq) |
| season_seq | INT(11) | NO | - | Partition key (RANGE) |
| season | VARCHAR(140) | YES | - | FK to Memora Season |
| subject | VARCHAR(140) | NO | - | FK to Memora Subject |
| player | VARCHAR(140) | NO | - | FK to Memora Player Profile |
| item_id | BINARY(16) | NO | - | UUID via UUID_TO_BIN polyfill |
| stage_id | VARCHAR(140) | NO | - | Lesson stage identifier |
| lesson | VARCHAR(140) | NO | - | FK to Memora Lesson |
| stability | DECIMAL(21,9) | YES | 0 | FSRS stability score |
| difficulty | DECIMAL(21,9) | YES | 0 | FSRS difficulty score |
| next_review | DATE | YES | NULL | Next scheduled review date |

### New Columns (added by this feature)

| Column | Type | Nullable | Default | Notes |
|--------|------|----------|---------|-------|
| **state** | TINYINT | YES | NULL | FSRS card state: 1=Learning, 2=Review, 3=Relearning. NULL = uninitialized (treat as Learning) |
| **step** | TINYINT | YES | NULL | Learning step counter. NULL when card is in Review state or uninitialized |
| **last_review** | DATETIME(6) | YES | NULL | Timestamp of most recent review. NULL = never reviewed (pre-migration records) |

### Column Type Rationale

- **state as TINYINT**: Only 3 valid values (1, 2, 3). TINYINT uses 1 byte. ENUM would also work but TINYINT is simpler for raw SQL and avoids ENUM pitfalls in MariaDB.
- **step as TINYINT**: Learning steps are typically 0-2 (FSRS default has 2 learning steps). TINYINT (0-255) is more than sufficient.
- **last_review as DATETIME(6)**: Matches Frappe's standard datetime precision (microseconds). Stores UTC timestamp. Consistent with `creation` and `modified` column types.

### Indexes

No new indexes required. The new columns are:
- Not used in WHERE clauses of existing queries
- Not used for sorting or grouping
- Only read during card reconstruction (point lookups via existing `idx_player_item_season`)

Adding an index on `state` would cause write amplification on every review (state changes frequently) with no query benefit.

### Constraints

- **Partition compatibility**: All nullable columns with no DEFAULT can be added to RANGE-partitioned tables without rebuilding partitions (MariaDB instant ADD COLUMN).
- **No foreign keys**: Raw SQL table, no FK enforcement. Application-level validation only.
- **Backward compatibility**: NULL values in all new columns are handled as "uninitialized" by the application layer.

## Entity: FSRS Card (in-memory, from `fsrs` library)

The `fsrs.Card` object is the in-memory representation. After this fix, all 6 fields map bidirectionally:

| Card Field | DB Column | Direction | Conversion |
|------------|-----------|-----------|------------|
| stability | stability | bidirectional | direct (float <-> DECIMAL) |
| difficulty | difficulty | bidirectional | direct (float <-> DECIMAL) |
| due | next_review | bidirectional | `card.due.date()` -> DATE (write), `datetime.combine(date, time.min, tz=utc)` (read) |
| state | state | bidirectional | `card.state.value` -> TINYINT (write), `State(int)` (read) |
| step | step | bidirectional | direct (int <-> TINYINT), NULL preserved |
| last_review | last_review | bidirectional | `.replace(tzinfo=None)` (write), `.replace(tzinfo=utc)` (read) |

## Entity: Redis Cache (modified)

**Key**: `memora:fsrs:{player}:{item_id}` (TTL: 24h)

### Current JSON

```json
{
  "stability": 2.31,
  "difficulty": 2.12,
  "next_review": "2026-02-19",
  "lesson": "LESSON-00001",
  "stage_id": "STAGE-00001"
}
```

### Updated JSON

```json
{
  "stability": 2.31,
  "difficulty": 2.12,
  "next_review": "2026-02-19",
  "state": 1,
  "step": 1,
  "last_review": "2026-02-18T12:00:00",
  "lesson": "LESSON-00001",
  "stage_id": "STAGE-00001"
}
```

### Backward Compatibility

Old cached entries (missing `state`, `step`, `last_review`) are handled by using `.get()` with None default. Missing fields = same behavior as NULL DB values = treat as uninitialized card.

## State Transitions

```
                    [First review]
   NULL state  ─────────────────────► Learning (1), step=0
       │                                    │
       │  (re-init on next review)          │ [Good/Hard review]
       ▼                                    ▼
   Learning (1) ◄───────────────── Learning (1), step=N
       │                                    │
       │                                    │ [Graduation: all steps done]
       │                                    ▼
       │                              Review (2), step=NULL
       │                                    │
       │                                    │ [Again rating = lapse]
       │                                    ▼
       │                            Relearning (3), step=0
       │                                    │
       │                                    │ [Good/Hard review]
       │                                    ▼
       └──────────────────────────── Review (2), step=NULL
```

**Business rule preserved**: Regardless of state transitions, `next_review` is always clamped to minimum tomorrow (next calendar day).
