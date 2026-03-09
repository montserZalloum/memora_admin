# Client PRD: Challenge Hub (مركز التحدي) — Mobile App

**Version:** 1.0
**Date:** 2026-03-08
**Backend Version:** 038-challenge-hub
**Language/Direction:** Arabic (RTL)

---

## Overview

Challenge Hub is a **sequential, self-paced mastery challenge** where students prove topic-level knowledge by answering all MCQ questions for a topic in one sitting. Students earn **Challenge XP** (isolated from main game XP), compete on plan-scoped leaderboards, and replay topics to improve scores. The feature follows the existing Subject > Track > Unit > Topic hierarchy but stops at the topic level (no individual lessons).

### Key Differentiators from Normal Path

| Aspect | Normal Path | Challenge Hub |
|--------|-------------|---------------|
| Granularity | Lesson-by-lesson | All MCQs for a topic at once |
| Progress type | Incremental completion | Pass/fail stamp per topic |
| XP system | Main XP (wallet) | Challenge XP (isolated) |
| Leaderboard | Main leaderboard | Separate Challenge leaderboard |
| Retries | Unlimited per lesson | Unlimited per topic |
| Question flow | Forward only, answer shown on wrong | Same |

---

## Navigation Architecture

```
┌──────────────────────────────────────────────┐
│                  Home / Hub Tabs              │
│  [Normal Path]  [Challenge Hub]  [Practice]  │
└───────────────────────┬──────────────────────┘
                        │
         ┌──────────────▼──────────────┐
         │   Subject Selection Screen  │
         │  (Cards with progress ring) │
         └──────────────┬──────────────┘
                        │
         ┌──────────────▼──────────────┐
         │   Subject Detail Screen     │
         │  (Hierarchy: Track > Unit   │
         │   > Topic with states)      │
         └──────────────┬──────────────┘
                        │
         ┌──────────────▼──────────────┐
         │   Challenge Play Screen     │
         │  (Question flow, 1 by 1)   │
         └──────────────┬──────────────┘
                        │
         ┌──────────────▼──────────────┐
         │   Result Screen             │
         │  (Score, XP, stamp, next)   │
         └──────────────┘

         ┌─────────────────────────────┐
         │   Leaderboard Screen        │
         │  (Top 20 + my rank)         │
         └─────────────────────────────┘
```

---

## Authentication

All endpoints require JWT via `Authorization: Bearer <token>`. The token contains:

```json
{
  "sub": "966512345678",
  "plan": "PLAN-00001",
  "season": "SEAS-00027",
  "name": "Ahmed",
  "fid": "session-uuid"
}
```

**Critical**: The `season` claim is **required** for challenge attempts. Tokens without `season` will receive `403 SEASON_REQUIRED` on submission.

### Base URL

```
Production: https://api.memora.app/api/v1
Development: http://localhost:8002/api/v1
```

---

## Screen 1: Subject Selection (Challenge Hub Landing)

### Purpose

Entry point. Shows all subjects from the student's plan with challenge progress summary.

### API Call

```http
GET /api/v1/challenge/hierarchy
Authorization: Bearer <token>
```

### Response

```json
{
  "subjects": [
    {
      "subject_id": "SUBJ-001",
      "subject_name": "الرياضيات — الصف الخامس",
      "total_topics": 45,
      "stamped_topics": 12,
      "total_challenge_xp": 340
    },
    {
      "subject_id": "SUBJ-002",
      "subject_name": "العلوم — الصف الخامس",
      "total_topics": 30,
      "stamped_topics": 0,
      "total_challenge_xp": 0
    }
  ]
}
```

### Response Fields

| Field | Type | Description |
|-------|------|-------------|
| `subject_id` | string | Unique subject identifier |
| `subject_name` | string | Arabic display name |
| `total_topics` | int | Total topics with MCQ questions (excludes empty topics) |
| `stamped_topics` | int | Topics the student has passed |
| `total_challenge_xp` | int | Sum of all Challenge XP earned in this subject |

### UI Requirements

#### Layout
- Grid or list of subject cards
- Each card shows:
  - Subject name (Arabic, RTL)
  - Circular progress ring: `stamped_topics / total_topics`
  - Challenge XP badge: `⭐ 340 XP`
  - Text: `12/45 تحديات مكتملة`

#### States
- **No subjects**: Empty state — "لا توجد مواد متاحة حالياً" (No subjects available)
- **All stamped**: Celebration state — all progress rings full, "ممتاز! أكملت جميع التحديات" (Excellent! You completed all challenges)
- **Loading**: Skeleton cards

#### Interactions
- Tap subject card → Navigate to Subject Detail Screen
- Pull to refresh → Re-fetch hierarchy
- Leaderboard icon in top bar → Navigate to Leaderboard Screen

### Error Handling

| Status | Code | Action |
|--------|------|--------|
| 401 | `UNAUTHORIZED` | Redirect to login |
| 403 | Season gate | Show "الموسم غير نشط" (Season not active) |

---

## Screen 2: Subject Detail (Hierarchy Browser)

### Purpose

Browse tracks, units, and topics with visual unlock/stamp/lock states.

### API Call

```http
GET /api/v1/challenge/hierarchy/{subject_id}
Authorization: Bearer <token>
```

### Response

```json
{
  "subject_id": "SUBJ-001",
  "tracks": [
    {
      "track_id": "TRK-001",
      "track_name": "الجبر",
      "has_access": true,
      "units": [
        {
          "unit_id": "UNIT-001",
          "unit_name": "المعادلات الخطية",
          "topics": [
            {
              "topic_id": "TOPIC-001",
              "topic_name": "المعادلة من الدرجة الأولى",
              "state": "stamped",
              "mcq_count": 20,
              "best_score_pct": 85.0,
              "best_passing_pct": 85.0,
              "total_xp": 90,
              "attempt_count": 3,
              "normal_path_complete": true,
              "has_access": true,
              "lock_reason": null
            },
            {
              "topic_id": "TOPIC-002",
              "topic_name": "المعادلة من الدرجة الثانية",
              "state": "open",
              "mcq_count": 15,
              "best_score_pct": null,
              "best_passing_pct": null,
              "total_xp": 0,
              "attempt_count": 0,
              "normal_path_complete": true,
              "has_access": true,
              "lock_reason": null
            },
            {
              "topic_id": "TOPIC-003",
              "topic_name": "الجذور التربيعية",
              "state": "locked",
              "mcq_count": 25,
              "best_score_pct": null,
              "best_passing_pct": null,
              "total_xp": 0,
              "attempt_count": 0,
              "normal_path_complete": true,
              "has_access": true,
              "lock_reason": "PREVIOUS_NOT_STAMPED"
            }
          ]
        }
      ]
    },
    {
      "track_id": "TRK-002",
      "track_name": "الهندسة",
      "has_access": false,
      "units": []
    }
  ]
}
```

### Response Fields — TopicState

| Field | Type | Description |
|-------|------|-------------|
| `topic_id` | string | Unique topic identifier |
| `topic_name` | string | Arabic display name |
| `state` | string | One of: `"locked"`, `"open"`, `"stamped"` |
| `mcq_count` | int | Number of MCQ questions (0 = hidden from UI) |
| `best_score_pct` | float\|null | Best overall score percentage (null if never attempted) |
| `best_passing_pct` | float\|null | Best passing score percentage (null if never passed) |
| `total_xp` | int | Total Challenge XP earned for this topic |
| `attempt_count` | int | Number of completed attempts |
| `normal_path_complete` | bool | Whether all lessons completed on normal path |
| `has_access` | bool | Whether student has content access |
| `lock_reason` | string\|null | Why topic is locked (null if not locked) |

### Lock Reasons

| `lock_reason` | Arabic Message | Description |
|---------------|----------------|-------------|
| `NO_ACCESS` | "يجب الاشتراك أولاً" | No subscription/grant for this content |
| `NORMAL_PATH_INCOMPLETE` | "أكمل دروس الموضوع أولاً" | Haven't finished all lessons on normal path |
| `PREVIOUS_NOT_STAMPED` | "أكمل التحدي السابق أولاً" | Previous topic not stamped in Challenge Hub |
| `null` | — | Topic is open or stamped |

### UI Requirements

#### Layout
- Expandable/collapsible track sections
- Inside each track: unit headers with topic list
- Topics are the interactive elements (not tracks or units)
- Empty topics (`mcq_count == 0`) are **hidden** — do not render them

#### Topic Visual States

```
┌─────────────────────────────────────────────────────┐
│  STAMPED (state: "stamped")                         │
│  ┌──────────────────────────────────────────────┐   │
│  │ ✅ المعادلة من الدرجة الأولى                  │   │
│  │ ⭐ 90 XP  |  أفضل: 85%  |  3 محاولات         │   │
│  │ [أعد التحدي]  ← visible, not primary          │   │
│  └──────────────────────────────────────────────┘   │
│                                                     │
│  OPEN (state: "open")                               │
│  ┌──────────────────────────────────────────────┐   │
│  │ 🔓 المعادلة من الدرجة الثانية                │   │
│  │ 15 سؤال  |  لم يُجرَب بعد                    │   │
│  │ [ابدأ التحدي]  ← primary CTA                  │   │
│  └──────────────────────────────────────────────┘   │
│                                                     │
│  OPEN + PREVIOUSLY FAILED                           │
│  ┌──────────────────────────────────────────────┐   │
│  │ 🔓 المتتاليات العددية                        │   │
│  │ 20 سؤال  |  أفضل: 40%  |  1 محاولة           │   │
│  │ [أعد المحاولة]  ← primary CTA                 │   │
│  └──────────────────────────────────────────────┘   │
│                                                     │
│  LOCKED (state: "locked")                           │
│  ┌──────────────────────────────────────────────┐   │
│  │ 🔒 الجذور التربيعية                          │   │
│  │ أكمل التحدي السابق أولاً                     │   │
│  │ [greyed out, not tappable]                    │   │
│  └──────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────┘
```

#### Track-Level Access Denial
- If `has_access == false` on a track, show the entire track as locked with message "يجب الاشتراك أولاً"
- Tapping a locked track should navigate to the subscription/purchase flow (normal path entry point)

#### Unit-Level Progress Indicator
- Compute client-side: `stamped topics / visible topics` per unit
- Show as a small progress indicator on the unit header
- When all topics in a unit are stamped, show a unit-level completion badge

#### Interactions
- Tap **stamped** topic → Navigate to Challenge Play Screen (retry mode)
- Tap **open** topic → Navigate to Challenge Play Screen
- Tap **locked** topic → Show lock reason toast/bottom sheet
- Expand/collapse tracks → Animated accordion
- Back button → Return to Subject Selection

### Error Handling

| Status | Code | Action |
|--------|------|--------|
| 404 | `SUBJECT_NOT_FOUND` | Show "المادة غير موجودة" and navigate back |

---

## Screen 3: Challenge Play (Question Flow)

### Purpose

Core gameplay. Student answers all MCQ questions for a topic in one sitting. Questions shown one at a time in random order.

### Question Loading

Questions are pre-cached per topic. The client should fetch the question file from the content delivery layer:

```
GET /cdn/challenges/{topic_id}.json
```

If no CDN file is available, fall back to the existing topic questions already cached in the app from the normal path's review items.

**Question file structure** (per topic):

```json
{
  "topic_id": "TOPIC-001",
  "questions": [
    {
      "item_id": "RI-00001",
      "question_text": "ما هو حل المعادلة 2x + 5 = 11؟",
      "choices": [
        {"id": 1, "text": "x = 2"},
        {"id": 2, "text": "x = 3"},
        {"id": 3, "text": "x = 4"},
        {"id": 4, "text": "x = 6"}
      ],
      "correct_choice": 2,
      "explanation": "2(3) + 5 = 6 + 5 = 11 ✓"
    }
  ]
}
```

### Client Responsibilities

1. **Randomize** question order on each attempt (shuffle locally)
2. **Track** per-question: `item_id`, `correct` (bool), `time_spent` (seconds), `chosen_answer` (1-4)
3. **Show correct answer** immediately after wrong answer (no going back)
4. **Generate `attempt_key`**: Unique string for idempotency — use `{player_id}:{topic_id}:{timestamp_ms}` format
5. **Calculate** score locally for immediate display: `correct_count / total_questions * 100`
6. **Determine** pass/fail locally: `score_pct >= pass_threshold` (get threshold from settings or use default 50%)
7. **Submit** all results in a single POST when all questions answered

### UI Requirements

#### Pre-Challenge Confirmation
Before starting, show a confirmation dialog:

```
┌─────────────────────────────────────────┐
│     المعادلة من الدرجة الثانية          │
│                                          │
│     📝 15 سؤال                           │
│     ⏱️ بدون حد زمني                      │
│     ✅ 50% للنجاح                        │
│                                          │
│  يجب الإجابة على جميع الأسئلة في         │
│  جلسة واحدة. الخروج يلغي المحاولة.       │
│                                          │
│  [إلغاء]          [ابدأ التحدي]          │
└─────────────────────────────────────────┘
```

#### Question Screen

```
┌─────────────────────────────────────────┐
│  ← خروج                    سؤال 3/15   │
│  ████████░░░░░░░░░░░░  20%              │
│                                          │
│  ما هو حل المعادلة 2x + 5 = 11؟         │
│                                          │
│  ┌─────────────────────────────────────┐ │
│  │  A) x = 2                          │ │
│  └─────────────────────────────────────┘ │
│  ┌─────────────────────────────────────┐ │
│  │  B) x = 3              ← selected  │ │
│  └─────────────────────────────────────┘ │
│  ┌─────────────────────────────────────┐ │
│  │  C) x = 4                          │ │
│  └─────────────────────────────────────┘ │
│  ┌─────────────────────────────────────┐ │
│  │  D) x = 6                          │ │
│  └─────────────────────────────────────┘ │
│                                          │
│  [تأكيد الإجابة]                         │
│                                          │
│  ⏱️ 0:45                                │
└─────────────────────────────────────────┘
```

#### After Answering (Correct)

```
┌─────────────────────────────────────────┐
│  ← خروج                    سؤال 3/15   │
│  ████████░░░░░░░░░░░░  20%              │
│                                          │
│  ما هو حل المعادلة 2x + 5 = 11؟         │
│                                          │
│  ┌─────────────────────────────────────┐ │
│  │  A) x = 2                          │ │
│  └─────────────────────────────────────┘ │
│  ┌─────────────────────  ✅ ──────────┐ │
│  │  B) x = 3              صحيح!      │ │
│  └─────────────────────────────────────┘ │
│  ┌─────────────────────────────────────┐ │
│  │  C) x = 4                          │ │
│  └─────────────────────────────────────┘ │
│  ┌─────────────────────────────────────┐ │
│  │  D) x = 6                          │ │
│  └─────────────────────────────────────┘ │
│                                          │
│  [السؤال التالي →]                       │
└─────────────────────────────────────────┘
```

#### After Answering (Incorrect)

```
┌─────────────────────────────────────────┐
│  ← خروج                    سؤال 3/15   │
│  ████████░░░░░░░░░░░░  20%              │
│                                          │
│  ما هو حل المعادلة 2x + 5 = 11؟         │
│                                          │
│  ┌─────────────────────────────────────┐ │
│  │  A) x = 2                          │ │
│  └─────────────────────────────────────┘ │
│  ┌─────────────────────  ✅ ──────────┐ │
│  │  B) x = 3              الإجابة     │ │
│  │                        الصحيحة     │ │
│  └─────────────────────────────────────┘ │
│  ┌─────────────────────────────────────┐ │
│  │  C) x = 4                          │ │
│  └─────────────────────  ❌ ──────────┘ │
│  ┌─────────────────────────────────────┐ │
│  │  D) x = 6                          │ │
│  └─────────────────────────────────────┘ │
│                                          │
│  💡 2(3) + 5 = 6 + 5 = 11 ✓            │
│                                          │
│  [السؤال التالي →]                       │
└─────────────────────────────────────────┘
```

#### Exit Mid-Challenge
- "← خروج" button shows confirmation dialog:
  ```
  هل تريد الخروج من التحدي؟
  سيتم إلغاء المحاولة ولن يُحفظ أي تقدم.
  [متابعة التحدي]    [خروج]
  ```
- If confirmed: discard all local state, navigate back to hierarchy. **No API call. Nothing saved.**

#### Timer
- Per-question timer (display only, not enforced) — shown as `⏱️ M:SS`
- Total attempt timer running in background (submitted as `time_spent`)
- No time limit enforcement (D-027)

#### Interactions
- Select answer → Highlight choice
- Tap "تأكيد الإجابة" → Lock choice, show correct/incorrect, reveal explanation if wrong
- Tap "السؤال التالي" → Advance to next question (no going back)
- Last question answered → Auto-navigate to Result Screen

---

## Screen 4: Result Screen

### Purpose

Show attempt results, XP earned, and next actions.

### API Call — Submit Attempt

```http
POST /api/v1/challenge/attempt
Authorization: Bearer <token>
Content-Type: application/json
```

**Request Body:**

```json
{
  "subject_id": "SUBJ-001",
  "topic_id": "TOPIC-001",
  "attempt_key": "966512345678:TOPIC-001:1709913600000",
  "total_questions": 20,
  "time_spent": 480,
  "questions": [
    {
      "item_id": "RI-00001",
      "correct": true,
      "time_spent": 15,
      "chosen_answer": 2
    },
    {
      "item_id": "RI-00002",
      "correct": false,
      "time_spent": 22,
      "chosen_answer": 4
    }
  ]
}
```

**Request Fields:**

| Field | Type | Constraints | Description |
|-------|------|-------------|-------------|
| `subject_id` | string | required | Subject identifier |
| `topic_id` | string | required | Topic identifier |
| `attempt_key` | string | 1-128 chars, required | Client-generated idempotency key |
| `total_questions` | int | >= 1 | Total MCQ questions for this topic |
| `time_spent` | int | >= 0 | Total attempt time in seconds |
| `questions` | array | >= 1 item | Per-question results |
| `questions[].item_id` | string | required | Review Item identifier |
| `questions[].correct` | bool | required | Client-evaluated correctness |
| `questions[].time_spent` | int | >= 0 | Seconds spent on this question |
| `questions[].chosen_answer` | int | 1-4 | Choice index selected |

**Response (200 OK):**

```json
{
  "attempt_number": 3,
  "score_pct": 70.0,
  "passed": true,
  "stamped": true,
  "xp_earned": 30,
  "total_topic_xp": 70,
  "best_score_pct": 70.0,
  "best_passing_pct": 70.0,
  "is_new_best": true,
  "next_topic": {
    "topic_id": "TOPIC-003",
    "state": "open"
  }
}
```

**Response Fields:**

| Field | Type | Description |
|-------|------|-------------|
| `attempt_number` | int | Sequential attempt number for this topic |
| `score_pct` | float | Score as percentage (0.0-100.0) |
| `passed` | bool | Whether score >= pass threshold (50%) |
| `stamped` | bool | Whether topic is now stamped (stays true once true) |
| `xp_earned` | int | Challenge XP earned **this attempt** (delta only) |
| `total_topic_xp` | int | Cumulative Challenge XP for this topic |
| `best_score_pct` | float | All-time best score percentage |
| `best_passing_pct` | float\|null | Best passing score (null if never passed) |
| `is_new_best` | bool | Whether this attempt set a new best score |
| `next_topic` | object\|null | Next topic that was unlocked by this stamp (null if no new unlock) |

### UI Requirements

#### Result Layout — Passed

```
┌─────────────────────────────────────────┐
│                                          │
│              🎉 نجحت!                    │
│                                          │
│           ┌───────────┐                  │
│           │           │                  │
│           │   70%     │  ← circular      │
│           │           │    score ring     │
│           └───────────┘                  │
│                                          │
│     14 / 20 إجابة صحيحة                 │
│                                          │
│  ┌─────────────────────────────────────┐ │
│  │  ⭐ +30 XP         أفضل: 70%       │ │
│  │  المجموع: 70 XP    المحاولة #3      │ │
│  └─────────────────────────────────────┘ │
│                                          │
│  ✅ تم ختم الموضوع!                     │
│                                          │
│  ┌─────────────────────────────────────┐ │
│  │  🔓 الموضوع التالي متاح الآن:       │ │
│  │  الجذور التربيعية                    │ │
│  │  [ابدأ التحدي التالي →]              │ │
│  └─────────────────────────────────────┘ │
│                                          │
│  [أعد التحدي]        [العودة للقائمة]    │
│                                          │
└─────────────────────────────────────────┘
```

#### Result Layout — Failed

```
┌─────────────────────────────────────────┐
│                                          │
│           حاول مرة أخرى!                │
│                                          │
│           ┌───────────┐                  │
│           │           │                  │
│           │   40%     │  ← red ring      │
│           │           │                  │
│           └───────────┘                  │
│                                          │
│     8 / 20 إجابة صحيحة                  │
│                                          │
│  ┌─────────────────────────────────────┐ │
│  │  ⭐ +40 XP         تحتاج 50%       │ │
│  │  المجموع: 40 XP    المحاولة #1      │ │
│  └─────────────────────────────────────┘ │
│                                          │
│  💡 تحتاج 50% على الأقل لختم الموضوع   │
│                                          │
│  [أعد المحاولة]       [العودة للقائمة]   │
│                                          │
└─────────────────────────────────────────┘
```

#### Result Layout — Retry (No Improvement)

```
┌─────────────────────────────────────────┐
│                                          │
│              أحسنت!                      │
│                                          │
│           ┌───────────┐                  │
│           │   50%     │                  │
│           └───────────┘                  │
│                                          │
│     10 / 20 إجابة صحيحة                 │
│                                          │
│  ┌─────────────────────────────────────┐ │
│  │  ⭐ +0 XP          أفضل: 70%       │ │
│  │  المجموع: 70 XP    المحاولة #4      │ │
│  └─────────────────────────────────────┘ │
│                                          │
│  💡 حسّن نتيجتك لتحصل على المزيد       │
│     من نقاط التحدي                       │
│                                          │
│  [أعد التحدي]        [العودة للقائمة]    │
│                                          │
└─────────────────────────────────────────┘
```

#### Result Layout — New Best (Retry with Improvement)

```
┌─────────────────────────────────────────┐
│                                          │
│           🏆 رقم قياسي جديد!            │
│                                          │
│           ┌───────────┐                  │
│           │   90%     │  ← gold ring     │
│           └───────────┘                  │
│                                          │
│     18 / 20 إجابة صحيحة                 │
│                                          │
│  ┌─────────────────────────────────────┐ │
│  │  ⭐ +20 XP         السابق: 70%     │ │
│  │  المجموع: 90 XP    المحاولة #4      │ │
│  └─────────────────────────────────────┘ │
│                                          │
│  [أعد التحدي]        [العودة للقائمة]    │
│                                          │
└─────────────────────────────────────────┘
```

#### Conditional Elements

| Condition | Show |
|-----------|------|
| `passed == true` && first stamp | "تم ختم الموضوع!" celebration |
| `next_topic != null` | "الموضوع التالي متاح" with navigate button |
| `is_new_best == true` | "رقم قياسي جديد!" header |
| `xp_earned == 0` | "حسّن نتيجتك" improvement hint |
| `passed == false` | "تحتاج 50% على الأقل" threshold hint |

#### Animations
- Score counter animating from 0 to final percentage
- XP earned counter animating from 0 to final value
- Stamp animation (checkmark seal) on first pass
- Confetti or particle effect on new best / first stamp

### Error Handling

| Status | Code | Action |
|--------|------|--------|
| 403 | `SEASON_REQUIRED` | Show "الموسم غير نشط" — token refresh needed |
| 403 | `TOPIC_LOCKED` | Show lock reason — should not happen if UI is correct |
| 404 | `TOPIC_NOT_FOUND` | Show error, navigate back |
| 400 | `VALIDATION_ERROR` | Show generic error, allow retry |
| 409 | `DUPLICATE_ATTEMPT` | **Use cached response** from `detail.response` |
| 409 | `ATTEMPT_IN_PROGRESS` | Show "جاري المعالجة، انتظر قليلاً" |

### Idempotency Handling (Critical)

The `attempt_key` ensures network retries don't double-award XP.

**Client implementation:**

```
1. Generate attempt_key = "{player_id}:{topic_id}:{Date.now()}" at challenge START
2. Store attempt_key in local state for this attempt
3. On submit:
   a. POST /challenge/attempt with attempt_key
   b. If 200 OK → show result
   c. If 409 DUPLICATE_ATTEMPT with response field → show cached result from response
   d. If 409 ATTEMPT_IN_PROGRESS → wait 1s, retry (max 3 retries)
   e. If network error → retry with SAME attempt_key (safe due to idempotency)
4. After showing result, clear local attempt state
```

**Key rule**: Generate `attempt_key` once at the START of the challenge, not at submission time. Reuse it on retries.

---

## Screen 5: Challenge Leaderboard

### Purpose

Plan-scoped rankings by Challenge XP with optional subject filter.

### API Calls

#### Top Rankings

```http
GET /api/v1/challenge/leaderboard?subject_id={optional}&limit=20&offset=0
Authorization: Bearer <token>
```

**Response:**

```json
{
  "subject_id": null,
  "entries": [
    {
      "rank": 1,
      "player_id": "966511111111",
      "display_name": "أحمد",
      "xp": 520,
      "avatar": "avatar_01",
      "is_me": false
    },
    {
      "rank": 1,
      "player_id": "966522222222",
      "display_name": "سارة",
      "xp": 520,
      "avatar": "avatar_03",
      "is_me": false
    },
    {
      "rank": 3,
      "player_id": "966533333333",
      "display_name": "محمد",
      "xp": 340,
      "avatar": "avatar_02",
      "is_me": true
    }
  ],
  "total_players": 45
}
```

#### My Rank

```http
GET /api/v1/challenge/leaderboard/me?subject_id={optional}
Authorization: Bearer <token>
```

**Response:**

```json
{
  "rank": 3,
  "xp": 340,
  "xp_to_next": 180,
  "neighbors": [
    {
      "rank": 1,
      "player_id": "966522222222",
      "display_name": "سارة",
      "xp": 520,
      "avatar": "avatar_03",
      "is_me": false
    },
    {
      "rank": 3,
      "player_id": "966533333333",
      "display_name": "محمد",
      "xp": 340,
      "avatar": "avatar_02",
      "is_me": true
    },
    {
      "rank": 4,
      "player_id": "966544444444",
      "display_name": "فاطمة",
      "xp": 200,
      "avatar": "avatar_05",
      "is_me": false
    }
  ],
  "total_players": 45
}
```

**Response Fields — LeaderboardEntry:**

| Field | Type | Description |
|-------|------|-------------|
| `rank` | int | Dense rank (tied players share same rank) |
| `player_id` | string | Player identifier |
| `display_name` | string | Arabic display name |
| `xp` | int | Challenge XP total |
| `avatar` | string\|null | Avatar asset identifier |
| `is_me` | bool | Whether this entry is the requesting player |

**Response Fields — MyRankResponse:**

| Field | Type | Description |
|-------|------|-------------|
| `rank` | int\|null | Player's rank (null if no Challenge XP yet) |
| `xp` | int | Player's total Challenge XP |
| `xp_to_next` | int\|null | XP needed to reach next rank (null if #1 or unranked) |
| `neighbors` | array | Players around the requesting player |
| `total_players` | int | Total ranked players in this leaderboard |

### UI Requirements

#### Layout

```
┌─────────────────────────────────────────┐
│  لوحة المتصدرين — التحدي               │
│                                          │
│  [جميع المواد ▼]  ← subject filter      │
│                                          │
│  ┌─ My Rank Card ───────────────────┐   │
│  │  #3  محمد     ⭐ 340 XP          │   │
│  │  180 XP للترتيب التالي           │   │
│  └──────────────────────────────────┘   │
│                                          │
│  ┌─ Leaderboard ────────────────────┐   │
│  │  🥇 #1  أحمد        ⭐ 520 XP   │   │
│  │  🥇 #1  سارة        ⭐ 520 XP   │   │
│  │  🥉 #3  محمد   ← me ⭐ 340 XP   │   │
│  │     #4  فاطمة       ⭐ 200 XP   │   │
│  │     #5  عمر         ⭐ 150 XP   │   │
│  │     ...                          │   │
│  └──────────────────────────────────┘   │
└─────────────────────────────────────────┘
```

#### Subject Filter
- Dropdown/picker at top: "جميع المواد" (All Subjects) + individual subjects from plan
- When `subject_id` is omitted → plan-level leaderboard (XP summed across all subjects)
- When `subject_id` is provided → subject-level leaderboard

#### Dense Ranking Display
- Tied players share the same rank number
- Rank gap after ties: 1, 1, 3 (not 1, 2, 3)
- Medal icons for top 3

#### My Rank Card
- Always visible at top (sticky)
- Shows rank, XP, and XP needed for next tier
- If unranked (`rank == null`): show "ابدأ التحدي لتظهر في اللوحة" (Start a challenge to appear)

#### Empty State
- No players: "لا يوجد متسابقون بعد — كن الأول!" (No contestants yet — be the first!)
- No plan: "يجب أن يكون لديك خطة لرؤية لوحة المتصدرين" (You need a plan to see the leaderboard)

#### Refresh Behavior
- **Do NOT auto-refresh** on a timer
- Refresh on: screen focus, pull-to-refresh, returning from attempt result
- The `lb_refresh_interval` (default 300s) is a **minimum interval** — do not fetch more frequently than this
- Cache last response locally and show immediately, fetch fresh data in background

#### Pagination
- Fetch page 1 (top 20) on load
- Infinite scroll for additional pages (`offset` parameter)

---

## Dart/Flutter Type Definitions

```dart
// === Request Models ===

class QuestionDetail {
  final String itemId;
  final bool correct;
  final int timeSpent;
  final int chosenAnswer;
}

class AttemptRequest {
  final String subjectId;
  final String topicId;
  final String attemptKey;
  final int totalQuestions;
  final int timeSpent;
  final List<QuestionDetail> questions;
}

// === Response Models ===

class ChallengeSubjectSummary {
  final String subjectId;
  final String subjectName;
  final int totalTopics;
  final int stampedTopics;
  final int totalChallengeXp;
}

class TopicState {
  final String topicId;
  final String topicName;
  final String state;          // "locked" | "open" | "stamped"
  final int mcqCount;
  final double? bestScorePct;
  final double? bestPassingPct;
  final int totalXp;
  final int attemptCount;
  final bool normalPathComplete;
  final bool hasAccess;
  final String? lockReason;    // "NO_ACCESS" | "NORMAL_PATH_INCOMPLETE" | "PREVIOUS_NOT_STAMPED"
}

class UnitState {
  final String unitId;
  final String unitName;
  final List<TopicState> topics;
}

class TrackState {
  final String trackId;
  final String trackName;
  final bool hasAccess;
  final List<UnitState> units;
}

class ChallengeHierarchyResponse {
  final String subjectId;
  final List<TrackState> tracks;
}

class NextTopicInfo {
  final String topicId;
  final String state;
}

class AttemptResponse {
  final int attemptNumber;
  final double scorePct;
  final bool passed;
  final bool stamped;
  final int xpEarned;
  final int totalTopicXp;
  final double bestScorePct;
  final double? bestPassingPct;
  final bool isNewBest;
  final NextTopicInfo? nextTopic;
}

class LeaderboardEntry {
  final int rank;
  final String playerId;
  final String displayName;
  final int xp;
  final String? avatar;
  final bool isMe;
}

class LeaderboardResponse {
  final String? subjectId;
  final List<LeaderboardEntry> entries;
  final int totalPlayers;
}

class MyRankResponse {
  final int? rank;
  final int xp;
  final int? xpToNext;
  final List<LeaderboardEntry> neighbors;
  final int totalPlayers;
}
```

---

## Client-Side State Management

### Challenge Session State (In-Memory Only)

```dart
class ChallengeSession {
  final String topicId;
  final String subjectId;
  final String attemptKey;          // Generated at session start
  final List<Question> questions;    // Shuffled at start
  final DateTime startedAt;

  int currentIndex = 0;
  List<QuestionResult> results = [];
  int totalTimeSpent = 0;

  // Computed
  int get correctCount => results.where((r) => r.correct).length;
  double get scorePct => (correctCount / questions.length) * 100;
  bool get passed => scorePct >= passThreshold;
  bool get isComplete => currentIndex >= questions.length;
}
```

**Key rules:**
- Session is created when student taps "ابدأ التحدي"
- Session lives only in memory — never persisted locally
- On exit/abandon → session is discarded entirely
- `attemptKey` is generated at creation, reused on submission retries

### Hierarchy Cache

```dart
class ChallengeCache {
  // Cache subject list (refresh on pull-to-refresh)
  List<ChallengeSubjectSummary>? subjects;
  DateTime? subjectsLastFetched;

  // Cache per-subject hierarchy (refresh on return from result screen)
  Map<String, ChallengeHierarchyResponse> hierarchies = {};
  Map<String, DateTime> hierarchyLastFetched = {};

  // Cache leaderboard (respect refresh interval)
  LeaderboardResponse? leaderboard;
  MyRankResponse? myRank;
  DateTime? leaderboardLastFetched;

  // Settings
  int passThreshold = 50;  // Default, override from backend
  int xpPerQuestion = 5;   // Default, override from backend
  int lbRefreshInterval = 300; // Seconds
}
```

**Cache invalidation:**
- Subject list: invalidate on pull-to-refresh or returning from any challenge
- Hierarchy: invalidate for the specific subject after completing an attempt
- Leaderboard: respect `lbRefreshInterval` minimum; invalidate on pull-to-refresh

---

## XP System — Client-Side Understanding

The client can **predict** XP before the server response for instant feedback:

```dart
int predictXpEarned(int correctCount, int previousBestCorrect, int xpPerQuestion) {
  final delta = correctCount - previousBestCorrect;
  if (delta <= 0) return 0;
  return delta * xpPerQuestion;
}
```

**Important**: The server is the source of truth. Always update UI with server response, not prediction. The prediction is only for the animation during submission loading.

### XP Isolation Guarantees

Challenge XP is **completely separate** from the main game:

| System | Shows Challenge XP? |
|--------|---------------------|
| Main wallet/XP display | **No** |
| Main leaderboard | **No** |
| Profile stats | **No** |
| Streak system | **No** |
| Level system | **No** |
| Challenge Hub screens | **Yes** |
| Challenge leaderboard | **Yes** |

The client must never mix Challenge XP into main XP displays.

---

## Offline & Error Handling Strategy

### Network Failure During Submission

```
1. Show loading spinner
2. POST /challenge/attempt (with attempt_key)
3. If network error:
   a. Show "فشل الإرسال — يتم إعادة المحاولة" (Submission failed — retrying)
   b. Retry with exponential backoff (1s, 2s, 4s) — SAME attempt_key
   c. Max 3 retries
   d. If all fail: show "فشل حفظ النتيجة. هل تريد المحاولة لاحقاً؟"
      Option 1: "أعد المحاولة" → retry now
      Option 2: "إلغاء" → discard (warn: results will be lost)
4. On 409 DUPLICATE_ATTEMPT with response: treat as success, show cached result
```

### Network Failure During Browsing

- Show cached hierarchy data if available
- Show offline indicator bar
- Disable "ابدأ التحدي" buttons (cannot submit without network)

### Token Expiry Mid-Challenge

- If token expires during question flow: continue locally (no API needed)
- On submission: refresh token, then submit with new token
- If refresh fails: same retry flow as network failure

---

## Accessibility (a11y) Requirements

- All text is Arabic RTL
- Minimum touch target: 48x48dp
- Color contrast: 4.5:1 minimum for text
- Screen reader support: announce question number, choices, correct/incorrect feedback
- Lock state icons must have semantic labels (not just color)
- Progress indicators must have text alternatives

---

## Analytics Events

Track the following client-side events:

| Event | Trigger | Properties |
|-------|---------|------------|
| `ch_hub_opened` | Challenge Hub tab opened | `subject_count` |
| `ch_subject_selected` | Subject card tapped | `subject_id`, `stamped_topics`, `total_topics` |
| `ch_challenge_started` | "ابدأ التحدي" confirmed | `subject_id`, `topic_id`, `mcq_count`, `is_retry` |
| `ch_challenge_abandoned` | Exit confirmed mid-challenge | `subject_id`, `topic_id`, `questions_answered`, `questions_total` |
| `ch_challenge_completed` | All questions answered | `subject_id`, `topic_id`, `score_pct`, `time_spent` |
| `ch_attempt_submitted` | Server response received | `subject_id`, `topic_id`, `passed`, `xp_earned`, `is_new_best` |
| `ch_attempt_failed` | Submission error (after retries) | `subject_id`, `topic_id`, `error_code` |
| `ch_leaderboard_opened` | Leaderboard screen opened | `subject_filter` |
| `ch_next_topic_tapped` | "ابدأ التحدي التالي" on result | `from_topic_id`, `to_topic_id` |

---

## Testing Checklist

### Functional Tests

- [ ] Subject list shows only plan subjects
- [ ] Hierarchy renders correct topic states (locked/open/stamped)
- [ ] Empty topics (mcq_count == 0) are hidden
- [ ] Locked topics are not tappable
- [ ] Lock reason messages display correctly
- [ ] Questions load and shuffle on each attempt
- [ ] Answer selection, confirmation, and correct/wrong feedback work
- [ ] Cannot go back to previous questions
- [ ] Exit mid-challenge discards all data
- [ ] Submission succeeds and shows correct result
- [ ] First pass shows stamp celebration + next topic unlock
- [ ] Retry with improvement shows XP delta
- [ ] Retry without improvement shows 0 XP
- [ ] New best score shows "رقم قياسي جديد"
- [ ] Leaderboard loads with correct rankings
- [ ] Subject filter changes leaderboard data
- [ ] Own rank card shows correct rank and XP-to-next

### Idempotency Tests

- [ ] Network retry with same attempt_key returns cached response (409 → success)
- [ ] Different attempt_key for same topic creates new attempt
- [ ] attempt_key generated at challenge start, not at submission

### Edge Cases

- [ ] Topic with 1 question: full flow works
- [ ] Topic with 100+ questions: performance acceptable, scroll works
- [ ] All topics stamped in unit: unit shows completion badge
- [ ] All topics stamped in subject: subject card shows full progress
- [ ] No plan assigned: leaderboard shows empty state
- [ ] Token expires mid-challenge: refresh and submit works
- [ ] Network failure during submission: retry with same key works
- [ ] Challenge XP never shows in main XP displays

### Performance Tests

- [ ] Hierarchy loads < 1s
- [ ] Attempt submission < 2s
- [ ] Leaderboard loads < 1s
- [ ] Transition from last question to result screen < 500ms

---

## Appendix A: Complete API Summary

| Endpoint | Method | Purpose | Rate Limit Scope |
|----------|--------|---------|-----------------|
| `/challenge/hierarchy` | GET | List subjects with stats | `ch_hierarchy` |
| `/challenge/hierarchy/{subject_id}` | GET | Browse tracks/units/topics | `ch_hierarchy` |
| `/challenge/attempt` | POST | Submit completed attempt | `ch_attempt` |
| `/challenge/leaderboard` | GET | Top rankings by Challenge XP | `ch_leaderboard` |
| `/challenge/leaderboard/me` | GET | Own rank + neighbors | `ch_leaderboard` |

All under base path: `/api/v1/`

---

## Appendix B: Question File Schema

```json
{
  "topic_id": "string",
  "questions": [
    {
      "item_id": "string (Review Item ID)",
      "question_text": "string (Arabic)",
      "choices": [
        {"id": 1, "text": "string"},
        {"id": 2, "text": "string"},
        {"id": 3, "text": "string"},
        {"id": 4, "text": "string"}
      ],
      "correct_choice": "int (1-4)",
      "explanation": "string | null"
    }
  ]
}
```

**Note**: `correct_choice` is needed client-side to show the correct answer after wrong answers. The server independently validates using its own source (Review Item table), so the client `correct` flag is verified server-side — no cheating vector.

---

## Appendix C: Design Decision Quick Reference

| Decision | Impact on Client |
|----------|-----------------|
| D-004: Empty topics hidden + auto-stamp | Filter topics where `mcq_count == 0` from render |
| D-007: No going back | Disable back navigation during question flow |
| D-009: Abandoned = discarded | No save on exit, no API call |
| D-013: Frontend evaluates correctness | Client computes `correct` bool using `correct_choice` from question file |
| D-014: Single submission per attempt | Batch all question results into one POST |
| D-016: Unit progress computed client-side | Count stamped/visible topics per unit locally |
| D-020: Delta-only XP | Display `xp_earned` from server, not total score × rate |
| D-023: Periodic leaderboard refresh | Respect `lb_refresh_interval`, don't poll faster |
| D-027: No time limit | Timer is display-only, never enforced |

---

## Changelog

| Version | Date | Changes |
|---------|------|---------|
| 1.0 | 2026-03-08 | Initial PRD based on 038-challenge-hub backend implementation |
