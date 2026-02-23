# API Contracts: Practice Arena

**Branch**: `025-practice-arena` | **Date**: 2026-02-23
**Base URL**: `http://127.0.0.1:8002/api/v1`

---

## Authentication

All endpoints require JWT Bearer token via `Authorization: Bearer <token>`.
Player identity extracted from `sub` claim (PLAYER-#####).

---

## 1. GET /practice/hierarchy

Browse content hierarchy with item counts and access flags for a subject.

### Request

```
GET /api/v1/practice/hierarchy?subject_id=SUB-00001&filter=all
```

**Query Parameters**:

| Param | Type | Required | Description |
|-------|------|----------|-------------|
| subject_id | string | Yes | Subject to browse |
| filter | string | No | `"all"` (default) or `"completed"` |

### Response 200

```json
{
    "subject_id": "SUB-00001",
    "subject_title": "الرياضيات",
    "tracks": [
        {
            "track_id": "TRK-00001",
            "track_title": "الجبر",
            "has_access": true,
            "item_count": 150,
            "units": [
                {
                    "unit_id": "UNI-00001",
                    "unit_title": "المعادلات",
                    "item_count": 80,
                    "topics": [
                        {
                            "topic_id": "TOP-00001",
                            "topic_title": "المعادلات الخطية",
                            "item_count": 45
                        },
                        {
                            "topic_id": "TOP-00002",
                            "topic_title": "المعادلات التربيعية",
                            "item_count": 35
                        }
                    ]
                }
            ]
        },
        {
            "track_id": "TRK-00002",
            "track_title": "الهندسة",
            "has_access": false,
            "item_count": 200,
            "units": []
        }
    ]
}
```

**Notes**:
- `has_access`: true if student has subject/track grant, plan membership, or content is free
- `item_count`: Count of Review Items at each level (recursive sum for parent levels)
- When `filter=completed`: only includes hierarchy nodes where student has completed at least one lesson; nodes with zero completed lessons are omitted entirely
- Tracks with `has_access=false` still appear (for UI display) but units/topics are empty
- Rate limit: 30/min per player

### Response 404

```json
{"detail": "SUBJECT_NOT_FOUND"}
```

---

## 2. POST /practice/start

Start a new practice session. Validates access, creates session, returns first batch of questions.

### Request

```json
{
    "subject_id": "SUB-00001",
    "filter": "all",
    "tracks": ["TRK-00001"],
    "units": [],
    "topics": ["TOP-00001", "TOP-00002"]
}
```

**Body Schema**:

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| subject_id | string | Yes | Subject to practice |
| filter | string | Yes | `"all"` or `"completed"` |
| tracks | string[] | Yes | 1+ track IDs (multi-track disables unit/topic selection) |
| units | string[] | No | Unit filter (only if 1 track selected, default []) |
| topics | string[] | No | Topic filter (only if 1 track + 1 unit, default []) |

**Validation Rules**:
- `tracks` must be non-empty
- If `len(tracks) > 1`: `units` and `topics` must be empty
- If `len(tracks) == 1` and `len(units) > 1`: `topics` must be empty
- All tracks must be accessible (subject/track grant, plan membership, or free content)

### Response 200

```json
{
    "session_active": true,
    "batch_seq": 0,
    "questions": [
        {
            "item_id": "a40e97dd-dbae-4d4d-9a5b-7b41af641ca1",
            "stage_type": "QUESTION",
            "question_text": "كم عظمة في جسم الانسان",
            "choices": ["206", "100", "300", "150"],
            "correct_choice": 1,
            "content_json": null
        },
        {
            "item_id": "b51f08ee-ecbf-5e5e-a6c6-8c52bg752db2",
            "stage_type": "MATCHING",
            "question_text": "طابق العناصر",
            "choices": [],
            "correct_choice": null,
            "content_json": {"left": "dog", "right": "كلب"}
        }
    ],
    "total_available": 150,
    "all_seen_warning": false
}
```

**Response Fields**:

| Field | Type | Description |
|-------|------|-------------|
| session_active | bool | Always true on successful start |
| batch_seq | int | Current batch number (0 for first) |
| questions | array | Up to `practice_session_size` questions |
| total_available | int | Total matching items (across all batches) |
| all_seen_warning | bool | True if student has seen ALL matching items |

**Question Object**:

| Field | Type | Description |
|-------|------|-------------|
| item_id | string | UUID — used in submit |
| stage_type | string | QUESTION, FILL_BLANK, MATCHING, etc. |
| question_text | string? | Question or instruction text |
| choices | string[] | MCQ choices (empty for non-MCQ) |
| correct_choice | int? | 1-based correct answer index (null for non-MCQ) |
| content_json | object? | Stage-specific data (null for MCQ) |

### Response 403

```json
{"detail": "NO_ACCESS", "tracks": ["TRK-00002"]}
```

Returned when one or more selected tracks are not accessible.

### Response 422

```json
{"detail": "NO_ITEMS", "message": "No reviewable items match the selected filters"}
```

Returned when filters produce zero items (e.g., "completed only" with no completed lessons).

### Rate Limit

10/min per player. Response 429:
```json
{"detail": "RATE_LIMIT_EXCEEDED", "retry_after": 45}
```

---

## 3. POST /practice/submit

Submit results for the current batch. Idempotent via batch_seq.

### Request

```json
{
    "batch_seq": 0,
    "results": [
        {"item_id": "a40e97dd-dbae-4d4d-9a5b-7b41af641ca1", "is_correct": true},
        {"item_id": "b51f08ee-ecbf-5e5e-a6c6-8c52bg752db2", "is_correct": false}
    ]
}
```

**Body Schema**:

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| batch_seq | int | Yes | Batch number being submitted |
| results | array | Yes | Per-item results |
| results[].item_id | string | Yes | UUID from question |
| results[].is_correct | bool | Yes | Whether student answered correctly |

### Response 200

```json
{
    "accepted": true,
    "batch_seq": 0,
    "correct_count": 15,
    "total_count": 20,
    "accuracy_percent": 75.0,
    "is_duplicate": false
}
```

**Response Fields**:

| Field | Type | Description |
|-------|------|-------------|
| accepted | bool | Always true if session exists |
| batch_seq | int | Echoed batch number |
| correct_count | int | Correct answers in this batch |
| total_count | int | Total items submitted |
| accuracy_percent | float | Percentage correct |
| is_duplicate | bool | True if this batch_seq was already submitted |

**Idempotency**: If `batch_seq` was already submitted, returns `is_duplicate: true` with original counts. Practice Log is NOT updated again.

### Response 404

```json
{"detail": "NO_ACTIVE_SESSION"}
```

### Response 409

```json
{"detail": "BATCH_SEQ_MISMATCH", "expected": 1, "received": 3}
```

Returned when `batch_seq` is higher than the next expected batch (skipped batches).

### Rate Limit

30/min per player.

---

## 4. POST /practice/continue

Request the next batch of questions in an active session.

### Request

```json
{}
```

Empty body — session context from Redis.

### Response 200

```json
{
    "session_active": true,
    "batch_seq": 1,
    "questions": [...],
    "total_available": 150,
    "all_seen_warning": false
}
```

Same schema as `/practice/start` response.

### Response 404

```json
{"detail": "NO_ACTIVE_SESSION"}
```

### Response 422

```json
{"detail": "PREVIOUS_BATCH_NOT_SUBMITTED", "batch_seq": 0}
```

Returned when the previous batch hasn't been submitted yet. Student must submit or abandon before continuing.

### Rate Limit

30/min per player.

---

## Pydantic Models

### Request Models

```python
class PracticeHierarchyParams(BaseModel):
    subject_id: str
    filter: Literal["all", "completed"] = "all"

class StartPracticeRequest(BaseModel):
    subject_id: str
    filter: Literal["all", "completed"]
    tracks: list[str]  # min 1
    units: list[str] = []
    topics: list[str] = []

class PracticeResult(BaseModel):
    item_id: str
    is_correct: bool

class SubmitPracticeRequest(BaseModel):
    batch_seq: int
    results: list[PracticeResult]
```

### Response Models

```python
class PracticeTopicInfo(BaseModel):
    topic_id: str
    topic_title: str
    item_count: int

class PracticeUnitInfo(BaseModel):
    unit_id: str
    unit_title: str
    item_count: int
    topics: list[PracticeTopicInfo]

class PracticeTrackInfo(BaseModel):
    track_id: str
    track_title: str
    has_access: bool
    item_count: int
    units: list[PracticeUnitInfo]

class PracticeHierarchyResponse(BaseModel):
    subject_id: str
    subject_title: str
    tracks: list[PracticeTrackInfo]

class PracticeQuestion(BaseModel):
    item_id: str
    stage_type: str
    question_text: str | None = None
    choices: list[str] = []
    correct_choice: int | None = None
    content_json: dict | None = None

class PracticeBatchResponse(BaseModel):
    session_active: bool
    batch_seq: int
    questions: list[PracticeQuestion]
    total_available: int
    all_seen_warning: bool = False

class PracticeSubmitResponse(BaseModel):
    accepted: bool
    batch_seq: int
    correct_count: int
    total_count: int
    accuracy_percent: float
    is_duplicate: bool = False
```

---

## Redis Keys (to add to redis_keys.py)

```python
def practice_session_key(player_id: str) -> str:
    """Active practice session for a player.

    Type: HASH (subject_id, filter, tracks, units, topics, batch_seq,
               served_item_ids, accessible_lessons, created_at)
    Producers: PracticeService.start_session()
    Consumers: PracticeService.continue_session(), submit_batch()
    TTL: practice_session_ttl (default 3600s)
    """
    return f"memora:practice:{player_id}"
```

---

## Rate Limiting Configuration

Add to `_SCOPE_SETTINGS` in `deps.py`:

```python
_SCOPE_SETTINGS = {
    # ... existing
    "practice_hierarchy": "practice_hierarchy_rate_limit",
    "practice_start": "practice_start_rate_limit",
    "practice_submit": "practice_submit_rate_limit",
    "practice_continue": "practice_continue_rate_limit",
}
```

Add to `config.py`:

```python
practice_hierarchy_rate_limit: int = 30
practice_start_rate_limit: int = 10
practice_submit_rate_limit: int = 30
practice_continue_rate_limit: int = 30
```
