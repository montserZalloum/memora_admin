# API Contract: Challenge Hub

**Base URL**: `http://127.0.0.1:8002/api/v1`
**Auth**: Bearer JWT (same as all game API endpoints)
**Rate Limiting**: Per-player, per-scope

---

## 1. Challenge Hierarchy

### GET `/challenge/hierarchy`

Returns subjects available for Challenge Hub (same as player's plan subjects).

**Auth**: Required
**Rate Limit**: `ch_hierarchy` (10 req/min)
**Dependencies**: `ActiveSeasonDep`

**Response** `200 OK`:
```json
{
  "subjects": [
    {
      "subject_id": "SUBJ-00001",
      "subject_name": "Mathematics Grade 5",
      "total_topics": 45,
      "stamped_topics": 12,
      "total_challenge_xp": 350
    }
  ]
}
```

**Errors**:
- `403 SEASON_EXPIRED` — season ended

---

### GET `/challenge/hierarchy/{subject_id}`

Returns tracks, units, and topics for a subject with challenge states.

**Auth**: Required
**Rate Limit**: `ch_hierarchy` (10 req/min)
**Dependencies**: `ActiveSeasonDep`

**Response** `200 OK`:
```json
{
  "subject_id": "SUBJ-00001",
  "tracks": [
    {
      "track_id": "TRK-00001",
      "track_name": "Algebra",
      "has_access": true,
      "units": [
        {
          "unit_id": "UNIT-00001",
          "unit_name": "Linear Equations",
          "topics": [
            {
              "topic_id": "TOPIC-00001",
              "topic_name": "Basic Addition",
              "state": "stamped",
              "mcq_count": 25,
              "best_score_pct": 92.0,
              "best_passing_pct": 92.0,
              "total_xp": 115,
              "attempt_count": 3,
              "normal_path_complete": true,
              "has_access": true
            },
            {
              "topic_id": "TOPIC-00002",
              "topic_name": "Subtraction",
              "state": "open",
              "mcq_count": 18,
              "best_score_pct": null,
              "best_passing_pct": null,
              "total_xp": 0,
              "attempt_count": 0,
              "normal_path_complete": true,
              "has_access": true
            },
            {
              "topic_id": "TOPIC-00003",
              "topic_name": "Mixed Operations",
              "state": "locked",
              "mcq_count": 30,
              "best_score_pct": null,
              "best_passing_pct": null,
              "total_xp": 0,
              "attempt_count": 0,
              "normal_path_complete": false,
              "has_access": true,
              "lock_reason": "NORMAL_PATH_INCOMPLETE"
            }
          ]
        }
      ]
    },
    {
      "track_id": "TRK-00002",
      "track_name": "Geometry",
      "has_access": false,
      "units": []
    }
  ]
}
```

**Topic States**:
- `"stamped"` — passed ≥ threshold, can replay
- `"open"` — all conditions met, can start/retry
- `"locked"` — one or more conditions not met

**Lock Reasons** (only present when `state == "locked"`):
- `"NO_ACCESS"` — student doesn't have content access (condition 1)
- `"NORMAL_PATH_INCOMPLETE"` — topic lessons not completed on normal path (condition 2)
- `"PREVIOUS_NOT_STAMPED"` — previous topic not stamped in Challenge Hub (condition 3)

**Notes**:
- Topics with `mcq_count == 0` are hidden (not included in response)
- Empty topics are auto-stamped when predecessor is stamped (computed server-side, not returned)
- `units` array is empty for locked tracks (`has_access == false`)

**Errors**:
- `403 SEASON_EXPIRED`
- `404 SUBJECT_NOT_FOUND` — subject not in player's plan

---

## 2. Challenge Attempt

### POST `/challenge/attempt`

Submit a completed challenge attempt.

**Auth**: Required
**Rate Limit**: `ch_attempt` (30 req/min)
**Dependencies**: `ActiveSeasonDep`

**Request Body**:
```json
{
  "topic_id": "TOPIC-00001",
  "attempt_key": "client-generated-uuid-v4",
  "total_questions": 25,
  "correct_count": 18,
  "time_spent": 420,
  "questions": [
    {
      "item_id": "550e8400-e29b-41d4-a716-446655440000",
      "correct": true,
      "time_spent": 12,
      "chosen_answer": 2
    },
    {
      "item_id": "550e8400-e29b-41d4-a716-446655440001",
      "correct": false,
      "time_spent": 25,
      "chosen_answer": 3
    }
  ]
}
```

**Field Validation**:
- `topic_id`: required, must exist in hierarchy
- `attempt_key`: required, UUID v4, used for idempotency (5-min window)
- `total_questions`: required, ≥ 1, must match `len(questions)`
- `correct_count`: required, 0 ≤ value ≤ total_questions, must match sum of `correct == true`
- `time_spent`: required, ≥ 0 (seconds)
- `questions`: required, min_length=1, each item has `item_id`, `correct`, `time_spent`, `chosen_answer`
- `questions[].chosen_answer`: 1-4

**Response** `200 OK`:
```json
{
  "attempt_number": 3,
  "score_pct": 72.0,
  "passed": true,
  "stamped": true,
  "xp_earned": 15,
  "total_topic_xp": 90,
  "best_score_pct": 72.0,
  "best_passing_pct": 72.0,
  "is_new_best": true,
  "next_topic": {
    "topic_id": "TOPIC-00003",
    "state": "open"
  }
}
```

**Response Fields**:
- `attempt_number`: sequential attempt count for this topic
- `score_pct`: this attempt's score
- `passed`: whether score ≥ pass_threshold
- `stamped`: whether topic is now stamped (may have been stamped before)
- `xp_earned`: Challenge XP delta earned this attempt (0 if no improvement)
- `total_topic_xp`: cumulative XP for this topic
- `best_score_pct`: updated best overall score
- `best_passing_pct`: updated best passing score (null if never passed)
- `is_new_best`: whether this attempt set a new best score
- `next_topic`: next topic info if this stamp unlocked it (null if not applicable)

**Errors**:
- `400 VALIDATION_ERROR` — invalid request body
- `403 SEASON_EXPIRED`
- `403 TOPIC_LOCKED` — topic is locked (access, normal path, or sequence)
- `409 DUPLICATE_ATTEMPT` — attempt_key already processed (returns cached response)

**Side Effects**:
1. Updates `Memora Challenge Progress` (Redis + dirty set)
2. Creates `Memora Challenge Attempt` + details (via background flush)
3. Updates Challenge leaderboard ZSET (if XP delta > 0)
4. Pushes question results to `memora:buffer:interactions` for FSRS

---

## 3. Challenge Leaderboard

### GET `/challenge/leaderboard`

Returns Challenge XP leaderboard for student's plan.

**Auth**: Required
**Rate Limit**: `ch_leaderboard` (10 req/min)
**Dependencies**: `ActiveSeasonDep`

**Query Parameters**:
- `subject_id` (optional): Filter by subject. Omit for plan-level (all subjects).
- `limit` (optional, default 20, max 100): Number of top players.
- `offset` (optional, default 0, max 1000): Pagination offset.

**Response** `200 OK`:
```json
{
  "subject_id": null,
  "entries": [
    {
      "rank": 1,
      "player_id": "PLAYER-00001",
      "display_name": "أحمد",
      "xp": 1250,
      "avatar": "avatar_url",
      "is_me": false
    },
    {
      "rank": 2,
      "player_id": "PLAYER-00002",
      "display_name": "سارة",
      "xp": 980,
      "avatar": null,
      "is_me": true
    }
  ],
  "total_players": 156
}
```

**Errors**:
- `403 SEASON_EXPIRED`

---

### GET `/challenge/leaderboard/me`

Returns student's own Challenge XP rank with neighbors.

**Auth**: Required
**Rate Limit**: `ch_leaderboard` (10 req/min)
**Dependencies**: `ActiveSeasonDep`

**Query Parameters**:
- `subject_id` (optional): Filter by subject. Omit for plan-level.

**Response** `200 OK`:
```json
{
  "rank": 15,
  "xp": 350,
  "xp_to_next": 30,
  "neighbors": [
    {
      "rank": 13,
      "player_id": "PLAYER-00050",
      "display_name": "محمد",
      "xp": 400,
      "avatar": null,
      "is_me": false
    },
    {
      "rank": 14,
      "player_id": "PLAYER-00033",
      "display_name": "ليلى",
      "xp": 380,
      "avatar": null,
      "is_me": false
    },
    {
      "rank": 15,
      "player_id": "PLAYER-00002",
      "display_name": "سارة",
      "xp": 350,
      "avatar": "avatar_url",
      "is_me": true
    },
    {
      "rank": 16,
      "player_id": "PLAYER-00077",
      "display_name": "عمر",
      "xp": 340,
      "avatar": null,
      "is_me": false
    }
  ],
  "total_players": 156
}
```

**Unranked** (no Challenge XP yet):
```json
{
  "rank": null,
  "xp": 0,
  "xp_to_next": null,
  "neighbors": [],
  "total_players": 156
}
```

**Errors**:
- `403 SEASON_EXPIRED`

---

## 4. Error Response Format

All errors follow the standard format:

```json
{
  "detail": {
    "code": "ERROR_CODE",
    "message": "Human-readable description"
  }
}
```

## 5. Rate Limit Scopes

| Scope | Limit | Window | Endpoints |
|-------|-------|--------|-----------|
| `ch_hierarchy` | 10 req | 60s | GET hierarchy, GET hierarchy/{subject} |
| `ch_attempt` | 30 req | 60s | POST attempt |
| `ch_leaderboard` | 10 req | 60s | GET leaderboard, GET leaderboard/me |
