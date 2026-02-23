# Quickstart: Practice Arena

**Branch**: `025-practice-arena` | **Date**: 2026-02-23

---

## Prerequisites

- Frappe v15 bench environment running
- FastAPI sidecar on port 8002
- Redis at `redis://127.0.0.1:13000`
- At least one subject with published lessons containing reviewable stages

---

## 1. Create the Practice Log Table

Run the DDL migration (one-time setup):

```bash
bench --site x.conanacademy.com console
```

```python
frappe.db.sql("""
CREATE TABLE IF NOT EXISTS `tabMemora Practice Log` (
    `name` BIGINT AUTO_INCREMENT,
    `player_id` VARCHAR(140) NOT NULL,
    `item_id` VARCHAR(36) NOT NULL,
    `first_seen_at` DATETIME NOT NULL,
    `last_seen_at` DATETIME NOT NULL,
    `last_result` ENUM('Correct', 'Incorrect') NOT NULL,
    `attempt_count` INT UNSIGNED NOT NULL DEFAULT 1,
    `correct_count` INT UNSIGNED NOT NULL DEFAULT 0,
    PRIMARY KEY (`name`),
    UNIQUE KEY `uq_player_item` (`player_id`, `item_id`),
    KEY `idx_item_id` (`item_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
""")
frappe.db.commit()
```

## 2. Add Settings Fields

Add `practice_session_size` (Int, default 20) and `practice_session_ttl` (Int, default 3600) to `Memora Settings` DocType via schema update.

## 3. Verify Review Items Exist

```bash
bench --site x.conanacademy.com console
```

```python
count = frappe.db.count("Memora Review Item")
print(f"Review Items: {count}")
# Should be > 0 if lessons have been saved since phase 024
```

If zero, trigger extraction for all reviewable lessons:

```python
lessons = frappe.get_all("Memora Lesson", filters={"is_reviewable": 1}, pluck="name")
for lesson_name in lessons:
    doc = frappe.get_doc("Memora Lesson", lesson_name)
    from memora_admin.api.review_items import sync_review_items
    result = sync_review_items(doc)
    if result["created"]:
        print(f"{lesson_name}: created {result['created']} items")
frappe.db.commit()
```

## 4. Test the FastAPI Endpoints

After restarting FastAPI:

```bash
# Kill and let supervisor restart
pkill -f "uvicorn fastapi_app.main:app"
sleep 3

# Health check
curl http://127.0.0.1:8002/api/v1/health/live

# Browse hierarchy (requires auth token)
curl -H "Authorization: Bearer $TOKEN" \
  "http://127.0.0.1:8002/api/v1/practice/hierarchy?subject_id=SUB-00001&filter=all"

# Start practice session
curl -X POST -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"subject_id":"SUB-00001","filter":"all","tracks":["TRK-00001"]}' \
  http://127.0.0.1:8002/api/v1/practice/start

# Submit results
curl -X POST -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"batch_seq":0,"results":[{"item_id":"UUID","is_correct":true}]}' \
  http://127.0.0.1:8002/api/v1/practice/submit

# Continue with next batch
curl -X POST -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{}' \
  http://127.0.0.1:8002/api/v1/practice/continue
```

## 5. Verify Practice Log

```bash
bench --site x.conanacademy.com console
```

```python
rows = frappe.db.sql("""
    SELECT player_id, item_id, attempt_count, correct_count, last_result
    FROM `tabMemora Practice Log`
    ORDER BY last_seen_at DESC
    LIMIT 10
""", as_dict=True)
for r in rows:
    print(f"{r.player_id} | {r.item_id[:8]}... | attempts={r.attempt_count} correct={r.correct_count}")
```

---

## Key Files

| Component | Path |
|-----------|------|
| Practice endpoints | `fastapi_app/api/v1/endpoints/practice.py` |
| Practice service | `fastapi_app/services/practice.py` |
| Pydantic models | `fastapi_app/models/practice.py` |
| Redis key builder | `fastapi_app/core/redis_keys.py` (add `practice_session_key`) |
| Rate limit config | `fastapi_app/core/config.py` (add practice limits) |
| Dependencies | `fastapi_app/api/deps.py` (add practice deps) |
| Review Item sync | `memora_admin/api/review_items.py` (gap-filling) |
| Review Item hook | `memora_admin/events/review_item_sync.py` |
| Settings DocType | `memora_admin/doctype/memora_settings/` |
| Hooks | `memora_admin/hooks.py` |
| DDL migration | `memora_admin/setup.py` (add Practice Log table creation) |

---

## Running Tests

```bash
# FastAPI practice endpoint tests
cd /home/corex/aurevia-bench/apps/memora_admin
python -m pytest fastapi_app/tests/test_practice.py -v

# Frappe-side review item gap-fill tests
cd /home/corex/aurevia-bench
bench --site x.conanacademy.com run-tests \
  --module memora_admin.memora_admin.doctype.memora_review_item.test_memora_review_item
```
