# API Contracts: Live Challenges

**Feature**: `037-live-challenges` | **Date**: 2026-03-07
**Base URL**: `http://127.0.0.1:8002/api/v1`

All endpoints require JWT authentication via `Authorization: Bearer <token>` header unless noted otherwise.

---

## 1. GET `/live-challenge/{event_id}`

**Description**: Get public event details (no correct answers).
**Auth**: `CurrentUser`
**Rate limit**: Standard

### Request

Path parameter: `event_id` (string) — Live Challenge Event name (e.g., `LC-00001`)

### Response 200

```json
{
  "event_id": "LC-00001",
  "event_name": "Weekly Math Quiz",
  "description": "<p>Test your algebra skills!</p>",
  "status": "Waiting",
  "scheduled_start": "2026-03-07 14:00:00",
  "exam_start_ts": "2026-03-07 14:03:00",
  "exam_end_ts": "2026-03-07 14:13:00",
  "waiting_room_duration": 180,
  "exam_duration": 10,
  "enable_question_timer": true,
  "question_time_limit": 30,
  "capacity": 500,
  "current_count": 142,
  "is_paid": false,
  "show_correct_answers": true,
  "show_student_rank": true,
  "participation_xp": 50,
  "first_place_xp": 500,
  "second_place_xp": 300,
  "third_place_xp": 100,
  "default_xp": 25,
  "question_count": 20,
  "eligible_plans": ["PLAN-00001", "PLAN-00003"],
  "has_joined": true,
  "has_submitted": false
}
```

**Notes**:
- `question_count` is the number of questions (not the questions themselves)
- `has_joined` and `has_submitted` are player-specific flags
- No correct answers are exposed
- `eligible_plans` is empty array if no plan restriction

### Response 404

```json
{"detail": "EVENT_NOT_FOUND"}
```

---

## 2. POST `/live-challenge/{event_id}/join`

**Description**: Join an event's waiting room. Validates eligibility and capacity.
**Auth**: `CurrentUser`
**Rate limit**: `require_rate_limit("lc_join")` — 5 per minute

### Request

Path parameter: `event_id` (string)

No request body.

### Response 200

```json
{
  "joined": true,
  "event_id": "LC-00001",
  "position": 143,
  "waiting_room_duration": 180,
  "countdown_remaining": 45,
  "ws_url": "/api/v1/live-challenge/LC-00001/ws?token=<jwt>"
}
```

- `position`: the student's position number (1-indexed)
- `countdown_remaining`: seconds left until exam starts (server-authoritative)
- `ws_url`: WebSocket URL for receiving the start signal

### Response 409

```json
{"detail": "ALREADY_JOINED"}
```

### Response 403

```json
{"detail": "PLAN_NOT_ELIGIBLE"}
```

### Response 422

```json
{"detail": "CAPACITY_FULL"}
```

### Response 400

```json
{"detail": "EVENT_NOT_JOINABLE"}
```

Returned when event is not in Waiting or Active status.

---

## 3. POST `/live-challenge/{event_id}/submit`

**Description**: Submit all answers for grading. Returns score immediately.
**Auth**: `CurrentUser`
**Rate limit**: `require_rate_limit("lc_submit")` — 2 per minute

### Request

Path parameter: `event_id` (string)

```json
{
  "answers": [
    {"question_idx": 0, "selected": "A"},
    {"question_idx": 1, "selected": "C"},
    {"question_idx": 2, "selected": null},
    {"question_idx": 3, "selected": "B"}
  ]
}
```

- `question_idx`: 0-based index matching the question order
- `selected`: "A", "B", "C", "D", or null (unanswered)
- Array length must match total question count

### Response 200

```json
{
  "score": 75.0,
  "correct_count": 15,
  "total_questions": 20,
  "submitted_at": "2026-03-07 14:08:32",
  "corrections": [
    {"question_idx": 1, "selected": "C", "correct_answer": "B"},
    {"question_idx": 2, "selected": null, "correct_answer": "D"},
    {"question_idx": 7, "selected": "A", "correct_answer": "C"},
    {"question_idx": 12, "selected": "D", "correct_answer": "A"},
    {"question_idx": 18, "selected": "B", "correct_answer": "D"}
  ]
}
```

- `corrections` is included ONLY if `show_correct_answers` is enabled on the event. Otherwise it is `null`.
- `score` is always returned immediately.

### Response 409

```json
{"detail": "ALREADY_SUBMITTED"}
```

### Response 400

```json
{"detail": "EVENT_NOT_ACTIVE"}
```

### Response 403

```json
{"detail": "NOT_A_PARTICIPANT"}
```

---

## 4. GET `/live-challenge/{event_id}/result`

**Description**: Get the student's own result and rank.
**Auth**: `CurrentUser`

### Request

Path parameter: `event_id` (string)

### Response 200

```json
{
  "event_id": "LC-00001",
  "event_name": "Weekly Math Quiz",
  "score": 75.0,
  "correct_count": 15,
  "total_questions": 20,
  "rank": 42,
  "total_participants": 487,
  "xp_awarded": 75,
  "submitted_at": "2026-03-07 14:08:32",
  "corrections": [
    {"question_idx": 1, "selected": "C", "correct_answer": "B"}
  ]
}
```

- `rank` is `null` if event hasn't ended yet (leaderboard not computed)
- `xp_awarded` is `null` if XP hasn't been distributed yet
- `corrections` follows same rules as submit response

### Response 404

```json
{"detail": "NO_PARTICIPATION"}
```

---

## 5. GET `/live-challenge/{event_id}/leaderboard`

**Description**: Get top 20 leaderboard after event ends.
**Auth**: `CurrentUser`

### Request

Path parameter: `event_id` (string)

### Response 200

```json
{
  "event_id": "LC-00001",
  "event_name": "Weekly Math Quiz",
  "status": "Ended",
  "leaderboard": [
    {"rank": 1, "player": "PLAYER-00042", "display_name": "Ahmed", "score": 100.0},
    {"rank": 1, "player": "PLAYER-00099", "display_name": "Sara", "score": 100.0},
    {"rank": 3, "player": "PLAYER-00017", "display_name": "Omar", "score": 95.0}
  ],
  "my_rank": 42,
  "my_score": 75.0,
  "total_participants": 487
}
```

- `my_rank` and `my_score` are included if `show_student_rank` is enabled, otherwise `null`
- Leaderboard contains top 20 entries only

### Response 400

```json
{"detail": "EVENT_NOT_ENDED"}
```

---

## 6. WebSocket `/live-challenge/{event_id}/ws`

**Description**: Waiting room countdown and start signal.
**Auth**: JWT via query parameter `token`

### Connection

```
ws://127.0.0.1:8002/api/v1/live-challenge/{event_id}/ws?token=<jwt>
```

Authentication is performed BEFORE accepting the WebSocket connection (same pattern as notifications endpoint).

### Server -> Client Messages

**Countdown update** (sent periodically during Waiting Room):
```json
{
  "type": "countdown",
  "remaining": 45,
  "participant_count": 234
}
```

**Exam start** (sent once when Waiting -> Active transition occurs):
```json
{
  "type": "exam_start",
  "exam_end_ts": "2026-03-07 14:13:00",
  "total_questions": 20,
  "enable_question_timer": true,
  "question_time_limit": 30,
  "questions": [
    {
      "idx": 0,
      "question_text": "What is 2+2?",
      "option_a": "3",
      "option_b": "4",
      "option_c": "5",
      "option_d": "6"
    }
  ]
}
```

- `questions` array does NOT include `correct_answer`
- `exam_end_ts` is server-authoritative

**Event ended** (sent if event ends while student is connected):
```json
{
  "type": "event_ended"
}
```

### Client -> Server Messages

No client-to-server messages required. The WebSocket is server-push only. The connection is kept alive by the WebSocket protocol's ping/pong mechanism.

### Disconnection

If the client disconnects, they can reconnect at any time while the event is in Waiting or Active status. On reconnect during Active, the server sends the `exam_start` message with current questions (so the client can resume).

---

## 7. Admin API (Frappe Whitelist)

These endpoints are served by Frappe (port 8000), not FastAPI. They use Frappe's session-based auth.

### GET `memora_admin.api.live_challenge.get_dashboard`

**Description**: Get admin dashboard data for an event.
**Auth**: System Manager role

**Request params**: `event_id` (string)

**Response** (Active event):
```json
{
  "status": "Active",
  "participant_count": 487,
  "submitted_count": 234,
  "still_taking_count": 253,
  "time_remaining": 312,
  "exam_end_ts": "2026-03-07 14:13:00"
}
```

**Response** (Ended event):
```json
{
  "status": "Ended",
  "participant_count": 487,
  "submitted_count": 478,
  "completion_rate": 98.2,
  "average_score": 67.3,
  "highest_score": 100.0,
  "leaderboard": [...]
}
```

### POST `memora_admin.api.live_challenge.import_review_items`

**Description**: Import questions from Memora Review Items into an event.
**Auth**: System Manager role

**Request params**: `event_id` (string), `review_item_ids` (list of strings)

**Response**:
```json
{
  "imported_count": 5,
  "questions": [...]
}
```
