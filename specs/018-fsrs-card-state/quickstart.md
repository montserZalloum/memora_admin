# Quickstart: FSRS Card State Persistence

**Feature**: 018-fsrs-card-state
**Date**: 2026-02-18

## What This Feature Does

Fixes the FSRS spaced repetition system so review intervals actually grow with mastery. Currently, every review item comes back "tomorrow" regardless of how many times a student answers correctly. After this fix, correctly-answered items will space out to days, weeks, and eventually months.

## Root Cause

The `Memora Memory State` table only stores 3 of the 6 fields needed by the FSRS algorithm. The missing 3 fields (`state`, `step`, `last_review`) cause the system to treat every card as brand new on every review.

## Files Changed

| File | What Changes |
|------|-------------|
| `memora_admin/setup.py` | New `_ensure_fsrs_state_columns()` function adds 3 nullable columns to the partitioned table |
| `memora_admin/tasks/fsrs_processor.py` | Lookup, reconstruct, and persist all 6 FSRS fields |
| `memora_admin/api/reviews.py` | Same changes as processor (submit_reviews path) |
| `memora_memory_state.json` | Add 3 fields as `is_virtual=1` for admin display |

## Deployment Steps

1. **Deploy code** to production
2. **Run migration**: `bench migrate` (triggers `after_migrate()` which adds the 3 new columns)
3. **Restart Frappe workers**: `bench restart` (activates updated processor/API)
4. **Restart FastAPI**: `pkill -f "uvicorn fastapi_app.main:app"` (process supervisor auto-restarts)
5. **Verify**: No immediate visible change. Over the next 24-48 hours, students who review items correctly will see intervals start to grow beyond "tomorrow."

## Verification

After deployment, verify correct behavior:

```sql
-- Check new columns exist
SELECT COLUMN_NAME, COLUMN_TYPE
FROM INFORMATION_SCHEMA.COLUMNS
WHERE TABLE_SCHEMA = DATABASE()
AND TABLE_NAME = 'tabMemora Memory State'
AND COLUMN_NAME IN ('state', 'step', 'last_review');

-- After some reviews have been processed, check state distribution
SELECT state, COUNT(*) as cnt
FROM `tabMemora Memory State`
WHERE season_seq = 1
AND state IS NOT NULL
GROUP BY state;

-- Check that intervals are growing (cards in Review state should have future dates)
SELECT state, next_review, stability
FROM `tabMemora Memory State`
WHERE state = 2
AND season_seq = 1
ORDER BY stability DESC
LIMIT 10;
```

## Rollback

If issues are detected:
1. The new columns are nullable - reverting code leaves them unused
2. Old code ignores NULL values in state/step/last_review (they aren't in the SELECT)
3. No data migration to reverse
4. Simply revert the code commit and restart services

## Testing

Run from bench root:

```bash
# Frappe tests (processor + API)
cd /home/corex/aurevia-bench
bench --site x.conanacademy.com run-tests \
  --app memora_admin \
  --module memora_admin.tests.test_fsrs_card_state

# FastAPI tests (if applicable)
cd apps/memora_admin
pytest tests/ -k "fsrs" -v
```
