# Live Challenges — Frontend Mobile App PRD
**Audience**: Frontend mobile app AI agent / developer
**Backend Status**: Implemented and deployed

---

## 1. Feature Overview

Live Challenges is a timed exam feature where students join a scheduled event via a shared link, wait in a synchronized waiting room, answer multiple-choice questions at their own pace within a time limit, receive instant scores, and see a leaderboard after the event ends.

**Key UX principles**:
- All timing is **server-authoritative** — never trust the device clock
- The flow is linear: **Link > Join > Waiting Room > Exam > Score > Leaderboard**
- WebSocket is used only during the waiting room phase for countdown + start signal
- The exam itself is entirely REST-based (no WebSocket during questions)
- Arabic-first UI (RTL layout)

---

## 2. User Flow (Step by Step)

### Flow Diagram

```
[Shared Link / Deep Link]
        |
        v
[GET /live-challenge/{event_id}]  ──> Event Detail Screen
        |                               (show event info, status, capacity)
        |
   [Status?] ─── Draft ──────────> "Event hasn't started yet" message
        |
        ├── Waiting or Active ──> Show "Join" button
        |
        v
[POST /live-challenge/{event_id}/join]
        |
        ├── Success ──> Connect WebSocket ──> Waiting Room Screen
        |                                        |
        |                                   [countdown ticks]
        |                                        |
        |                                   [type: "exam_start"] received
        |                                        |
        |                                        v
        |                                   Exam Screen
        |                                   (questions one-by-one)
        |                                        |
        |                                   [all answered or timer expires]
        |                                        |
        |                                        v
        |                              [POST /live-challenge/{event_id}/submit]
        |                                        |
        |                                        v
        |                                   Score Screen (instant)
        |                                        |
        |                                   [wait for event to end]
        |                                        |
        |                                        v
        |                              [GET /live-challenge/{event_id}/leaderboard]
        |                                        |
        |                                        v
        |                                   Leaderboard Screen
        |
        ├── Ended ──> Show result/leaderboard directly
        |
        └── Capacity Full / Not Eligible ──> Error message
```

### 2.1. Entry Point — Deep Link

The student receives a shared link from the admin (e.g., via social media, messaging app). The link contains the `event_id` (format: `LC-00001`).

**Action**: Parse the event ID from the link and call `GET /live-challenge/{event_id}`.

### 2.2. Event Detail Screen

Display event information. This is the "landing page" for the challenge.

**Fields to display**:
| Field | Description | Notes |
|-------|-------------|-------|
| `event_name` | Title of the challenge | Large, prominent |
| `description` | Rich HTML description | Render safely (HTML from admin) |
| `status` | Current state | Drive UI state (see below) |
| `scheduled_start` | When the waiting room opens | Show as formatted date/time |
| `exam_duration` | Exam length in minutes | "10 minutes" |
| `question_count` | Number of questions | "20 questions" |
| `capacity` | Max participants | Show as "X / Y joined" |
| `current_count` | Current participant count | Live counter |
| `is_paid` | Whether event is paid | Show badge if true (payment not enforced yet) |
| `eligible_plans` | Required study plans | Show if non-empty; empty = open to all |
| `participation_xp` | XP for participating | Show XP rewards section |
| `first_place_xp` | XP for 1st place | |
| `second_place_xp` | XP for 2nd place | |
| `third_place_xp` | XP for 3rd place | |
| `default_xp` | XP for 4th+ place | |

**Player-specific flags** (from the same response):
| Flag | Meaning | UI Action |
|------|---------|-----------|
| `has_joined == false` | Not yet joined | Show "Join" button |
| `has_joined == true && has_submitted == false` | Joined but hasn't submitted | Reconnect to WebSocket / resume exam |
| `has_submitted == true` | Already submitted | Show "View Results" button |

**Status-driven UI**:
| Status | UI State |
|--------|----------|
| `Draft` | "Event hasn't started yet. Come back at [scheduled_start]" |
| `Waiting` | Show "Join" button (if not joined) or auto-reconnect WS (if joined) |
| `Active` | Show "Join" button for late joiners (if not joined, capacity permitting) OR resume exam (if joined) |
| `Ended` | Show "View Results" / "View Leaderboard" buttons |

### 2.3. Join Flow

**Trigger**: User taps "Join" button.

**API Call**: `POST /live-challenge/{event_id}/join`

**Success Response** (200):
```json
{
  "joined": true,
  "event_id": "LC-00001",
  "position": 143,
  "waiting_room_duration": 180,
  "countdown_remaining": 45,
  "ws_url": "/api/v1/live-challenge/LC-00001/ws?token="
}
```

**What to do with the response**:
1. Store `position` — display as "You are participant #143"
2. Use `countdown_remaining` as the initial countdown value (server-authoritative)
3. Append the user's JWT access token to `ws_url` and connect the WebSocket
4. Navigate to the Waiting Room screen

**Error Handling**:
| HTTP Code | `detail` | User Message |
|-----------|----------|-------------|
| 400 | `EVENT_NOT_JOINABLE` | "This event is not accepting participants right now" |
| 403 | `PLAN_NOT_ELIGIBLE` | "Your study plan is not eligible for this event" |
| 409 | `ALREADY_JOINED` | Skip join, go directly to WS/exam (treat as success) |
| 422 | `CAPACITY_FULL` | "This event is full. Better luck next time!" |

**Important**: `ALREADY_JOINED` is not really an error — the student may have closed the app and returned. Treat it as a successful re-entry and reconnect to WebSocket.

### 2.4. Waiting Room Screen (WebSocket)

**Connection**: `ws://[host]:8002/api/v1/live-challenge/{event_id}/ws?token={jwt_access_token}`

The WebSocket is **server-push only** — the client does not send any messages (just maintains the connection).

**Messages received from server**:

#### Message 1: `countdown` (periodic, every 1 second)
```json
{
  "type": "countdown",
  "remaining": 45,
  "participant_count": 234
}
```

**UI**:
- Display a large countdown timer (MM:SS format)
- Show live participant count: "234 participants joined"
- Animate the countdown
- Use `remaining` from the server (not local clock) — trust the server

#### Message 2: `exam_start` (sent once)
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

**CRITICAL — What to do**:
1. **Store all questions locally** — they are delivered once, all at once
2. **Store `exam_end_ts`** — this is the server-authoritative deadline
3. Start the global exam countdown timer based on `exam_end_ts` minus current server time
4. If `enable_question_timer` is true, show a per-question timer of `question_time_limit` seconds
5. **Navigate to the Exam Screen** immediately
6. **Close the WebSocket connection** — it is no longer needed during the exam
7. Questions do NOT contain `correct_answer` — it is never sent to the client before submission

#### Message 3: `event_ended`
```json
{
  "type": "event_ended"
}
```

This means the exam time expired. If the student hasn't submitted yet, their attempt is lost. Show a "Time's Up" screen.

**Reconnection handling**:
- If the WebSocket disconnects, reconnect automatically
- On reconnect during `Waiting` status: you'll receive `countdown` messages normally
- On reconnect during `Active` status: you'll immediately receive the `exam_start` message with all questions (late join / reconnect behavior)
- On reconnect after `Ended`: connection will be rejected with close code `4000`

### 2.5. Exam Screen

The exam is entirely client-side navigation through questions, with a single server call at submission.

**Layout**:
- **Global timer** (top): Countdown to `exam_end_ts` (mandatory, always visible)
- **Per-question timer** (if `enable_question_timer` is true): Countdown of `question_time_limit` seconds, resets for each question
- **Question area**: Question text + 4 options (A/B/C/D)
- **Navigation**: "Next" button (or auto-advance when per-question timer expires)
- **Progress indicator**: "Question 3 / 20"
- **Submit button**: Available after the last question or at any time to submit early

**Question navigation rules**:
- Students progress through questions **one at a time** (forward only recommended, but you may allow review)
- When `enable_question_timer` is true and the per-question timer expires:
  - Auto-advance to the next question
  - Record the unanswered question as `selected: null`
- Students can skip a question (set `selected: null`)
- The client stores all answers locally as an array

**Global timer behavior**:
- Calculate remaining time as: `exam_end_ts` (from server) minus device time
- When global timer reaches zero: **auto-submit whatever answers have been recorded**
- If the student hasn't answered any questions, submit an array of all `null` selections

**Per-question timer** (client-side only):
- The server does NOT enforce per-question time
- If enabled, start a `question_time_limit` second countdown for each question
- On expiry, auto-advance and mark as unanswered (`null`)

**Local answer storage format** (build this as the student answers):
```json
[
  {"question_idx": 0, "selected": "B"},
  {"question_idx": 1, "selected": null},
  {"question_idx": 2, "selected": "A"},
  ...
]
```

### 2.6. Submission

**Trigger**: Student taps "Submit" OR global timer expires.

**API Call**: `POST /live-challenge/{event_id}/submit`

**Request body**:
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

**Rules**:
- Array length MUST match `total_questions` from the `exam_start` message
- `question_idx` is 0-based
- `selected` is `"A"`, `"B"`, `"C"`, `"D"`, or `null` (unanswered)
- This is a **one-time operation** — duplicate submissions are rejected

**Success Response** (200):
```json
{
  "score": 75.0,
  "correct_count": 15,
  "total_questions": 20,
  "submitted_at": "2026-03-07 14:08:32",
  "corrections": [
    {"question_idx": 1, "selected": "C", "correct_answer": "B"},
    {"question_idx": 2, "selected": null, "correct_answer": "D"},
    {"question_idx": 7, "selected": "A", "correct_answer": "C"}
  ]
}
```

**Notes**:
- `score` is a float out of 100 (e.g., 75.0 means 75%)
- `corrections` is a list of ONLY the wrong answers — if the student got all correct, it's an empty array
- `corrections` is `null` (not returned) if `show_correct_answers` is disabled on the event
- Navigate to the **Score Screen** immediately

**Error Handling**:
| HTTP Code | `detail` | User Message |
|-----------|----------|-------------|
| 400 | `EVENT_NOT_ACTIVE` | "The exam has ended. Your answers were not submitted in time." |
| 403 | `NOT_A_PARTICIPANT` | "You are not a participant in this event" |
| 409 | `ALREADY_SUBMITTED` | "You have already submitted your answers" — show previous result |

### 2.7. Score Screen

Displayed immediately after submission.

**Display**:
- **Score**: Large, prominent — e.g., "75 / 100" or "75%"
- **Correct count**: "15 out of 20 correct"
- **Submitted at**: Formatted timestamp

**If `corrections` is not null** (show_correct_answers enabled):
- Show a list of incorrect answers with:
  - Question number (question_idx + 1 for display)
  - What the student selected
  - The correct answer
- Optionally allow scrolling through to review each wrong answer with the original question text (from locally stored questions)

**If `corrections` is null**:
- Just show the score, no detailed breakdown

**Navigation**:
- "View Leaderboard" button (may show "Leaderboard available soon..." if event hasn't ended yet)
- The leaderboard is only available after the event ends — poll or check on tap

### 2.8. Result Screen (Re-entry)

When a student returns to a completed event (via link or history), use:

**API Call**: `GET /live-challenge/{event_id}/result`

**Response** (200):
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

**Notes**:
- `rank` is `null` until the event ends and leaderboard is computed (within 60 seconds of event ending)
- `xp_awarded` is `null` until XP distribution completes (within 120 seconds of event ending)
- `corrections` follows same rules as submit response
- If rank/xp is null, show a loading indicator or "Calculating..." message

**Error**: 404 with `NO_PARTICIPATION` if the student didn't participate.

### 2.9. Leaderboard Screen

**API Call**: `GET /live-challenge/{event_id}/leaderboard`

**Response** (200):
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

**Display**:
- Top 20 leaderboard entries in a list/table
- Note: Tied scores share the same rank (standard competition ranking: 1, 1, 3 — not 1, 1, 2)
- Highlight the current student's position if they appear in the top 20
- Below the top 20, show "Your rank: #42 with a score of 75.0"
- `my_rank` and `my_score` are `null` if `show_student_rank` is disabled on the event
- Show total participants: "out of 487 participants"

**Error**: 400 with `EVENT_NOT_ENDED` if the event hasn't ended yet — show "Leaderboard will be available after the event ends."

---

## 3. API Reference (Complete)

All endpoints require `Authorization: Bearer <jwt_access_token>` header.

| Method | Endpoint | Purpose | Rate Limit |
|--------|----------|---------|------------|
| GET | `/live-challenge/{event_id}` | Event details + player flags | Standard |
| POST | `/live-challenge/{event_id}/join` | Join event | 5/min |
| POST | `/live-challenge/{event_id}/submit` | Submit answers | 2/min |
| GET | `/live-challenge/{event_id}/result` | Student's own result | Standard |
| GET | `/live-challenge/{event_id}/leaderboard` | Top 20 leaderboard | Standard |
| WS | `/live-challenge/{event_id}/ws?token={jwt}` | Waiting room WebSocket | N/A |

### Authentication

- REST endpoints: `Authorization: Bearer <jwt_access_token>` header
- WebSocket: JWT passed as `token` query parameter (not header)
- The same access token used for all other Memora APIs

### Datetime Format

All datetime strings from the server use the format: `YYYY-MM-DD HH:MM:SS` (no timezone, no Z suffix, no milliseconds). Example: `"2026-03-07 14:13:00"`.

Treat all times as server-local time (UTC+3 / Arabia Standard Time). The server is authoritative for all timing.

---

## 4. Screen-by-Screen Summary

### Screen 1: Event Detail
- **Entry**: Deep link with event_id
- **API**: `GET /live-challenge/{event_id}`
- **Actions**: Join button, View Results button (conditional on status)
- **Polling**: Optional — refresh every 10-15 seconds to update `current_count` and `status` while waiting

### Screen 2: Waiting Room
- **Entry**: After successful join
- **Connection**: WebSocket
- **Display**: Countdown timer (from server `remaining`), participant count, event name
- **Exit**: Automatic on `exam_start` message → navigate to Exam Screen
- **Edge case**: `event_ended` message → show "Event ended" dialog

### Screen 3: Exam
- **Entry**: After receiving `exam_start` via WebSocket
- **Data**: Questions from `exam_start` message (stored locally)
- **Display**: One question at a time, global timer, optional per-question timer
- **Navigation**: Forward through questions, submit when done
- **Exit**: Submit button or global timer expiry → auto-submit → navigate to Score Screen

### Screen 4: Score (Instant Result)
- **Entry**: Immediately after submission
- **Data**: From `POST /submit` response
- **Display**: Score, correct count, corrections (if enabled)
- **Actions**: "View Leaderboard" button (available after event ends)

### Screen 5: Leaderboard
- **Entry**: After event ends, from Score screen or re-entry
- **API**: `GET /live-challenge/{event_id}/leaderboard`
- **Display**: Top 20 list, student's own rank, total participants

### Screen 6: Result (Re-entry)
- **Entry**: When returning to a completed event
- **API**: `GET /live-challenge/{event_id}/result`
- **Display**: Same as Score screen + rank + XP awarded

---

## 5. Edge Cases & Error Handling

### Network / Connection Issues

| Scenario | Handling |
|----------|----------|
| WebSocket disconnects during waiting room | Auto-reconnect. On reconnect you get current countdown state. |
| WebSocket disconnects during Active phase | Reconnect WebSocket — server sends `exam_start` again with all questions. Student can continue answering. |
| App backgrounded during exam | Keep the global timer running. On foreground, recalculate remaining time from `exam_end_ts`. |
| Submit fails (network error) | Retry the submit call. The server is idempotent — if already submitted, you get `ALREADY_SUBMITTED` (409). |
| App killed during exam | On re-open, call `GET /live-challenge/{event_id}` — if `has_joined=true` and `has_submitted=false` and event is Active, reconnect WS to get questions again. If event is Ended, the submission was lost. |

### State Conflicts

| Scenario | Handling |
|----------|----------|
| Student opens link for Draft event | Show "Event starts at [scheduled_start]". Optionally set a local reminder. |
| Student tries to join full event | Show "CAPACITY_FULL" error message |
| Student on wrong plan | Show "PLAN_NOT_ELIGIBLE" error with explanation |
| Student tries to submit twice | 409 `ALREADY_SUBMITTED` — navigate to result screen |
| Student tries to submit after event ended | 400 `EVENT_NOT_ACTIVE` — show "Time's up" message |
| Student never submitted, event ended | No score recorded. If they call `/result`, they get 404 `NO_PARTICIPATION` — well, they have a participation record but no score. Show "You didn't submit in time." |
| Global timer expires mid-question | Auto-submit immediately with whatever answers are recorded (unanswered = null) |
| Leaderboard not ready yet | 400 `EVENT_NOT_ENDED` — show "Calculating results..." and let user retry in 30-60 seconds |

### Late Joining

- A student can join during the **Active** phase (not just Waiting)
- On join during Active: `countdown_remaining` will be 0
- After joining late, connect to WebSocket → server immediately sends `exam_start` with all questions
- The student gets the remaining exam time (from `exam_end_ts`), not the full duration
- All other flow is identical

---

## 6. Data Models (TypeScript/Dart Reference)

```typescript
// Event Detail
interface EventDetail {
  event_id: string;           // "LC-00001"
  event_name: string;
  description: string | null; // HTML
  status: "Draft" | "Waiting" | "Active" | "Ended";
  scheduled_start: string;    // "YYYY-MM-DD HH:MM:SS"
  exam_start_ts: string | null;
  exam_end_ts: string | null;
  waiting_room_duration: number; // seconds
  exam_duration: number;         // minutes
  enable_question_timer: boolean;
  question_time_limit: number;   // seconds
  capacity: number;
  current_count: number;
  is_paid: boolean;
  show_correct_answers: boolean;
  show_student_rank: boolean;
  participation_xp: number;
  first_place_xp: number;
  second_place_xp: number;
  third_place_xp: number;
  default_xp: number;
  question_count: number;
  eligible_plans: string[];    // plan IDs, empty = no restriction
  has_joined: boolean;         // player-specific
  has_submitted: boolean;      // player-specific
}

// Join Response
interface JoinResponse {
  joined: boolean;              // always true on success
  event_id: string;
  position: number;             // 1-indexed
  waiting_room_duration: number;
  countdown_remaining: number;  // seconds until exam starts
  ws_url: string;               // append JWT token
}

// WebSocket Messages
interface WSCountdown {
  type: "countdown";
  remaining: number;            // seconds
  participant_count: number;
}

interface WSQuestion {
  idx: number;                  // 0-based
  question_text: string;
  option_a: string;
  option_b: string;
  option_c: string;
  option_d: string;
}

interface WSExamStart {
  type: "exam_start";
  exam_end_ts: string;          // "YYYY-MM-DD HH:MM:SS"
  total_questions: number;
  enable_question_timer: boolean;
  question_time_limit: number;
  questions: WSQuestion[];       // no correct_answer!
}

interface WSEventEnded {
  type: "event_ended";
}

// Submit
interface SubmitAnswer {
  question_idx: number;         // 0-based
  selected: "A" | "B" | "C" | "D" | null;
}

interface SubmitRequest {
  answers: SubmitAnswer[];
}

interface Correction {
  question_idx: number;
  selected: "A" | "B" | "C" | "D" | null;
  correct_answer: "A" | "B" | "C" | "D";
}

interface SubmitResponse {
  score: number;                // 0-100
  correct_count: number;
  total_questions: number;
  submitted_at: string;
  corrections: Correction[] | null;  // null if show_correct_answers disabled
}

// Result
interface ResultResponse {
  event_id: string;
  event_name: string;
  score: number;
  correct_count: number;
  total_questions: number;
  rank: number | null;          // null until leaderboard computed
  total_participants: number;
  xp_awarded: number | null;    // null until XP distributed
  submitted_at: string | null;
  corrections: Correction[] | null;
}

// Leaderboard
interface LeaderboardEntry {
  rank: number;
  player: string;
  display_name: string;
  score: number;
}

interface LeaderboardResponse {
  event_id: string;
  event_name: string;
  status: string;
  leaderboard: LeaderboardEntry[];  // top 20
  my_rank: number | null;           // null if show_student_rank disabled
  my_score: number | null;
  total_participants: number;
}
```

---

## 7. Timing & Performance Expectations

| Operation | Expected Latency |
|-----------|-----------------|
| GET event detail | < 500ms |
| POST join | < 200ms |
| WebSocket connection | < 1s |
| Countdown messages | Every 1 second |
| POST submit (score returned) | < 2 seconds |
| Leaderboard available after event ends | Within 60 seconds |
| XP awarded after event ends | Within 120 seconds |

---

## 8. UI/UX Recommendations

### Waiting Room
- Full-screen countdown with large digits
- Animated counter or circular progress
- Show participant count growing ("324 students waiting...")
- Optional excitement elements (pulse animation, sound)
- Disable back navigation (or confirm exit)

### Exam
- Clean, distraction-free interface
- Large, readable question text (Arabic RTL)
- Clear A/B/C/D option buttons with selection highlight
- Prominent global timer (always visible, turns red when < 60s)
- Per-question timer as a progress bar (if enabled)
- Question navigation dots or progress bar at top
- "Submit" button only after last question (or "Submit Early" option)
- Confirm dialog before submission: "Are you sure? You have X unanswered questions."

### Score
- Celebratory animation for high scores (> 80%)
- Color-coded score (green > 70%, yellow 40-70%, red < 40%)
- Corrections list with question number, wrong answer vs correct answer
- Share score button (optional)

### Leaderboard
- Medal icons for top 3 (gold, silver, bronze)
- Highlight current student's row with distinct color/border
- If student is not in top 20, show their rank below the list
- "Your rank: #42 out of 487" with their score

---

## 9. Assumptions & Out of Scope

### In This Version
- Single event at a time (no concurrent events)
- Questions are multiple-choice with exactly 4 options (A/B/C/D)
- One correct answer per question
- Equal weight per question (score = correct/total * 100)
- Per-question timer is purely client-side display
- Admin shares the link manually (no in-app notification system for events)

### Out of Scope (Not Implemented)
- Payment flow for paid events (`is_paid` flag exists but is not enforced)
- In-app notifications about upcoming events
- Student event history page
- CSV/PDF export of results
- Question images or rich media (questions are plain text only)
- Multi-select or open-ended questions
- Partial credit scoring

### Known Limitations
- If the server crashes mid-event, up to 30 seconds of submissions may be lost (submissions that were graded and score returned but not yet persisted to DB)
- If the app is killed and the event ends before the student re-opens, their submission is lost
- Leaderboard shows top 20 only — no pagination for full rankings
