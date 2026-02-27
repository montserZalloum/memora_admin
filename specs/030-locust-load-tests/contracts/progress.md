# API Contract: Progress & Hierarchy (BrowserUser Profile)

All endpoints require `Authorization: Bearer <access_token>` header.

## GET /api/v1/progress

**Used by**: DashboardUser (weight 1), LessonPlayer (step 1), BrowserUser (step 1)

### Response (200 OK)
```json
[
  {
    "subject_id": "SUBJ-00001",
    "subject_name": "Mathematics",
    "percentage": 45.5,
    "completed": 50,
    "total": 110
  },
  {
    "subject_id": "SUBJ-00002",
    "subject_name": "Science",
    "percentage": 20.0,
    "completed": 10,
    "total": 50
  }
]
```

---

## GET /api/v1/progress/{subject}/tracks

**Locust name**: `/api/v1/progress/[subject]/tracks`

### Response (200 OK)
```json
[
  {
    "track_id": "TRK-00001",
    "completed": 20,
    "total": 40,
    "percentage": 50.0,
    "unlocked": true
  }
]
```

---

## GET /api/v1/progress/{subject}/tracks/{track}

**Locust name**: `/api/v1/progress/[subject]/tracks/[track]`

### Response (200 OK)
```json
{
  "track_id": "TRK-00001",
  "completed": 20,
  "total": 40,
  "percentage": 50.0,
  "unlocked": true,
  "units": [
    {
      "unit_id": "UNIT-00001",
      "completed": 10,
      "total": 15,
      "percentage": 66.7,
      "unlocked": true
    }
  ]
}
```

---

## GET /api/v1/progress/{subject}/tracks/{track}/units/{unit}

**Locust name**: `/api/v1/progress/[subject]/tracks/[track]/units/[unit]`

### Response (200 OK)
```json
{
  "unit_id": "UNIT-00001",
  "completed": 10,
  "total": 15,
  "percentage": 66.7,
  "unlocked": true,
  "topics": [
    {
      "topic_id": "TOPIC-00001",
      "completed": 5,
      "total": 5,
      "percentage": 100.0,
      "unlocked": true
    }
  ]
}
```

---

## GET /api/v1/progress/{subject}/topics/{topic}/lessons

**Locust name**: `/api/v1/progress/[subject]/topics/[topic]/lessons`
**Used by**: LessonPlayer (step 2 — pick a lesson to play)

### Response (200 OK)
```json
{
  "topic_id": "TOPIC-00001",
  "total": 5,
  "completed": 3,
  "percentage": 60.0,
  "lessons": [
    {"lesson_id": "LESSON-00001", "bit_index": 0, "completed": true},
    {"lesson_id": "LESSON-00002", "bit_index": 1, "completed": true},
    {"lesson_id": "LESSON-00003", "bit_index": 2, "completed": true},
    {"lesson_id": "LESSON-00004", "bit_index": 3, "completed": false},
    {"lesson_id": "LESSON-00005", "bit_index": 4, "completed": false}
  ]
}
```

---

## Full Browse Flow (BrowserUser task)

```
1. GET /api/v1/progress                                              → pick subject
2. GET /api/v1/progress/{subject}/tracks                             → list tracks
3. GET /api/v1/progress/{subject}/tracks/{track}                     → pick track, get units
4. GET /api/v1/progress/{subject}/tracks/{track}/units/{unit}        → get topics
   (stop drilling if empty tracks/units returned)
```

**Edge Case**: If the tracks list is empty, stop drilling down and move to next iteration (per spec edge case).
