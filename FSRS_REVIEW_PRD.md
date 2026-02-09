# Memora FSRS Review System - PRD

**Base URL:** `https://x.conanacademy.com/api/v1`

---

## Overview

The FSRS (Free Spaced Repetition Scheduler) review system helps students retain what they've learned through scientifically-optimized review sessions. After completing lessons, stages become due for review based on FSRS scheduling.

**Key Concepts:**
- **Memory State:** Each completed stage has a memory state (stability, difficulty, next_review date)
- **Due Reviews:** Stages where `next_review <= today` appear in review sessions
- **Review Sessions:** Players review up to 10 stages at a time, oldest due first (FIFO)
- **FSRS Scheduling:** Backend computes next review date based on player's performance (fail_count)

**User Flow:**
1. App checks for due reviews daily (badge notification)
2. Player taps "Review" → sees subjects with due counts
3. Player selects subject → reviews 10 stages at a time
4. Player submits answers → earns 3 XP per session
5. Backend schedules next review date (minimum tomorrow)
6. Repeat until no more due reviews for the day

---

## Daily Review Check

### GET `/reviews`

**Purpose:** Check how many reviews are due across all subjects.

**When to call:**
- On app launch (daily check)
- After completing a review session (to update badge)
- When user navigates to "Reviews" screen

**Request:**
```
GET /api/v1/reviews
Authorization: Bearer <token>
```

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

**Response fields:**
- `subject_id`: Subject identifier (matches your content plan files)
- `due_count`: Number of stages due for review today

**UI Implementation:**
```javascript
// Show badge notification if any reviews due
const totalDue = subjects.reduce((sum, s) => sum + s.due_count, 0);
if (totalDue > 0) {
  showBadge(`${totalDue} reviews ready`);
}
```

**Caching:**
- Backend caches this response for 5 minutes
- Cache automatically invalidates when you submit reviews
- Safe to call frequently without performance impact

**Empty state:**
```json
{
  "subjects": []
}
```
No reviews due today. Show "Come back tomorrow!" message.

---

## Fetching Due Stages

### GET `/reviews/{subject}`

**Purpose:** Get up to 10 due stages for a subject to review.

**When to call:**
- When user taps on a subject in the reviews list
- After submitting a review batch (to get next 10 stages)

**Request:**
```
GET /api/v1/reviews/SUBJ-00001
Authorization: Bearer <token>
```

**Response (200 OK):**
```json
{
  "subject_id": "SUBJ-00001",
  "stages": [
    {
      "stage_id": "aerviq97bb",
      "lesson_id": "LESSON-001",
      "stage_type": "MCQ"
    },
    {
      "stage_id": "bxmw82kd9c",
      "lesson_id": "LESSON-003",
      "stage_type": "TrueFalse"
    },
    {
      "stage_id": "cmzp91je7d",
      "lesson_id": "LESSON-002",
      "stage_type": "MCQ"
    }
  ],
  "has_more": true
}
```

**Response fields:**
- `subject_id`: Subject being reviewed
- `stages`: Array of due stages (max 10, ordered oldest → newest)
  - `stage_id`: Unique stage identifier (use to look up content)
  - `lesson_id`: Parent lesson (use to look up content)
  - `stage_type`: Stage type (MCQ, TrueFalse, FillBlank, etc.)
- `has_more`: Whether more stages are due after these 10

**Stage ordering:**
- FIFO (First In, First Out): Oldest due first
- Example: Stage due 3 days ago appears before stage due yesterday

**Batch size:**
- Always returns ≤10 stages
- Even if 50 stages are due, you get 10 at a time
- Call again after submitting to get next batch

**Client responsibility:**
```javascript
// Look up stage content from local plan cache
const stage = planCache.getStage(lesson_id, stage_id);

// Display question, answers, images, etc.
renderStage(stage);

// Track user's performance
let failCount = 0;
stage.onIncorrectAnswer(() => failCount++);
stage.onCorrectAnswer(() => recordResult(stage_id, failCount));
```

**Content delivery:**
- API does NOT return stage content (text, images, answers)
- You must have the subject's plan file cached locally
- Use `lesson_id` + `stage_id` to look up content in your cache
- If content not found: Skip stage and report bug

**Removed stages:**
- If a stage was removed from the lesson (content updated), backend filters it out
- You'll never receive deleted stages from this endpoint
- Backend over-fetches to compensate (fetches 15, returns 10 valid)

---

## Submitting Review Results

### POST `/reviews/{subject}/submit`

**Purpose:** Submit batch of reviewed stages and get next review scheduled.

**When to call:**
- After player reviews all 10 stages from current batch
- When player completes a review session

**Request:**
```
POST /api/v1/reviews/SUBJ-00001/submit
Authorization: Bearer <token>
Content-Type: application/json

{
  "stages": [
    {
      "stage_id": "aerviq97bb",
      "fail_count": 0
    },
    {
      "stage_id": "bxmw82kd9c",
      "fail_count": 1
    },
    {
      "stage_id": "cmzp91je7d",
      "fail_count": 3
    }
  ]
}
```

**Request fields:**
- `stages`: Array of reviewed stages (1-10 stages per batch)
  - `stage_id`: Stage identifier (from `/reviews/{subject}` response)
  - `fail_count`: Number of incorrect attempts before correct answer
    - `0`: Answered correctly on first try
    - `1`: Answered correctly after 1 mistake
    - `2+`: Answered correctly after 2+ mistakes (or gave up)

**Response (200 OK):**
```json
{
  "processed": 3,
  "remaining_due": 12,
  "has_more": true,
  "xp_awarded": 3
}
```

**Response fields:**
- `processed`: Number of stages successfully processed
- `remaining_due`: How many stages still due for this subject today
- `has_more`: Whether to fetch another batch (`remaining_due > 0`)
- `xp_awarded`: XP earned this session (always 3 XP per session, not per stage)

**XP Reward:**
- Fixed 3 XP per review session (regardless of batch size)
- NOT per stage (to prevent farming)
- Awarded only if `processed > 0`

**Review flow:**
```javascript
// 1. Fetch due stages
const { stages, has_more } = await fetch('/api/v1/reviews/SUBJ-00001');

// 2. Present stages to user one-by-one
const results = [];
for (const stage of stages) {
  const failCount = await presentStageAndGetFailCount(stage);
  results.push({ stage_id: stage.stage_id, fail_count: failCount });
}

// 3. Submit batch
const response = await fetch('/api/v1/reviews/SUBJ-00001/submit', {
  method: 'POST',
  body: JSON.stringify({ stages: results })
});

// 4. Show XP reward
showXPAnimation(response.xp_awarded);

// 5. Check if more due
if (response.has_more) {
  showContinueButton(); // Fetch next batch
} else {
  showCompletionMessage(`All reviews complete! Come back tomorrow.`);
}
```

**FSRS Scheduling (Backend):**
- Backend uses fail_count to compute next review date
- Mapping:
  - `fail_count = 0` → FSRS Rating: Good (easier next time)
  - `fail_count = 1` → FSRS Rating: Hard (slightly easier)
  - `fail_count ≥ 2` → FSRS Rating: Again (reset, review soon)
- Next review date is always:
  - Clamped to midnight (00:00:00)
  - Minimum of tomorrow (no same-day reviews)
- Next review appears in `/reviews/{subject}` when `next_review <= today`

---

## Content Delivery

### Lesson Files (CDN)

**Stage content is NOT in the API.** You must take them from the lessons files directly.

**Handling missing content:**
- If `getStageContent()` returns null, skip that stage
- Log error: `"Stage ${stageId} not found in lesson ${lessonId}"`
- DO NOT submit that stage in `/reviews/{subject}/submit`
- Backend already filters out removed stages, but extra safety

---

## UI/UX Recommendations

### Daily Check (Home Screen)

**Badge notification:**
```
┌─────────────────────┐
│  📚 Reviews         │
│  15 ready           │  ← Show total due count
└─────────────────────┘
```

**When to show:**
- Check `/reviews` on app launch
- Update badge when returning from background
- Refresh after review session completion

---

### Subject Selection

**List subjects with due counts:**
```
┌─────────────────────────────┐
│  Mathematics        15 due  │
│  Physics             8 due  │
│  Chemistry           0 due  │  ← Grayed out or hidden
└─────────────────────────────┘
```

**Tap subject → Start review session**

---

### Review Session

**Progress indicator:**
```
┌─────────────────────────────┐
│  Question 3 of 10           │  ← Current position in batch
│  ████████░░░░░░░░░  30%     │
└─────────────────────────────┘
```

**Stage presentation:**
1. Show question text
2. Show answer options (MCQ, TrueFalse, etc.)
3. Wait for user answer
4. If incorrect: increment `fail_count`, show correct answer, allow retry
5. If correct: move to next stage
6. Track `fail_count` per stage

**End of batch:**
```
┌─────────────────────────────┐
│  ✓ 10 stages reviewed       │
│  +3 XP earned               │
│                             │
│  12 more due today          │
│  [Continue] [Done]          │
└─────────────────────────────┘
```

- If `has_more: true` → Show "Continue" button (fetch next batch)
- If `has_more: false` → Show "Done" (all reviews complete)

---

### Completion State

**All reviews done:**
```
┌─────────────────────────────┐
│  🎉 All caught up!          │
│  You reviewed 25 stages     │
│  +9 XP earned today         │
│                             │
│  Come back tomorrow for     │
│  more reviews!              │
└─────────────────────────────┘
```

---

## fail_count Tracking

### How to Track fail_count

**fail_count = number of incorrect attempts before correct answer**

**Example 1: Perfect (fail_count = 0)**
```
User sees: "What is 2 + 2?"
User answers: "4" ✓
→ fail_count = 0
```

**Example 2: One mistake (fail_count = 1)**
```
User sees: "What is 2 + 2?"
User answers: "3" ✗ (wrong)
User answers: "4" ✓ (correct)
→ fail_count = 1
```

**Example 3: Multiple mistakes (fail_count = 3)**
```
User sees: "What is 2 + 2?"
User answers: "3" ✗
User answers: "5" ✗
User answers: "6" ✗
User answers: "4" ✓
→ fail_count = 3
```

**Example 4: Gave up (fail_count = 3+)**
```
User sees: "What is 2 + 2?"
User answers: "3" ✗
User answers: "5" ✗
User taps: "Show answer" → "4"
User taps: "I understand"
→ fail_count = 3 (or higher, you decide)
```

**Implementation:**
```javascript
class StageReview {
  constructor(stage) {
    this.stage = stage;
    this.failCount = 0;
  }

  onAnswer(answer) {
    if (this.isCorrect(answer)) {
      return { correct: true, failCount: this.failCount };
    } else {
      this.failCount++;
      return { correct: false, failCount: this.failCount };
    }
  }

  onShowAnswer() {
    // User gave up, set high fail_count
    this.failCount = Math.max(this.failCount, 3);
  }
}
```

---

## Error Handling

### 401 Unauthorized
```json
{
  "detail": "Invalid or expired token"
}
```
**Action:** Refresh JWT using `/auth/refresh` or prompt re-login.

---

### 403 Forbidden
```json
{
  "detail": "Access denied"
}
```
**Action:** User doesn't have access to this subject. Show "Subscription required" message.

**Note:** Reviews are only available for subjects the user has completed at least one lesson in. If 403, user likely hasn't completed any lessons yet.

---

### 404 Not Found (Subject)
```json
{
  "detail": "Subject not found"
}
```
**Action:** Invalid `subject_id`. Check that subject exists in your plan cache.

---

### 400 Bad Request (Submit)
```json
{
  "detail": "Invalid stage_id in submission"
}
```
**Action:** You submitted a `stage_id` that doesn't match the due stages. Re-fetch `/reviews/{subject}` and try again.

---

### Empty Reviews
```
GET /api/v1/reviews/SUBJ-00001
→ 200 OK

{
  "subject_id": "SUBJ-00001",
  "stages": [],
  "has_more": false
}
```

**Meaning:** No stages due for this subject today.

**Action:** Show "All caught up! Come back tomorrow." message.

---

## Performance & Caching

### Response Times (Target)

| Endpoint | Target | Notes |
|----------|--------|-------|
| `GET /reviews` | <50ms | Cached 5 min |
| `GET /reviews/{subject}` | <100ms | Fresh query (MariaDB) |
| `POST /reviews/{subject}/submit` | <200ms | FSRS computation + DB update |

### Caching Strategy

**Overview endpoint (`GET /reviews`):**
- Backend caches for 5 minutes
- Invalidated automatically when you submit reviews
- Safe to poll frequently (every app launch)

**Due stages endpoint (`GET /reviews/{subject}`):**
- NOT cached (always fresh)
- Uses composite DB index for fast queries
- <100ms even with 120M+ memory state records

**Stage content (plan files):**
- Cache plan files locally (IndexedDB, SQLite, etc.)
- Check for updates daily (compare version numbers)
- Download new version if available

---

## Client Responsibilities

✅ **You handle:**
1. JWT token management (store, refresh, include in Authorization header)
2. Content caching (plan files from CDN)
3. Stage content lookup (lesson_id + stage_id → stage data)
4. fail_count tracking (count incorrect attempts)
5. UI/UX (progress bars, animations, "show answer" button)
6. Badge notifications (show due count on home screen)
7. Offline handling (queue submissions if offline)

❌ **Backend handles:**
1. FSRS scheduling (when each stage is next due)
2. Memory state management (stability, difficulty)
3. Filtering removed stages (deleted from lessons)
4. XP calculation (3 XP per session)
5. Access control (who can review what)

---

## Offline Support

### Queueing Reviews

**Scenario:** User reviews stages while offline.

**Strategy:**
1. Allow user to complete review session locally
2. Store results in local queue: `{ subject_id, stages: [{stage_id, fail_count}] }`
3. When online, submit queued reviews via `/reviews/{subject}/submit`
4. If submission fails (404, 400), discard that batch and log error

**XP sync:**
- XP is awarded by backend on successful submission
- When offline reviews sync, user sees XP increase
- Show notification: "+9 XP synced from offline reviews"

**Edge case:** Stage was removed while offline
- Backend returns `processed < stages.length` if some stages invalid
- User still gets XP for processed stages
- Log unprocessed stage_ids for debugging

---

## Testing

### Test Account
```
Email: test@example.com
Password: test123
```

**This account has:**
- Completed lessons in SUBJ-00001 (Math)
- 5 stages due for review (as of 2026-02-09)
- Use to test review flow end-to-end

### Manual Testing Flow

1. **Daily check:**
   ```bash
   curl -H "Authorization: Bearer <token>" \
     https://x.conanacademy.com/api/v1/reviews
   ```
   Expect: `due_count > 0` for at least one subject

2. **Fetch due stages:**
   ```bash
   curl -H "Authorization: Bearer <token>" \
     https://x.conanacademy.com/api/v1/reviews/SUBJ-00001
   ```
   Expect: Array of stages with `stage_id`, `lesson_id`, `stage_type`

3. **Submit results:**
   ```bash
   curl -X POST \
     -H "Authorization: Bearer <token>" \
     -H "Content-Type: application/json" \
     -d '{"stages":[{"stage_id":"aerviq97bb","fail_count":0}]}' \
     https://x.conanacademy.com/api/v1/reviews/SUBJ-00001/submit
   ```
   Expect: `xp_awarded: 3`, `remaining_due` decremented

4. **Verify cache invalidation:**
   ```bash
   curl -H "Authorization: Bearer <token>" \
     https://x.conanacademy.com/api/v1/reviews
   ```
   Expect: `due_count` for SUBJ-00001 reduced by 1

---

## FAQ

**Q: When do stages become due for review?**
A: After completing a lesson, its stages are scheduled for review. The first review is typically 1-3 days later. Subsequent reviews are spaced based on performance (easier if you get it right, sooner if you struggle).

**Q: Can users review the same stage multiple times per day?**
A: No. Each stage can only be reviewed once per day. After submission, backend schedules the next review for tomorrow or later.

**Q: What happens if user closes app mid-review?**
A: Progress is lost (not submitted). They'll need to start a new batch. Consider adding "Resume session" with local storage.

**Q: Do reviews count toward daily streak?**
A: No. Only lesson completions (via `/sessions/end`) count toward streak. Reviews are separate.

**Q: What if user gets all answers wrong?**
A: They still earn 3 XP (reviews reward participation). High fail_count makes stage appear sooner for next review (FSRS makes it easier).

**Q: Can users skip stages they don't remember?**
A: Yes. Implement "Show answer" button → set `fail_count = 3+` → move to next stage. This tells FSRS the user struggled.

**Q: How many stages can be due at once?**
A: Unlimited (per subject). If user hasn't reviewed in weeks, could have 100+ due. That's why we batch in groups of 10.

**Q: What if Lesson file doesn't have the stage?**
A: Skip that stage silently. Backend already filters removed stages, but content updates might create race conditions. Log error for debugging.

---

## Support

**API Issues:**
- Check health: `https://x.conanacademy.com/api/v1/health/live`
- Report bugs with full request/response (redact Authorization token)

**Content Issues:**
- If stage content missing: Verify lesson file version
- Re-download lesson file from CDN

**FSRS Behavior:**
- Backend logs all FSRS computations
- Include `stage_id` and `fail_count` in bug reports
