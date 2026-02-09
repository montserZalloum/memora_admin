# Memora Player App - Product Requirements Document

**Base URL:** `https://x.conanacademy.com/api/v1` (production will be HTTPS)

---

## Overview

Memora is a gamified learning platform for Arabic-speaking students. Students complete interactive lessons, earn XP and maintain learning streaks, review content via spaced repetition, and compete on leaderboards.

**Your app's responsibilities:**
1. Authenticate users and manage JWT tokens
2. Fetch and cache content (lessons, stages) from CDN
3. Display progress, handle game sessions, submit interactions
4. Show leaderboards and player profiles
5. Enable FSRS review sessions for retention
6. Display product catalog and handle purchase requests
7. Listen for real-time notifications (WebSocket)

**Backend handles:**
- Content authoring and publishing
- Progress calculation and sync
- XP/streak computation
- FSRS (spaced repetition) scheduling
- Access control and subscriptions

---

## Authentication

### POST `/auth/login`

**Request:**
```json
{
  "email": "student@example.com",
  "password": "secure_password"
}
```

**Response (200 OK):**
```json
{
  "access_token": "eyJhbGc...",
  "refresh_token": "eyJhbGc...",
  "token_type": "bearer",
  "profile": {
    "display_name": "أحمد",
    "avatar": "https://cdn.example.com/avatars/123.jpg",
    "gender": "M",
    "xp": 1500,
    "subscriptions": ["SUB-MATH", "SUB-PHYSICS", "TRK-MATH-01"]
  }
}
```

**Token Storage:**
- Store `access_token` securely (Keychain/KeyStore)
- Store `refresh_token` for renewal
- Tokens are JWTs with 7-day expiry

**Authorization Header:**
```
Authorization: Bearer eyJhbGc...
```

Include this header in ALL subsequent API requests.

---

## Content Delivery

### Content Architecture

**Content is NOT fetched from the API.** Instead:

1. **Initial Download:** App downloads a `.plan` file per subject from CDN
   - Contains full subject hierarchy (tracks → units → topics → lessons → stages)
   - Includes all stage content (text, images, interactions)
   - Typically 5-50 MB per subject

2. **Updates:** Check for new plan versions periodically
   - Backend publishes new plan versions to CDN when content changes
   - App compares local version with server version
   - Download and replace if outdated

3. **Format:** Plans are JSON files with this structure:
```json
{
  "version": 3,
  "hierarchy": {
    "tracks": [
      {
        "track_id": "TRK-001",
        "title": "الجبر الأساسي",
        "units": [...]
      }
    ]
  }
}
```

**CDN URLs:** Backend provides CDN base URL. Plan files are at:
```
{cdn_base}/plans/{subject_id}/plan-v{version}.json
```

---

## Progress Tracking

### GET `/progress/{subject}`

Get player's progress summary for a subject.

**Response (200 OK):**
```json
{
  "subject_id": "SUBJ-00001",
  "completed": 45,
  "total": 120,
  "unlocked": 50,
  "percentage": 37.5,
  "has_free_content": true
}
```

**Fields:**
- `completed`: Stages marked complete
- `total`: Total stages in subject
- `unlocked`: Stages currently accessible (including completed)
- `percentage`: Completion percentage (0-100)
- `has_free_content`: Whether subject has any free lessons (no explicit grant needed)

---

### GET `/progress/{subject}/bitmap`

Get detailed completion bitmap (which stages are complete).

**Response (200 OK):**
```json
{
  "subject_id": "SUBJ-00001",
  "bitmap": "00101110011010...",
  "stage_ids": ["stage1", "stage2", "stage3", ...]
}
```

**Bitmap format:**
- Each character represents one stage (in order of `stage_ids`)
- `'1'` = completed, `'0'` = not completed
- Matches positionally with `stage_ids` array

**Usage:**
```javascript
const isComplete = bitmap[index] === '1';
```

---

### GET `/progress/{subject}/tracks`

Get summary of all tracks in a subject (lazy-load optimization).

**Response (200 OK):**
```json
{
  "subject_id": "SUBJ-00001",
  "tracks": [
    {
      "track_id": "TRK-001",
      "completed": 15,
      "total": 40,
      "unlocked": 20,
      "percentage": 37.5
    }
  ]
}
```

---

### GET `/progress/{subject}/tracks/{track_id}`

Get units within a specific track.

**Response (200 OK):**
```json
{
  "track_id": "TRK-001",
  "completed": 15,
  "total": 40,
  "unlocked": 20,
  "percentage": 37.5,
  "units": [
    {
      "unit_id": "UNIT-001",
      "completed": 5,
      "total": 10,
      "unlocked": 7,
      "percentage": 50.0
    }
  ]
}
```

---

### GET `/progress/{subject}/tracks/{track_id}/units/{unit_id}`

Get topics within a specific unit.

**Response (200 OK):**
```json
{
  "unit_id": "UNIT-001",
  "completed": 5,
  "total": 10,
  "unlocked": 7,
  "percentage": 50.0,
  "topics": [
    {
      "topic_id": "TOPIC-001",
      "lesson_ids": ["LESSON-001", "LESSON-002"],
      "completed": 2,
      "total": 5,
      "unlocked": 3,
      "percentage": 40.0,
      "status": "unlocked"
    }
  ]
}
```

**Topic status values:**
- `"locked"`: Not yet accessible
- `"unlocked"`: Accessible but not started
- `"in_progress"`: At least one stage complete
- `"completed"`: All stages complete

---

### GET `/progress/{subject}/lessons/{lesson_id}/status`

Check completion status for a specific lesson.

**Response (200 OK):**
```json
{
  "lesson_id": "LESSON-001",
  "completed_stages": ["stage1", "stage3"],
  "total_stages": 5,
  "is_complete": false
}
```

**Use case:** Show checkmarks on individual stages in lesson UI.

---

## Game Sessions

### POST `/sessions/start`

Start a new game session for a lesson.

**Request:**
```json
{
  "lesson_id": "LESSON-001",
  "subject_id": "SUBJ-00001"
}
```

**Response (200 OK):**
```json
{
  "session_id": "uuid-string",
  "lesson_id": "LESSON-001",
  "started_at": "2026-02-09T10:30:00Z"
}
```

**Errors:**
- `403 Forbidden`: Player doesn't have access to this content
  - Check `profile.subscriptions` from login response
  - Show "Purchase required" UI or lock icon

**Session lifecycle:**
1. Call `/sessions/start` when player taps "Start Lesson"
2. Track interactions locally during lesson
3. Call `/sessions/end` when lesson completes or player exits

---

### POST `/sessions/end`

End a session and submit all interactions.

**Request:**
```json
{
  "session_id": "uuid-string",
  "lesson_id": "LESSON-001",
  "subject_id": "SUBJ-00001",
  "interactions": [
    {
      "stage_id": "stage1",
      "response_time_ms": 5000,
      "timestamp": "2026-02-09T10:31:00Z",
      "is_correct": true,
      "fail_count": 0
    },
    {
      "stage_id": "stage2",
      "response_time_ms": 8000,
      "timestamp": "2026-02-09T10:31:08Z",
      "is_correct": false,
      "fail_count": 1
    }
  ]
}
```

**Response (200 OK):**
```json
{
  "xp_awarded": 15,
  "completed_stages": ["stage1", "stage2"],
  "lesson_complete": false,
  "new_unlocks": ["LESSON-002"]
}
```

**Fields:**
- `xp_awarded`: Total XP earned this session (added to profile.xp)
- `completed_stages`: Stages marked complete (merged into bitmap)
- `lesson_complete`: Whether entire lesson is now complete
- `new_unlocks`: Newly unlocked content (lessons/topics) to highlight in UI

**Interaction tracking:**
- `response_time_ms`: Time spent on stage (milliseconds)
- `is_correct`: Whether stage was answered correctly (affects XP)
- `fail_count`: Number of failed attempts before success (affects FSRS difficulty)

**XP Calculation (backend):**
- Base XP per stage: 5 XP
- Incorrect answer: 2 XP (40% of base)
- Time bonus: +1 XP if completed in < 30 seconds

---

## Wallet & Gamification

### GET `/wallet`

Get player's current wallet (XP and streak).

**Response (200 OK):**
```json
{
  "xp": 1500,
  "current_streak": 7,
  "longest_streak": 12,
  "last_activity_date": "2026-02-09"
}
```

**Streak rules:**
- Completing ANY lesson increments streak
- Missing a day resets `current_streak` to 0
- `longest_streak` is historical best

**Display in UI:**
- Show XP total in profile/header
- Show fire icon + streak count in daily summary
- Celebrate milestones (7-day, 30-day streaks)

---

## Leaderboards

### GET `/leaderboards/{subject}`

Get leaderboard for a subject.

**Query params:**
- `season_id` (optional): Filter by season (defaults to current season)
- `limit` (optional): Max results (default 50, max 100)

**Response (200 OK):**
```json
{
  "subject_id": "SUBJ-00001",
  "season_id": "SEASON-2026-Q1",
  "entries": [
    {
      "rank": 1,
      "player_id": "PLAYER-001",
      "display_name": "أحمد",
      "avatar": "https://cdn.example.com/avatars/123.jpg",
      "xp": 2500,
      "is_current_user": false
    },
    {
      "rank": 2,
      "player_id": "PLAYER-002",
      "display_name": "فاطمة",
      "avatar": null,
      "xp": 2300,
      "is_current_user": true
    }
  ],
  "current_user_rank": 2
}
```

**UI notes:**
- Highlight `is_current_user: true` entry (typically "you" badge)
- Show `current_user_rank` in header even if not in top N
- Default avatar if `avatar` is null

---

## FSRS Review System

### GET `/reviews`

Get overview of due reviews across all subjects.

**Response (200 OK):**
```json
{
  "subjects": [
    {
      "subject_id": "SUBJ-00001",
      "due_count": 15
    },
    {
      "subject_id": "SUBJ-00002",
      "due_count": 8
    }
  ]
}
```

**Cached:** 5-minute TTL. Invalidated when you submit reviews.

**UI placement:**
- Show badge/notification in home screen if any `due_count > 0`
- "15 lessons ready to review" prompt

---

### GET `/reviews/{subject}`

Get up to 10 due stages for a subject (oldest first).

**Response (200 OK):**
```json
{
  "subject_id": "SUBJ-00001",
  "stages": [
    {
      "stage_id": "stage1",
      "lesson_id": "LESSON-001",
      "stage_type": "MCQ"
    },
    {
      "stage_id": "stage5",
      "lesson_id": "LESSON-002",
      "stage_type": "TrueFalse"
    }
  ],
  "has_more": true
}
```

**Client responsibility:**
- Use `lesson_id` + `stage_id` to look up stage content from local plan cache
- Display stage content (question, answers, etc.)
- Collect user's answer and track `fail_count`

**Batch size:** Always returns ≤10 stages per call. Check `has_more` for pagination.

---

### POST `/reviews/{subject}/submit`

Submit batch of reviewed stages.

**Request:**
```json
{
  "stages": [
    {
      "stage_id": "stage1",
      "fail_count": 0
    },
    {
      "stage_id": "stage5",
      "fail_count": 2
    }
  ]
}
```

**Response (200 OK):**
```json
{
  "processed": 2,
  "remaining_due": 13,
  "has_more": true,
  "xp_awarded": 3
}
```

**Fields:**
- `processed`: Number of stages successfully processed
- `remaining_due`: How many stages still due for this subject
- `has_more`: Whether to fetch another batch (same as `remaining_due > 0`)
- `xp_awarded`: 3 XP per review session (not per stage)

**Review flow:**
1. Fetch `/reviews/{subject}` (get 10 stages)
2. Present stages one-by-one to user
3. Track whether user got it right (0 = correct, 1+ = fail count)
4. Submit batch via `/reviews/{subject}/submit`
5. If `has_more: true`, repeat from step 1

**fail_count mapping:**
- `0`: Answered correctly on first try
- `1`: Answered correctly after 1 mistake
- `2+`: Answered correctly after 2+ mistakes (or gave up)

**FSRS scheduling (backend handles):**
- Uses fail_count to compute next review date
- Next review is minimum tomorrow (no same-day reviews)
- Stages appear in `/reviews` when due date ≤ today

---

## Product Catalog & Purchases

### GET `/catalog`

Get available products for your plan (products you haven't purchased yet).

**Response (200 OK):**
```json
{
  "products": [
    {
      "product_id": "PROD-001",
      "bundle_name": "حزمة الفيزياء المتقدمة",
      "subjects": [
        {
          "subject_id": "SUBJ-00003",
          "title": "ميكانيكا نيوتن"
        },
        {
          "subject_id": "SUBJ-00004",
          "title": "الكهرومغناطيسية"
        }
      ],
      "description": "دروس متقدمة في الفيزياء للصف الثاني الثانوي",
      "price": 299.00,
      "currency": "SAR"
    }
  ]
}
```

**Catalog behavior:**
- Products you've already purchased do NOT appear
- Products with pending purchase requests do NOT appear (prevents duplicates)

**Cached:** Permanent cache until invalidated (no TTL). Backend pushes invalidation when products change.

---

### POST `/purchase`

Submit a purchase request for a product.

**Request:**
```json
{
  "product_id": "PROD-001"
}
```

**Response (201 Created):**
```json
{
  "transaction_id": "TXN-001",
  "status": "pending",
  "message": "Purchase request submitted. You'll be notified when approved."
}
```

**Purchase flow:**
1. User browses catalog and taps "Buy"
2. App calls `/purchase` with `product_id`
3. Show success message ("Request submitted, pending admin approval")
4. Product disappears from catalog (status = pending)
5. User receives WebSocket notification when approved (see Real-Time Notifications)
6. Approved subjects appear in `profile.subscriptions`

**Payment:** Manual approval only (Phase 1). Payment gateway integration deferred.

---

## Real-Time Notifications (WebSocket)

### Connect: `ws://x.conanacademy.com/api/v1/ws`

Establish WebSocket connection for real-time updates.

**Connection:**
```javascript
// Include JWT in query param for authentication
const ws = new WebSocket(`ws://x.conanacademy.com/api/v1/ws?token=${access_token}`);
```

**Message format:**
```json
{
  "type": "subscription_update",
  "data": {
    "transaction_id": "TXN-001",
    "status": "approved",
    "product_name": "حزمة الفيزياء المتقدمة",
    "subject_ids": ["SUBJ-00003", "SUBJ-00004"]
  }
}
```

**Client handling:**
```javascript
ws.onmessage = (event) => {
  const message = JSON.parse(event.data);

  if (message.type === 'subscription_update') {
    if (message.data.status === 'approved') {
      // Add subject_ids to local subscriptions array
      // Show success notification
      // Refresh catalog (invalidate cache)
      // Unlock newly accessible content
    } else if (message.data.status === 'rejected') {
      // Show rejection notification with reason
    }
  }
};
```

**Reconnection:**
- If connection drops, reconnect with exponential backoff
- Max 5 retries before showing "offline" state

---

## Error Handling

### Standard Error Response

All errors return this format:

```json
{
  "detail": "Human-readable error message"
}
```

### HTTP Status Codes

| Code | Meaning | Action |
|------|---------|--------|
| 200 | Success | Process response |
| 201 | Created | Resource created successfully |
| 400 | Bad Request | Show error to user, check request format |
| 401 | Unauthorized | Refresh token or prompt re-login |
| 403 | Forbidden | User doesn't have access (show paywall) |
| 404 | Not Found | Resource doesn't exist |
| 500 | Server Error | Show "Try again later" message |

### 403 Forbidden (Access Control)

When you get `403` on content endpoints:

1. Check `profile.subscriptions` from login
2. If subject/track NOT in subscriptions:
   - Show lock icon on content
   - Display "Purchase required" message
   - Link to catalog

**Free content:**
- Some lessons are marked `is_free: true` (no subscription required)
- Backend grants access to free content automatically
- If you get 403 on free content, it's a bug — report it

---

## Performance Expectations

### API Response Times (Target)

| Endpoint | Target | Cached |
|----------|--------|--------|
| `/auth/login` | <500ms | No |
| `/progress/{subject}` | <20ms | Yes (Redis) |
| `/progress/{subject}/bitmap` | <50ms | Yes (Redis) |
| `/sessions/start` | <50ms | No |
| `/sessions/end` | <100ms | No |
| `/wallet` | <20ms | Yes (Redis) |
| `/leaderboards/{subject}` | <200ms | Yes (5 min) |
| `/reviews` | <50ms | Yes (5 min) |
| `/reviews/{subject}` | <100ms | No (fresh) |
| `/catalog` | <50ms | Yes (permanent) |

### Caching Strategy

**App-side caching:**
- Content (plan files): Cache indefinitely, check for updates daily
- Progress bitmaps: Cache for 5 minutes, invalidate on `/sessions/end`
- Leaderboards: Cache for 5 minutes (matches backend cache)
- Catalog: Cache indefinitely, invalidate on WebSocket notification

**Backend caching:**
- All progress data cached in Redis (sub-20ms reads)
- Leaderboards cached for 5 minutes
- Review overview cached for 5 minutes
- Catalog cached until product changes

---

## Client Responsibilities Summary

✅ **You handle:**
1. JWT token management (store, refresh, include in headers)
2. Content download and local storage (plan files from CDN)
3. Offline support (cached content, queue interactions)
4. UI/UX (lesson flow, animations, gamification)
5. Stage rendering (questions, answers, media)
6. Interaction tracking (`response_time_ms`, `fail_count`)
7. WebSocket reconnection logic

❌ **Backend handles:**
1. Progress calculation (which stages are complete, unlocked)
2. XP and streak computation
3. FSRS scheduling (when reviews are due)
4. Leaderboard ranking
5. Access control (who can access what)
6. Content publishing and versioning

---

## Testing & Development

### Test Account

```
Email: test@example.com
Password: test123
```

This account has:
- Full access to SUBJ-00001 (Math - Basic)
- Partial access to SUBJ-00002 (Physics - Intro)
- Some completed stages for testing progress UI
- Active 5-day streak

### Postman Collection

Available at: `http://x.conanacademy.com/api/docs`

Interactive API documentation with "Try it out" buttons.

---

## FAQ

**Q: How do I know which content is free vs paid?**
A: Check `is_free` field in your local plan file. If `is_free: true`, no subscription required.

**Q: Can users complete lessons offline?**
A: Yes! Cache content locally. Queue `/sessions/end` calls until online. Progress syncs when reconnected.

**Q: What happens if two devices complete the same stage?**
A: Backend deduplicates. Progress is union of all completions (stage marked complete once).

**Q: How often should I check for plan updates?**
A: Once per day on app launch is sufficient. Backend publishes new versions infrequently (weekly).

**Q: Do reviews count toward daily streak?**
A: No. Only lesson completions (via `/sessions/end`) count toward streak.

**Q: What if a review stage no longer exists in the plan?**
A: Backend filters it out automatically. You'll never receive deleted stages from `/reviews/{subject}`.

**Q: Can I batch multiple sessions before calling `/sessions/end`?**
A: No. Each `/sessions/start` must have a matching `/sessions/end`. One session = one lesson attempt.

---

## Support

For API issues or questions:
- Check logs at `http://x.conanacademy.com/api/v1/health/live`
- Report bugs to backend team with full request/response
- Include `Authorization` header (redacted) in bug reports
