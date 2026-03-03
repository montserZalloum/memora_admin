# API Contracts: Practice Arena (Phase 035)

**Branch**: `035-practice-arena` | **Date**: 2026-03-02
**Base URL**: `http://127.0.0.1:8002/api/v1`
**Prior art**: Phase 025 contracts at `specs/025-practice-arena/contracts/practice-api.md`

---

## Delta from Phase 025

All 4 endpoints are already implemented. Phase 035 changes the **behavior** of responses, not the contract shapes:

| Change | Endpoint | Description |
|--------|----------|-------------|
| `all_seen_warning` semantics | `/start`, `/continue` | Now true when ANY question is a repeat (not just when ALL exhausted) |
| Proportional distribution | `/start`, `/continue` | Questions distributed across topics by content volume |

**No request/response schema changes.**

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
                        }
                    ]
                }
            ]
        }
    ]
}
```

**Notes**:
- `has_access`: true if student has subject/track grant, plan membership, or content is free
- `item_count`: Count of Review Items at each level (recursive sum for parent levels)
- When `filter=completed`: only includes hierarchy nodes where student has completed at least one lesson
- Tracks with `has_access=false` still appear (for UI display) but units/topics are empty
- Rate limit: 30/min per player

### Error Responses

| Code | Detail | When |
|------|--------|------|
| 404 | `SUBJECT_NOT_FOUND` | Subject doesn't exist |

---

## 2. POST /practice/start

Start a new practice session. Validates access, creates session, returns first batch.

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

**Validation Rules**:
- `tracks` must be non-empty
- If `len(tracks) > 1`: `units` and `topics` must be empty
- If `len(tracks) == 1` and `len(units) > 1`: `topics` must be empty
- All tracks must be accessible

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
        }
    ],
    "total_available": 150,
    "all_seen_warning": false
}
```

**`all_seen_warning` semantics (Phase 035 change)**:
- `true` if ANY question in the batch has been previously seen by this student
- `true` if wrapping around (all items exhausted, re-serving from pool)
- `false` only if every question in the batch is brand new to the student

### Error Responses

| Code | Detail | When |
|------|--------|------|
| 403 | `{"code": "NO_ACCESS", "tracks": ["TRK-00002"]}` | Inaccessible tracks |
| 422 | `{"code": "NO_ITEMS"}` | Zero items match filters |
| 429 | `{"detail": "RATE_LIMIT_EXCEEDED"}` | 10/min exceeded |

---

## 3. POST /practice/submit

Submit results for the current batch. Idempotent via batch_seq.

### Request

```json
{
    "batch_seq": 0,
    "results": [
        {"item_id": "a40e97dd-...", "is_correct": true},
        {"item_id": "b51f08ee-...", "is_correct": false}
    ]
}
```

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

### Error Responses

| Code | Detail | When |
|------|--------|------|
| 404 | `NO_ACTIVE_SESSION` | Session expired/not started |
| 409 | `{"code": "BATCH_SEQ_MISMATCH"}` | Skipped batch numbers |
| 429 | Rate limit | 30/min exceeded |

---

## 4. POST /practice/continue

Request the next batch in an active session.

### Request

Empty body — session context from Redis.

### Response 200

Same schema as `/practice/start` response. `all_seen_warning` follows same Phase 035 semantics.

### Error Responses

| Code | Detail | When |
|------|--------|------|
| 404 | `NO_ACTIVE_SESSION` | Session expired |
| 422 | `{"code": "PREVIOUS_BATCH_NOT_SUBMITTED"}` | Must submit before continuing |
| 429 | Rate limit | 30/min exceeded |

---

## Pydantic Models (Unchanged)

All models remain as implemented in Phase 025 at `fastapi_app/models/practice.py`. No schema changes needed.

---

## Redis Keys

### Existing (Phase 025)
- `memora:practice:{player_id}` — Session HASH (TTL: configurable)
- `memora:practice:hierarchy_meta:{subject_id}` — Metadata cache (TTL: 1h)

### New (Phase 035)
- `memora:dirty:review_items` — Dirty set of lesson IDs pending extraction (TTL: None, protected)
