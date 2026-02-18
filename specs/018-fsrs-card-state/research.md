# Research: FSRS Card State Persistence

**Feature**: 018-fsrs-card-state
**Date**: 2026-02-18

## R1: FSRS v6 Card Object Fields

**Decision**: Persist three new fields: `state` (TINYINT), `step` (TINYINT NULL), `last_review` (DATETIME(6) NULL)

**Research Findings**:

The `fsrs` library v6.3.0 (`Card` object) exposes these fields:
- `stability` (float) - already persisted
- `difficulty` (float) - already persisted
- `due` (datetime, tz-aware) - persisted as `next_review` DATE
- `state` (enum `State`) - **NOT persisted** (the bug)
- `step` (int | None) - **NOT persisted** (the bug)
- `last_review` (datetime | None) - **NOT persisted** (the bug)

**State enum values** (FSRS v6.3.0):
| Name | Value | Description |
|------|-------|-------------|
| Learning | 1 | Card in initial learning phase |
| Review | 2 | Card graduated, normal review |
| Relearning | 3 | Card lapsed, re-entering learning |

**Important**: There is NO `State.New` (value 0) in FSRS v6. A freshly constructed `Card()` has `state=State.Learning` (1), `step=0`, `stability=None`, `difficulty=None`. The spec's assumption "0=New" is incorrect for FSRS v6. The DB default for `state` should be NULL (not 0), and code treats NULL as "needs initialization" (same as Learning, step=0).

**Card progression verified** (simulation):
1. New card (first review Good): state=Learning(1), step=1, stability=2.31
2. Second review Good: state=Review(2), step=None, stability=7.32 (graduated!)
3. Third review Good: state=Review(2), step=None, stability=26.25 (weeks away)
4. Fourth review Good: stability grows further (months away)

**Rationale**: Without state/step/last_review, the `Card()` constructor creates a brand-new Learning card every time. The FSRS scheduler then outputs short learning-step intervals (minutes), which get clamped to "tomorrow" by the business rule. This is why intervals never grow.

**Alternatives Considered**:
- Store as JSON blob: Rejected. Breaks SQL queries for mastery classification and would require application-layer deserialization in all query paths.
- Use `card_id` field: Not needed. The (player, item_id, season_seq) unique index already identifies cards.

## R2: Column Addition on Partitioned Tables

**Decision**: Add nullable columns via `setup.py` `after_migrate()` hook using `ALTER TABLE ADD COLUMN` with `INFORMATION_SCHEMA` idempotency check.

**Research Findings**:

The existing pattern in `setup.py` uses:
1. `INFORMATION_SCHEMA.COLUMNS` check to see if column exists
2. `frappe.db.sql_ddl()` for DDL statements
3. Each function is idempotent (re-runnable)

For MariaDB 10.6 with RANGE-partitioned InnoDB tables:
- `ALTER TABLE ADD COLUMN` with nullable columns is an **instant operation** (metadata-only change, no table rebuild)
- No data migration needed: NULL defaults work for all new columns
- The `before_migrate` hook in `_verify_no_schema_drift()` will block if fields are added to the JSON without corresponding DB columns. We MUST NOT add these fields to the DocType JSON (they are managed by setup.py only, similar to `item_id`).

**Pattern**: Follow the `_ensure_item_id_binary_column()` pattern but with `is_virtual=1` in JSON to prevent Frappe interference.

**Rationale**: Consistent with existing infrastructure. Adding nullable columns is instant on MariaDB InnoDB regardless of row count.

**Alternatives Considered**:
- Frappe schema migration (bench migrate): Blocked by `_guard_memory_state_schema()`. Would require removing protection, which is unsafe.
- Add fields to JSON as non-virtual: Would trigger `_verify_no_schema_drift()` error. The JSON is intentionally sparse for this table.

## R3: Handling Existing Records with NULL New Fields

**Decision**: Treat NULL `state`/`step`/`last_review` as a card needing re-initialization: construct as `Card()` (Learning, step=0) and proceed with normal FSRS review. Do NOT set `last_review` on reconstruction (let FSRS compute it from the review).

**Research Findings**:

Current card reconstruction only sets `stability`, `difficulty`, and `due`. A new `Card()` already starts as Learning(1), step=0, which is the correct default for records missing these fields.

For existing records with `stability > 0` but NULL state/step/last_review:
- The card has been reviewed before but we lost the progression info
- Setting state=Learning, step=0 means the card will go through learning steps again
- This is conservative but correct: after 1-2 correct reviews, the card will graduate to Review state
- The existing stability/difficulty values are preserved and used by FSRS

For the `last_review` field:
- FSRS uses `last_review` to compute elapsed time since last review
- For existing records, we don't know when the last review was
- Setting `last_review = None` causes FSRS to use the card's creation time as reference
- This is acceptable: after one review cycle with the fix, `last_review` will be correctly persisted

**Rationale**: No data migration. Self-correcting over 1-2 review cycles. Conservative approach that never over-promotes cards.

## R4: Redis Cache Extension

**Decision**: Add `state`, `step`, and `last_review` to the Redis cache JSON at `memora:fsrs:{player}:{item_id}`. Existing cached entries (missing new fields) handled via `.get()` with defaults.

**Research Findings**:

Current Redis cache structure (`fsrs_processor.py:417-425`):
```json
{
  "stability": float,
  "difficulty": float,
  "next_review": "YYYY-MM-DD",
  "lesson": str,
  "stage_id": str
}
```

Extended structure:
```json
{
  "stability": float,
  "difficulty": float,
  "next_review": "YYYY-MM-DD",
  "state": int,
  "step": int | null,
  "last_review": "ISO8601" | null,
  "lesson": str,
  "stage_id": str
}
```

The cache has 24h TTL. Existing entries will naturally expire and be replaced with the full state on the next FSRS processing cycle. Code that reads from cache will use `.get("state")` with None default, treating missing fields the same as NULL DB values.

**Note**: The Redis cache is currently only written by the background processor (`fsrs_processor.py`) and is NOT read back during card reconstruction (both `fsrs_processor.py` and `reviews.py` read from MariaDB). The cache is used by other services for display purposes only. Therefore, cache staleness during the 24h transition window has zero impact on FSRS computation correctness.

**Rationale**: Minimal change. Natural TTL expiry handles migration. No cache flush needed.

## R5: Mastery Classification Impact

**Decision**: No change needed to mastery classification queries. The `state` field could theoretically improve classification accuracy, but the current stability-based approach remains correct.

**Research Findings**:

Current mastery query (`profile.py:198-210`) classifies by stability thresholds:
- Mature: stability >= 21.0
- Learning: 0 < stability < 21.0
- New: stability == 0

With the fix, stability values will be more accurate (not inflated by broken reconstruction). This means:
- Cards that previously had inflated stability (e.g., 4550) will gradually self-correct
- New cards will follow the expected progression: ~2.3 -> ~7.3 -> ~26.2 -> ...
- The 21-day threshold for "mature" aligns with FSRS graduation (typically 3-4 correct reviews)

The `state` field could replace stability-based classification, but this would be a separate enhancement. The current approach works correctly with proper stability values.

**Rationale**: Stability-based classification is correct when stability values are accurate. The fix ensures stability values become accurate.

## R6: Code Locations Requiring Changes

**Decision**: Changes limited to 5 files + 1 new migration function.

| File | Change | Scope |
|------|--------|-------|
| `memora_admin/setup.py` | Add `_ensure_fsrs_state_columns()` function, call from `after_migrate()` | New function + 1 line in existing function |
| `memora_admin/setup.py` | Update schema reference comment to include new columns | Comment update |
| `memora_admin/tasks/fsrs_processor.py` | Update `_lookup_memory_state()` SELECT to include state, step, last_review | SQL change |
| `memora_admin/tasks/fsrs_processor.py` | Update card reconstruction to set state, step, last_review | Logic change |
| `memora_admin/tasks/fsrs_processor.py` | Update `_update_memory_state()` to persist state, step, last_review | SQL + signature change |
| `memora_admin/tasks/fsrs_processor.py` | Update `_insert_memory_state()` to include state, step, last_review | SQL + signature change |
| `memora_admin/tasks/fsrs_processor.py` | Update Redis cache to include state, step, last_review | JSON change |
| `memora_admin/api/reviews.py` | Update `submit_reviews()` SELECT to include state, step, last_review | SQL change |
| `memora_admin/api/reviews.py` | Update card reconstruction to set state, step, last_review | Logic change |
| `memora_admin/api/reviews.py` | Update `submit_reviews()` UPDATE to persist state, step, last_review | SQL change |
| `memora_admin/memora_admin/doctype/memora_memory_state/memora_memory_state.json` | Add state, step, last_review fields as `is_virtual=1` | JSON update (display only) |

**No changes needed**:
- `get_review_overview()` - counts only, doesn't touch new fields
- `get_due_items()` - returns items for display, doesn't reconstruct cards
- `get_memory_mastery()` - uses stability-based classification (unchanged)
- `_verify_no_schema_drift()` - new fields are `is_virtual=1`, so excluded from drift check
- FastAPI endpoints - delegate to Frappe API or use existing service patterns
