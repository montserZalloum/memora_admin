# API Contract: Dashboard (DashboardUser Profile)

All endpoints require `Authorization: Bearer <access_token>` header.

## GET /api/v1/profile

**Task weight**: 3 (most frequent)

### Response (200 OK)
```json
{
  "display_name": "Ahmed",
  "avatar": "avatar_1",
  "level": 5,
  "level_title": "Explorer",
  "current_xp": 5200,
  "xp_in_level": 200,
  "xp_for_next_level": 800,
  "xp_level_start": 5000,
  "xp_level_end": 6000
}
```

---

## GET /api/v1/profile/stats

**Task weight**: 2
**Optional query**: `?subject=SUBJ-00001`

### Response (200 OK)
```json
{
  "subject": null,
  "streak": 7,
  "items_learned": 142,
  "total_xp": 5200
}
```

---

## GET /api/v1/profile/activity

**Task weight**: 2
**Optional query**: `?subject=SUBJ-00001`

### Response (200 OK)
```json
{
  "subject": null,
  "week_start": "2026-02-23",
  "days": [
    {"date": "2026-02-23", "day_name": "Mon", "xp": 120},
    {"date": "2026-02-24", "day_name": "Tue", "xp": 85},
    {"date": "2026-02-25", "day_name": "Wed", "xp": 200},
    {"date": "2026-02-26", "day_name": "Thu", "xp": 0},
    {"date": "2026-02-27", "day_name": "Fri", "xp": 150},
    {"date": "2026-02-28", "day_name": "Sat", "xp": 0},
    {"date": "2026-03-01", "day_name": "Sun", "xp": 0}
  ],
  "total_xp": 555
}
```

---

## GET /api/v1/profile/mastery

**Task weight**: 1 (less frequent, 5 min server-side cache)
**Optional query**: `?subject=SUBJ-00001`

### Response (200 OK)
```json
{
  "subject": null,
  "mature": 45,
  "learning": 97
}
```

---

## GET /api/v1/wallet

**Task weight**: 1

### Response (200 OK)
```json
{
  "xp": 5200,
  "streak": 7
}
```

---

## GET /api/v1/progress

**Task weight**: 1

### Response (200 OK)
```json
[
  {
    "subject_id": "SUBJ-00001",
    "subject_name": "Mathematics",
    "percentage": 45.5,
    "completed": 50,
    "total": 110
  }
]
```

---

## Common Error Responses (all endpoints)

### 429 Rate Limited
```json
{"error": "RATE_LIMITED", "retry_after": 60}
```
**Handling**: `resp.success()` (FR-007)

### 401 Unauthorized
```json
{"detail": "Invalid credentials"}
```
**Handling**: `resp.success()` + clear token (FR-008)
