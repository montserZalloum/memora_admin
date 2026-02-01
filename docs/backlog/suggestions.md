🟢 Redis Data Structures المطلوبة
1. Progress Bitmaps
redisKey: progress:{player_id}:{subject_id}
Type: String (binary bitmap)
Purpose: تتبع الدروس المكتملة بـ O(1)
2. Leaderboards
redisKey: leaderboard:xp:daily:{date}
Type: Sorted Set
TTL: 48 hours

Key: leaderboard:xp:weekly:{week}
Key: leaderboard:streak:current
3. Dirty Sets (للـ Batch Sync)
redisKey: dirty:progress
Key: dirty:wallet
Type: Set
Purpose: تجميع التغييرات للكتابة الدفعية
4. Sessions
redisKey: session:{player_id}:{device_id}
Type: String (JSON)
TTL: 7 days
5. Access Grants
redisKey: memora:access:{player_id}
Type: Set
Values: ["SUB-MATH-101", "PLAN-00001"]
6. Rate Limiting
redisKey: ratelimit:{player_id}:{endpoint}
Type: String (counter)
TTL: 60 seconds
```

---

## 🔵 JSON Build System

### Private JSON (Backend Only)
```
{subject_id}_b.json - BitMap Index
├── subject_id
├── version
├── total_lessons
└── structure
    ├── tracks: {track_id: {is_linear, bits: [0,1,2,3]}}
    ├── units: {unit_id: {track, is_linear, bits: [0,1]}}
    ├── topics: {topic_id: {unit, is_linear, bits: [0]}}
    └── lessons: {lesson_id: {topic, bit: 0}}
```

### Public JSON (CDN)
```
/manifest.json                    - Global (5min cache)
/plans/{id}/manifest.json         - Per Plan (5min cache)
/subjects/{id}/_h.json           - Hierarchy (5min cache)
/subjects/{id}/units/{id}_c.json - Unit Content (1hr cache)
/lessons/{id}.json               - Lesson (1 month cache)