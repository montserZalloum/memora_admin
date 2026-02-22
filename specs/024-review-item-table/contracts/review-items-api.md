# API Contracts: Review Items

**Feature**: 024-review-item-table
**Date**: 2026-02-22

## Modified Endpoints

### GET /api/v1/reviews/{subject} (MODIFIED)

**Change**: Response now includes question data from Review Item table alongside existing Memory State metadata.

**Request**: Unchanged.

**Response** (updated `DueItemsResponse`):

```json
{
  "subject_id": "SUB-00001",
  "items": [
    {
      "item_id": "a40e97dd-dbae-4d4d-9a5b-7b41af641ca1",
      "stage_id": "row-abc123",
      "lesson_id": "LES-00042",
      "stage_type": "QUESTION",
      "question_text": "كم عظمة في جسم الانسان",
      "choices": ["10", "12", "14"],
      "correct_choice": 1,
      "content_json": null
    },
    {
      "item_id": "cebaff25-9064-4636-a3c4-c618427f5fef",
      "stage_id": "row-def456",
      "lesson_id": "LES-00043",
      "stage_type": "FILL_BLANK",
      "question_text": "مرحب كيفك",
      "choices": [],
      "correct_choice": null,
      "content_json": {
        "blank_from": 5,
        "blank_to": 9,
        "correct_word": "كيفك",
        "distractors": ["طيب"]
      }
    }
  ],
  "has_more": true
}
```

**Field changes in `DueItem`**:
- `stability`, `difficulty` — REMOVED (internal FSRS state, not needed by client)
- `question_text` — NEW (from Review Item)
- `choices` — NEW (non-empty choices as array)
- `correct_choice` — NEW (1-based index, null for non-MCQ)
- `content_json` — NEW (raw data for non-MCQ stages, null for QUESTION)

**Graceful degradation**: If a due item has no matching Review Item record (pre-existing Memory State for un-resaved lessons), the item is included with `question_text=null`, `choices=[]`, `correct_choice=null`, `content_json=null`. A warning is logged.

---

## Frappe API Changes

### get_due_items (MODIFIED)

**File**: `memora_admin/api/reviews.py`

**Change**: JOIN `tabMemora Review Item` to enrich due items with question data.

**SQL** (conceptual):
```sql
SELECT
    BIN_TO_UUID(ms.item_id) as item_id,
    ms.stage_id,
    ms.lesson,
    ls.stage_type,
    ri.question_text,
    ri.choice_1,
    ri.choice_2,
    ri.choice_3,
    ri.choice_4,
    ri.correct_choice,
    ri.content_json
FROM `tabMemora Memory State` ms
INNER JOIN `tabMemora Lesson Stage` ls
    ON ls.name = ms.stage_id AND ls.parent = ms.lesson
LEFT JOIN `tabMemora Review Item` ri
    ON ri.name = BIN_TO_UUID(ms.item_id)
WHERE ms.player = %(player)s
  AND ms.subject = %(subject)s
  AND ms.next_review <= %(today)s
  AND ms.season_seq = %(season_seq)s
ORDER BY ms.next_review ASC
LIMIT %(limit)s
```

**Key**: LEFT JOIN ensures items without Review Item records still appear (graceful degradation per spec edge case).

---

### sync_review_items (NEW)

**File**: `memora_admin/api/review_items.py` (new file)

**Purpose**: Called from lesson `on_update` hook to sync Review Item records.

**Signature**:
```python
def sync_review_items(lesson_doc: Document) -> dict:
    """Sync Review Item records from lesson stages.

    Returns: {"created": int, "updated": int, "deleted": int}
    """
```

**Logic**:
1. Collect all `item_id` values from non-skippable stages
2. Fetch existing Review Items for this lesson (`WHERE lesson = lesson_doc.name`)
3. Upsert items (create new, update changed)
4. Delete orphans (in DB but not in current config)
5. For deleted items, also delete Memory State records

---

### delete_review_items_for_lesson (NEW)

**File**: `memora_admin/api/review_items.py`

**Purpose**: Called from lesson `on_trash` hook.

**Signature**:
```python
def delete_review_items_for_lesson(lesson_name: str) -> int:
    """Delete all Review Items for a lesson and clean up Memory State.

    Returns: number of Review Items deleted.
    """
```

---

## Pydantic Model Changes

### DueItem (MODIFIED)

**File**: `fastapi_app/models/review.py`

```python
class DueItem(BaseModel):
    """A single item due for review, with question content."""

    item_id: str
    stage_id: str
    lesson_id: str
    stage_type: str
    question_text: str | None = None
    choices: list[str] = []
    correct_choice: int | None = None
    content_json: dict | None = None
```

**Removed fields**: `stability`, `difficulty` (were internal FSRS state exposed by mistake).

---

## DocType Schema Changes

### Memora Settings (MODIFIED)

**New field** in `memora_settings.json`:
```json
{
    "fieldname": "review_session_size",
    "fieldtype": "Int",
    "label": "Review Session Size",
    "default": "10",
    "description": "Maximum number of items per review session"
}
```

Added to `field_order` after `fsrs_weights`.

### Memora Review Item (NEW)

Standard Frappe DocType. See `data-model.md` for full schema.
