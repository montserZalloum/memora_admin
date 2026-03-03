# Quickstart: Practice Arena (Phase 035)

**Branch**: `035-practice-arena` | **Date**: 2026-03-02

---

## What's Changing

Phase 035 is a **gap-fix** for the existing Practice Arena (Phase 025). No new endpoints, tables, or models. Three behavioral changes:

1. **Dirty-set extraction** — Review Item extraction moves from synchronous `on_lesson_save` to a Redis dirty-set consumed every 2 minutes
2. **Proportional topic distribution** — Questions are distributed across topics by content volume instead of simple priority ordering
3. **`all_seen_warning` fix** — Flag is now true when ANY batch question is a repeat, not just when all items are exhausted

---

## Files to Modify

### Frappe Side

| File | Change |
|------|--------|
| `memora_admin/events/review_item_sync.py` | Switch from sync to dirty-set SADD + immediate delete for non-reviewable |
| `memora_admin/tasks/sync.py` | Add `sync_dirty_review_items()` consumer function |
| `memora_admin/hooks.py` | Add `*/2 * * * *` scheduler entry |

### FastAPI Side

| File | Change |
|------|--------|
| `fastapi_app/core/redis_keys.py` | Add `dirty_review_items_key()` builder |
| `fastapi_app/core/constants.py` | Add `DIRTY_REVIEW_ITEMS_KEY` constant |
| `fastapi_app/services/practice.py` | Proportional distribution + all_seen_warning fix |

### Tests

| File | Change |
|------|--------|
| `fastapi_app/tests/test_practice.py` | Add tests for new behavior |
| `memora_admin/memora_admin/doctype/memora_review_item/test_memora_review_item.py` | Add dirty-set test if needed |

---

## Development Steps

### 1. Add Redis Key

In `fastapi_app/core/redis_keys.py`:
```python
def dirty_review_items_key() -> str:
    """Dirty set of lesson IDs pending Review Item extraction.
    Type: SET of lesson names
    TTL: None (protected)
    """
    return "memora:dirty:review_items"
```

In `fastapi_app/core/constants.py`:
```python
from fastapi_app.core.redis_keys import dirty_review_items_key
DIRTY_REVIEW_ITEMS_KEY = dirty_review_items_key()
```

### 2. Modify Producer (review_item_sync.py)

Replace synchronous `sync_review_items()` call with `SADD` to dirty set.
Keep immediate deletion for `is_reviewable=0` lessons and `on_lesson_trash`.

### 3. Add Consumer (sync.py)

Add `sync_dirty_review_items()` following the `sync_dirty_progress()` pattern:
- `SMEMBERS` → process each → `SREM` on success → leave on failure

### 4. Add Scheduler Entry (hooks.py)

```python
"*/2 * * * *": [
    "memora_admin.tasks.sync.sync_dirty_review_items",
],
```

### 5. Fix `_select_questions()` (practice.py)

- Add per-topic COUNT + proportional quota calculation
- Thread `any_repeat` boolean from SQL results back to callers
- Update `start_session()` and `continue_session()` to use `any_repeat`

### 6. Test

```bash
# Run practice tests
cd /home/corex/aurevia-bench/apps/memora_admin
python -m pytest fastapi_app/tests/test_practice.py -v

# Run review item tests
cd /home/corex/aurevia-bench
bench --site x.conanacademy.com run-tests \
    --app memora_admin \
    --module "memora_admin.doctype.memora_review_item"
```

---

## Verification

```bash
# 1. Verify dirty set key exists after lesson save
redis-cli -p 13001 SMEMBERS memora:dirty:review_items

# 2. Verify sync consumer processes and clears
# Wait 2 minutes, then:
redis-cli -p 13001 SCARD memora:dirty:review_items  # Should be 0

# 3. Verify all_seen_warning on API
curl -H "Authorization: Bearer $TOKEN" \
    "http://127.0.0.1:8002/api/v1/practice/start" \
    -d '{"subject_id":"SUB-00001","filter":"all","tracks":["TRK-00001"]}'
# Check all_seen_warning in response

# 4. Health check
curl http://127.0.0.1:8002/api/v1/health/live
```
