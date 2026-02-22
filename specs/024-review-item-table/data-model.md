# Data Model: Review Item Table

**Feature**: 024-review-item-table
**Date**: 2026-02-22

## Entities

### Memora Review Item (NEW)

A denormalized record storing review question data for fast batch retrieval during spaced repetition sessions. One row per `item_id` (the same UUID used in `config_json` and `Memora Memory State`).

**DocType**: `Memora Review Item`
**Table**: `tabMemora Review Item`
**Autoname**: `field:item_id` (UUID string = Frappe `name` column)

| Field | Type | Required | Default | Description |
|-------|------|----------|---------|-------------|
| `item_id` | Data | Yes | — | UUID string from config_json. Primary key via autoname. |
| `subject` | Link (Memora Subject) | Yes | — | Denormalized hierarchy reference |
| `track` | Link (Memora Track) | Yes | — | Denormalized hierarchy reference |
| `unit` | Link (Memora Unit) | Yes | — | Denormalized hierarchy reference |
| `topic` | Link (Memora Topic) | Yes | — | Denormalized hierarchy reference |
| `lesson` | Link (Memora Lesson) | Yes | — | Parent lesson |
| `stage_id` | Data | Yes | — | Lesson Stage child table row name |
| `stage_type` | Link (Memora Lesson Stage Settings) | Yes | — | Stage type (QUESTION, FILL_BLANK, MATCHING, etc.) |
| `question_text` | Small Text | No | — | Question text (QUESTION: question field; FILL_BLANK: sentence; MATCHING: instruction) |
| `choice_1` | Small Text | No | — | First choice text (QUESTION stages only) |
| `choice_2` | Small Text | No | — | Second choice text (QUESTION stages only) |
| `choice_3` | Small Text | No | — | Third choice text (may be empty if < 4 choices) |
| `choice_4` | Small Text | No | — | Fourth choice text (may be empty if < 4 choices) |
| `correct_choice` | Int | No | — | 1-based index of correct choice (QUESTION stages only) |
| `content_json` | Code (JSON) | No | — | Raw item data for non-MCQ stages (FILL_BLANK, MATCHING) |

**Indexes**:
- Primary: `name` (= `item_id` UUID string) — batch IN-clause lookups
- `lesson` — cascade deletion on lesson delete
- `subject` — filtering by subject (FR-007)
- `stage_id` — cascade deletion on stage removal

**Validation Rules**:
- `item_id` must be a valid UUID string
- `correct_choice` must be 1–4 when populated
- At least one of (`choice_1`, `content_json`) must be non-null

**State Transitions**: None — this is a denormalization cache, not a stateful entity.

### Memora Settings (MODIFIED)

**New Field**:

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `review_session_size` | Int | 10 | Max items per review session |

Added under the FSRS section.

## Relationships

```
Memora Lesson (1) ──has-many──> Memora Lesson Stage (N) [child table]
Memora Lesson Stage (1) ──derives──> Memora Review Item (N) [per item_id in config_json]
Memora Review Item (1) <──shares-key──> Memora Memory State (N) [same item_id, per player]
```

```
Memora Subject ─┐
Memora Track  ──┤
Memora Unit   ──┼── denormalized into ──> Memora Review Item
Memora Topic  ──┤
Memora Lesson ──┘
```

## Data Flow

### Write Path (Admin saves lesson)

```
Teacher saves lesson
  → on_update hook fires
  → sync_review_items(lesson_doc)
    → For each non-skippable stage:
      → Parse config_json
      → Extract items (dispatch by stage_type)
      → Upsert Review Item records
    → Delete orphaned Review Items (items in DB but not in current stages)
    → Delete orphaned Memory State records (for deleted items)
```

### Read Path (Student review session)

```
Student requests review
  → get_due_items(player, subject) [existing Frappe API]
    → Query Memory State for due item_ids (existing)
    → JOIN tabMemora Review Item to enrich with question data (NEW)
    → Return: item_id, stage_type, question_text, choices[], correct_choice, content_json
```

### Delete Path (Admin deletes lesson)

```
Teacher deletes lesson
  → on_trash hook fires
  → delete_review_items_for_lesson(lesson_name)
    → DELETE FROM tabMemora Review Item WHERE lesson = %(lesson)s
    → DELETE FROM tabMemora Memory State WHERE lesson = %(lesson)s AND season_seq = ...
```

## Item Extraction by Stage Type

### QUESTION
```python
# Input config_json:
{"question": "كم عظمة في جسم الانسان", "answers": [
    {"text": "10", "is_correct": true, "item_id": "UUID-1"},
    {"text": "12", "is_correct": false, "item_id": "UUID-2"},
    {"text": "14", "is_correct": false, "item_id": "UUID-3"}
]}

# Output Review Items (one per answer/item_id):
# For UUID-1: question_text="كم عظمة...", choice_1="10", choice_2="12", choice_3="14", correct_choice=1
# For UUID-2: question_text="كم عظمة...", choice_1="10", choice_2="12", choice_3="14", correct_choice=1
# For UUID-3: question_text="كم عظمة...", choice_1="10", choice_2="12", choice_3="14", correct_choice=1
```

### FILL_BLANK
```python
# Input config_json:
{"text": "مرحب كيفك", "blanks": [
    {"from": 5, "to": 9, "item_id": "UUID-4"}
], "distractors": ["طيب"]}

# Output Review Item:
# item_id=UUID-4, stage_type=FILL_BLANK, question_text="مرحب كيفك"
# content_json={"blank_from": 5, "blank_to": 9, "correct_word": "كيفك", "distractors": ["طيب"]}
```

### MATCHING
```python
# Input config_json:
{"pairs": [{"left": "s", "right": "s", "item_id": "UUID-5"}]}

# Output Review Item:
# item_id=UUID-5, stage_type=MATCHING, question_text=instruction
# content_json={"left": "s", "right": "s"}
```
