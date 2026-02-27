# API Contract: Sessions (LessonPlayer Profile)

All endpoints require `Authorization: Bearer <access_token>` header.

## POST /api/v1/sessions/start

### Request
```http
POST /api/v1/sessions/start HTTP/1.1
Authorization: Bearer <token>
Content-Type: application/json

{
  "lesson_id": "LESSON-00001",
  "subject_id": "SUBJ-00001"
}
```

### Response (200 OK)
```json
{
  "session_id": "sess_abc123def456",
  "lesson_id": "LESSON-00001"
}
```

### Response (409 Session Already Active)
If a previous session wasn't ended properly:
```json
{
  "detail": "Active session exists"
}
```
**Handling**: Call `GET /sessions/current` to recover, then end the existing session first.

---

## POST /api/v1/sessions/end

### Request
```http
POST /api/v1/sessions/end HTTP/1.1
Authorization: Bearer <token>
Content-Type: application/json

{
  "stages": [
    {
      "stage_id": "STAGE-001",
      "time_spent": 5000,
      "fail_count": 0,
      "completed_at": "2026-02-27T10:15:30",
      "metadata": {},
      "items": [
        {"item_id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890", "fail_count": 0}
      ]
    },
    {
      "stage_id": "STAGE-002",
      "time_spent": 8000,
      "fail_count": 1,
      "completed_at": "2026-02-27T10:15:38",
      "metadata": {},
      "items": []
    }
  ]
}
```

### Response (200 OK)
```json
{
  "success": true,
  "xp_awarded": 15,
  "is_replay": false,
  "streak": 7
}
```

### Response (401 Session Expired)
Session TTL (1h) expired between start and end:
```json
{"detail": "Invalid credentials"}
```
**Handling**: `resp.success()` — expected under heavy load (FR-008)

---

## GET /api/v1/sessions/current

Used for session recovery (e.g., after Locust user respawn).

### Response (200 OK)
```json
{
  "session_id": "sess_abc123def456",
  "lesson_id": "LESSON-00001",
  "subject_id": "SUBJ-00001",
  "device_id": "locust-abc123",
  "started_at": "2026-02-27T10:15:00"
}
```

### Response (404 No Active Session)
```json
{"detail": "No active session"}
```

---

## Full Lesson Flow (LessonPlayer task)

```
1. GET  /api/v1/progress                              → pick random subject
2. GET  /api/v1/progress/{subject}/topics/{topic}/lessons → pick random lesson
3. POST /api/v1/sessions/start                         → get session_id
4. sleep(random.uniform(3, 10))                        → simulate student thinking
5. POST /api/v1/sessions/end                           → submit stage results
6. GET  /api/v1/wallet                                 → verify XP awarded
```
