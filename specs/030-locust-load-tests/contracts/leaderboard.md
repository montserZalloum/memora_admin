# API Contract: Leaderboard (LeaderboardChecker Profile)

All endpoints require `Authorization: Bearer <access_token>` header.

## GET /api/v1/leaderboard/{type}

**Locust name**: `/api/v1/leaderboard/[type]`
**Types**: `daily`, `weekly`
**Optional query**: `?limit=20&subject_id=SUBJ-00001`

### Response (200 OK)
```json
{
  "leaderboard_type": "daily",
  "subject_id": null,
  "entries": [
    {
      "rank": 1,
      "player_id": "PLAYER-00001",
      "display_name": "Ahmed",
      "xp": 350,
      "avatar": "avatar_1",
      "is_me": false
    },
    {
      "rank": 2,
      "player_id": "PLAYER-00002",
      "display_name": "Sara",
      "xp": 280,
      "avatar": "avatar_3",
      "is_me": true
    }
  ],
  "total_players": 150
}
```

---

## GET /api/v1/leaderboard/{type}/me

**Locust name**: `/api/v1/leaderboard/[type]/me`
**Types**: `daily`, `weekly`
**Optional query**: `?subject_id=SUBJ-00001`

### Response (200 OK)
```json
{
  "rank": 15,
  "xp": 120,
  "xp_to_next": 30,
  "neighbors": [
    {"rank": 13, "player_id": "PLAYER-00050", "display_name": "Ali", "xp": 155, "avatar": "avatar_2", "is_me": false},
    {"rank": 14, "player_id": "PLAYER-00033", "display_name": "Noor", "xp": 140, "avatar": "avatar_5", "is_me": false},
    {"rank": 15, "player_id": "PLAYER-00002", "display_name": "Sara", "xp": 120, "avatar": "avatar_3", "is_me": true},
    {"rank": 16, "player_id": "PLAYER-00044", "display_name": "Omar", "xp": 110, "avatar": "avatar_1", "is_me": false},
    {"rank": 17, "player_id": "PLAYER-00055", "display_name": "Lina", "xp": 95, "avatar": "avatar_4", "is_me": false}
  ],
  "total_players": 150
}
```

### Response (200 OK — Unranked Player)
```json
{
  "rank": null,
  "xp": 0,
  "xp_to_next": null,
  "neighbors": [],
  "total_players": 150
}
```

---

## Full Leaderboard Flow (LeaderboardChecker tasks)

```
Task: check_daily (weight 2)
  1. GET /api/v1/leaderboard/daily

Task: check_weekly (weight 1)
  1. GET /api/v1/leaderboard/weekly

Task: check_my_rank (weight 2)
  1. GET /api/v1/leaderboard/daily/me   (or weekly/me, randomly chosen)
```
