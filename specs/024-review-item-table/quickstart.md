# Quickstart: Review Item Table

**Feature**: 024-review-item-table

## Prerequisites

- Frappe v15 bench environment running (`bench start`)
- Redis at `redis://127.0.0.1:13000`
- FastAPI sidecar on port 8002

## After Implementation

### 1. Run Migrations

```bash
cd /home/corex/aurevia-bench
bench --site x.conanacademy.com migrate
```

This creates the `tabMemora Review Item` table and adds the `review_session_size` field to Memora Settings.

### 2. Restart Services

```bash
# Restart Frappe workers (picks up new hooks)
bench restart

# Restart FastAPI (picks up updated models/endpoints)
pkill -f "uvicorn fastapi_app.main:app"
# Wait 2-3 seconds for supervisor to restart
curl http://127.0.0.1:8002/api/v1/health/live
```

### 3. Populate Review Items

Review Items auto-populate when a teacher saves a lesson. To populate existing lessons:

```bash
# Via bench console — bulk sync all lessons
bench --site x.conanacademy.com console
```

```python
from memora_admin.api.review_items import sync_review_items
import frappe

lessons = frappe.get_all("Memora Lesson", filters={"is_published": 1}, pluck="name")
for lesson_name in lessons:
    doc = frappe.get_doc("Memora Lesson", lesson_name)
    result = sync_review_items(doc)
    if result["created"] > 0:
        print(f"{lesson_name}: {result}")
frappe.db.commit()
```

### 4. Verify

```bash
# Check Review Item count
bench --site x.conanacademy.com console
```

```python
count = frappe.db.count("Memora Review Item")
print(f"Review Items: {count}")

# Check a specific item
items = frappe.get_all("Memora Review Item", filters={"stage_type": "QUESTION"}, fields=["name", "question_text", "choice_1", "correct_choice"], limit=3)
for i in items:
    print(i)
```

### 5. Configure Session Size (Optional)

Navigate to **Memora Settings** in the admin panel and set `Review Session Size` (default: 10).

## Testing

```bash
# Run tests
cd /home/corex/aurevia-bench/apps/memora_admin
pytest fastapi_app/tests/test_review_items.py -v

# Run Frappe tests
cd /home/corex/aurevia-bench
bench --site x.conanacademy.com run-tests --module memora_admin.memora_admin.tests.test_review_item_sync
```
