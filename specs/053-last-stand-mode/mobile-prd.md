# PRD: Last Stand Mode — Mobile App Implementation

**Date**: 2026-03-22
**Audience**: Frontend mobile app AI agent
**Backend Status**: Fully implemented (FastAPI + Frappe + Redis)

---

## 1. Overview

Last Stand is a new **elimination-based live challenge mode** alongside the existing Exam mode. Players start with configurable hearts; wrong or missed answers cost one heart; zero hearts = eliminated (spectator). The server controls round-based question delivery with synchronized timing via WebSocket.

**Your job**: Implement the mobile UI/UX for this mode. The backend is complete — all APIs and WebSocket messages are defined below. You are consuming them, not building them.

---

## 2. What Changes for the Mobile App

### 2.1 Mode Detection

Every event now has a `mode` field: `"exam"` (default) or `"last_stand"`. The mode is available in:

- **Join response** → `mode` field
- **Status response** → `mode` field
- **Event detail** (if extended) → `mode` field on the event object

**Rule**: All existing Exam mode screens and flows remain 100% unchanged. Last Stand is additive — branch on `mode` to decide which UI to show.

### 2.2 Error Response Format

All error responses from the Live Challenge API use this structure:

```json
{
  "detail": {
    "code": "ERROR_CODE",
    "message": "Arabic user-facing message"
  }
}
```

- **`detail.code`**: Machine-readable error code (use this for branching logic)
- **`detail.message`**: Arabic string safe to display directly to the user

Parse `detail.code` for programmatic handling. Display `detail.message` to the user as-is.

### 2.3 Summary of Differences

| Aspect | Exam Mode (unchanged) | Last Stand Mode (new) |
|--------|----------------------|----------------------|
| Join during Active | Allowed | **Rejected** (NO_LATE_JOIN error) |
| Question delivery | All at once on exam_start | **One at a time** via WebSocket `round_start` |
| Answer submission | `POST /submit` with all answers | **`POST /answer` per round** |
| Timer | Client-side per question (optional) | **Server-controlled** per round |
| Hearts | N/A | Displayed, decremented on wrong/timeout |
| Elimination | N/A | Player becomes spectator at 0 hearts |
| Correct answer reveal | After submit | **After event ends** (never during play) |
| Leaderboard during event | N/A | N/A (both modes: only after Ended) |

> **CRITICAL**: `POST /submit` is **exam-only**. Calling it on a Last Stand event returns `MODE_NOT_SUPPORTED`. The frontend **MUST** check `mode` from the join response and route to `POST /answer` (per-round) for Last Stand events. There is no batch submission in Last Stand mode.

---

## 3. User Flows

### 3.1 Event Discovery & Join

**No changes to discovery.** The event card/detail should show a visual indicator that this is a "Last Stand" event (e.g., a badge, icon, or label).

**Join flow**:
1. Player taps "Join" on a Last Stand event in `Waiting` state.
2. `POST /{event_id}/join` → response includes:
   ```json
   {
     "joined": true,
     "event_id": "EVT-001",
     "position": 42,
     "waiting_room_duration": 120,
     "countdown_remaining": 85,
     "ws_url": "wss://...",
     "mode": "last_stand",
     "starting_hearts": 3
   }
   ```
3. On success, navigate to the **Waiting Room** screen.
4. **If event is Active**: Server returns HTTP 400 with `detail.code: "NO_LATE_JOIN"`. Display `detail.message` (Arabic) to the user and navigate back.

### 3.2 Waiting Room

Same as Exam mode with these additions:
- Display **starting hearts** (e.g., 3 heart icons)
- Display **mode badge**: "Last Stand"
- WebSocket countdown messages (`type: "countdown"`) work identically

When `exam_start` is received via WebSocket, transition to the **Last Stand Game Screen** (not the Exam screen).

### 3.3 Last Stand Game Screen (Core Gameplay)

This is the **new primary screen** for Last Stand mode. It replaces the Exam screen entirely for this mode.

> **Endpoint routing rule**: When `JoinResponse.mode == "last_stand"`, the frontend must use `POST /{event_id}/answer` (one answer per round). **Never** call `POST /{event_id}/submit` — it will be rejected with `MODE_NOT_SUPPORTED`.

#### 3.3.1 Layout

```
┌─────────────────────────────────┐
│  Round 3 / 10        ❤️ ❤️ 🖤   │  ← Header: round progress + hearts
│                                  │
│  ┌─────────────────────────────┐ │
│  │  Alive: 847 / 1000         │ │  ← Alive counter
│  └─────────────────────────────┘ │
│                                  │
│  ┌─────────────────────────────┐ │
│  │  ⏱ 12s                     │ │  ← Countdown timer (server-synced)
│  └─────────────────────────────┘ │
│                                  │
│  What is the capital of France?  │  ← Question text
│                                  │
│  ┌─────────────────────────────┐ │
│  │  A) London                  │ │  ← Answer options (tappable)
│  ├─────────────────────────────┤ │
│  │  B) Paris                   │ │
│  ├─────────────────────────────┤ │
│  │  C) Berlin                  │ │
│  ├─────────────────────────────┤ │
│  │  D) Madrid                  │ │
│  └─────────────────────────────┘ │
│                                  │
└─────────────────────────────────┘
```

#### 3.3.2 Hearts Display

- Show hearts as icons (filled = remaining, empty/dark = lost)
- Starting hearts come from `JoinResponse.starting_hearts`
- Updated after each `round_result` message via `hearts_remaining`
- **Heart loss animation**: When `heart_lost: true`, animate one heart breaking/fading

#### 3.3.3 Round Flow (driven entirely by WebSocket)

**Phase 1 — Receive `round_start`**:
```json
{
  "type": "round_start",
  "round_id": "EVT-001-R3",
  "question_idx": 2,
  "question": {
    "idx": 2,
    "question_text": "What is the capital of France?",
    "option_a": "London",
    "option_b": "Paris",
    "option_c": "Berlin",
    "option_d": "Madrid"
  },
  "time_limit": 15,
  "alive_count": 847,
  "total_rounds": 10,
  "is_alive": true
}
```

On receive:
- Display the question and options
- Start a **local countdown timer** = `time_limit` seconds
- Update alive counter
- Update round progress indicator: `question_idx + 1` / `total_rounds`
- If `is_alive == false`: show question as read-only (spectator view — options not tappable)
- **Store `round_id`** — needed for answer submission

**Phase 2 — Player taps an answer** (only if alive):
- Immediately call `POST /{event_id}/answer`:
  ```json
  {
    "round_id": "EVT-001-R3",
    "selected": "B"
  }
  ```
- On `200`: mark the selected option as "locked in" (visual feedback — e.g., highlight, disable other options)
- **Do NOT reveal correctness** — the server does not tell you if it's right or wrong yet
- On error (non-200), parse `detail.code` from the response body:
  | `detail.code` | HTTP Status | Meaning | UI Action |
  |--------------|-------------|---------|-----------|
  | `ROUND_MISMATCH` | 400 | Stale round | Ignore silently (next round_start will fix) |
  | `WINDOW_CLOSED` | 400 | Too late | Show brief toast: "Time's up!" |
  | `ALREADY_ANSWERED` | 400 | Duplicate | Ignore (already locked in) |
  | `NOT_ALIVE` | 400 | Eliminated | Switch to spectator view |
  | `NOT_A_PARTICIPANT` | 403 | Not joined | Should not happen if join succeeded |
  | `EVENT_NOT_ACTIVE` | 409 | Not active | Show toast with `detail.message` |
  | `MODE_NOT_SUPPORTED` | 400 | Bug — wrong endpoint | Should never happen if mode check is correct |

**Phase 3 — Receive `round_result`** (personalized per player):
```json
{
  "type": "round_result",
  "round_id": "EVT-001-R3",
  "question_idx": 2,
  "alive_count": 820,
  "eliminated_this_round": 27,
  "hearts_remaining": 2,
  "heart_lost": true,
  "is_correct": false,
  "is_eliminated": false,
  "is_alive": true
}
```

On receive — show the **Result Overlay** (displayed for `result_window_duration` seconds, typically 3s):
- **Correct answer**: Flash green on the player's selection (if `is_correct: true`)
- **Wrong answer**: Flash red on the player's selection, show generic "incorrect" indicator (if `is_correct: false`)
- **No answer**: Show "No answer" indicator (if `is_correct: null`)
- **Heart lost**: If `heart_lost: true`, animate one heart breaking. Update hearts display to `hearts_remaining`
- **Eliminated this round**: If `is_eliminated: true`, show dramatic **elimination overlay** (e.g., "ELIMINATED" with animation). Transition to spectator mode
- **Alive counter**: Update to `alive_count`
- **Eliminated count badge**: Show `eliminated_this_round` (e.g., "-27 players")
- **IMPORTANT**: Do NOT reveal the correct answer. `is_correct` tells the player if THEY were right, but you must NOT highlight which option was correct. Correct answers are only revealed after event ends (FR-020).

**Phase 4 — Wait for next `round_start`**:
- After the result window, the server sends the next `round_start`
- Clear the result overlay, display the new question
- Repeat from Phase 1

#### 3.3.4 Timer Behavior

- Timer is **server-authoritative**. `time_limit` from `round_start` is the duration in seconds.
- Start a local countdown from `time_limit` when `round_start` is received.
- When timer reaches 0: disable answer options (even if `round_result` hasn't arrived yet — it will come shortly).
- **Do NOT submit anything on timeout** — the server handles timeouts automatically (counts as wrong).

#### 3.3.5 Spectator Mode (Eliminated Player)

When a player is eliminated (`is_eliminated: true` in `round_result`):
- Show elimination animation/overlay
- Transition the game screen to **spectator mode**:
  - Questions still appear (from `round_start`)
  - Answer options are **disabled/grayed out** (not tappable)
  - Hearts display shows 0 (all empty/dark)
  - Show a persistent "Spectating" badge
  - `round_result` still arrives with shared stats (alive_count, eliminated_this_round)
  - Personalized fields show: `is_alive: false`, `is_correct: null`, `heart_lost: false`
- Player remains connected until event ends

### 3.4 Reconnection

If the WebSocket disconnects during an Active Last Stand event:
1. Reconnect to `ws_url`
2. Server sends a `player_state` message:
   ```json
   {
     "type": "player_state",
     "hearts_remaining": 2,
     "is_alive": true,
     "current_round_id": "EVT-001-R5",
     "question_idx": 4,
     "phase": "answer",
     "phase_remaining_ms": 8500,
     "question": {
       "idx": 4,
       "question_text": "...",
       "option_a": "...",
       "option_b": "...",
       "option_c": "...",
       "option_d": "..."
     },
     "alive_count": 650,
     "eliminated_at_question": null
   }
   ```
3. Rebuild the game screen from this state:
   - Set hearts to `hearts_remaining`
   - If `is_alive: false`: enter spectator mode, show `eliminated_at_question`
   - If `phase == "answer"` and `is_alive`: show question, start timer from `phase_remaining_ms / 1000`
   - If `phase == "result"`: show "waiting for next round" state
   - If `question` is null: waiting between rounds or in result phase — just show current stats

**Missed rounds**: If the player was disconnected for multiple rounds, the server already deducted hearts for each missed round. The `player_state` reflects the current state — no client-side catch-up needed.

### 3.5 Event End

Server broadcasts `event_ended`:
```json
{
  "type": "event_ended",
  "reason": "all_finished",
  "final_alive_count": 12,
  "total_rounds_played": 10
}
```

**End reasons**:
| Reason | Meaning | Suggested UI Message |
|--------|---------|---------------------|
| `all_finished` | All questions completed | "Challenge Complete!" |
| `all_eliminated` | Everyone eliminated | "Everyone Eliminated!" |
| `time_ceiling` | Safety timeout hit | "Time's Up!" |

On event end:
1. Show **end screen** with the reason
2. Navigate to the **Results Screen** (can be immediate or after a short delay)

### 3.6 Results Screen

Call `GET /{event_id}/result`:
```json
{
  "event_id": "EVT-001",
  "event_name": "Weekly Challenge #5",
  "score": 70.0,
  "correct_count": 7,
  "total_questions": 10,
  "rank": 15,
  "total_participants": 1000,
  "xp_awarded": 50,
  "submitted_at": null,
  "corrections": [...],
  "final_hearts": 2,
  "is_eliminated": false,
  "eliminated_at_question": 0,
  "avg_response_time_ms": 4200
}
```

Display:
- **Score**: `70%` (correct_count / total_questions)
- **Rank**: `#15 / 1000`
- **Hearts remaining**: 2 heart icons (or "Eliminated at Q5" if `is_eliminated`)
- **Avg response time**: `4.2s`
- **XP awarded**: `+50 XP`
- **Corrections**: If provided, show which questions were wrong (this is after event ends, so correct answers CAN be shown here)

### 3.7 Leaderboard Screen

Call `GET /{event_id}/leaderboard`:
```json
{
  "event_id": "EVT-001",
  "event_name": "Weekly Challenge #5",
  "status": "ended",
  "leaderboard": [
    {
      "rank": 1,
      "player": "PLR-001",
      "display_name": "Alice",
      "score": 100.0,
      "final_hearts": 3,
      "is_eliminated": false
    },
    {
      "rank": 2,
      "player": "PLR-002",
      "display_name": "Bob",
      "score": 90.0,
      "final_hearts": 1,
      "is_eliminated": false
    }
  ],
  "my_rank": 15,
  "my_score": 70.0,
  "total_participants": 1000,
  "exam_end_ts": "2026-03-22T15:00:00Z"
}
```

Display:
- Top 20 entries with rank, name, score
- **Last Stand additions**: Show hearts remaining and eliminated badge per entry
- Highlight the current player's rank (even if not in top 20 via `my_rank`)
- Ranking logic (server-computed): score DESC → hearts DESC → avg response time ASC

---

## 4. API Reference

Base path: `/api/v1/live-challenge`

### 4.1 New Endpoint

#### `POST /{event_id}/answer` — Round Answer Submission (Last Stand Only)

**Auth**: Bearer token required.

**Request**:
```json
{
  "round_id": "EVT-001-R3",
  "selected": "B"
}
```

**Success Response (200)**:
```json
{
  "accepted": true,
  "round_id": "EVT-001-R3"
}
```

**Error Responses** (all use `detail: {code, message}` format):
| HTTP Status | `detail.code` | Meaning |
|-------------|---------------|---------|
| 400 | `ROUND_MISMATCH` | round_id doesn't match current round |
| 400 | `WINDOW_CLOSED` | Answer window has ended |
| 400 | `ALREADY_ANSWERED` | Player already answered this round |
| 400 | `NOT_ALIVE` | Player is eliminated |
| 400 | `MODE_NOT_SUPPORTED` | Event is exam mode (should never happen if mode check is correct) |
| 403 | `NOT_A_PARTICIPANT` | Player hasn't joined |
| 409 | `EVENT_NOT_ACTIVE` | Event is not in Active state |

### 4.2 Modified Endpoints

#### `POST /{event_id}/join`
- New fields in response: `mode`, `starting_hearts` (null for exam)
- New error for Last Stand: `NO_LATE_JOIN` (400) when event is Active

#### `POST /{event_id}/submit` — **EXAM MODE ONLY**
> **WARNING**: This endpoint is exclusively for Exam mode. Calling it on a Last Stand event returns HTTP 400 with `detail.code: "MODE_NOT_SUPPORTED"`. The frontend **MUST** check `mode` from the join response and **NEVER** call `/submit` for Last Stand events. Use `POST /{event_id}/answer` (per-round) instead.

#### `GET /{event_id}/status`
- New fields: `mode`, `alive_count`, `eliminated_count`, `current_round`, `total_rounds`
- Last Stand-specific fields are `null` for exam mode

#### `GET /{event_id}/result`
- New fields: `final_hearts`, `is_eliminated`, `eliminated_at_question`, `avg_response_time_ms`
- These fields are `0`/`false` for exam mode

#### `GET /{event_id}/leaderboard`
- Entries include: `final_hearts`, `is_eliminated`
- Ranking uses 3-tier sort for Last Stand (transparent to client — server-computed)

---

## 5. WebSocket Message Reference

All messages arrive as JSON via the existing WebSocket connection at `ws_url`.

### 5.1 Existing Messages (unchanged)

| `type` | When | Notes |
|--------|------|-------|
| `countdown` | Waiting room | Periodic countdown updates |
| `exam_start` | Waiting → Active | For **exam mode only** — delivers all questions |

### 5.2 New Messages (Last Stand only)

#### `round_start`
**When**: Start of each round's answer window
**Personalized**: `is_alive` field differs per player

```typescript
{
  type: "round_start"
  round_id: string        // e.g. "EVT-001-R3"
  question_idx: number    // 0-based
  question: {
    idx: number
    question_text: string
    option_a: string
    option_b: string
    option_c: string
    option_d: string
  }
  time_limit: number      // seconds for answer window
  alive_count: number
  total_rounds: number
  is_alive: boolean       // (personalized) true if this player can answer
}
```

#### `round_result`
**When**: After answer window closes and server evaluates
**Personalized**: `hearts_remaining`, `heart_lost`, `is_correct`, `is_eliminated`, `is_alive` differ per player

```typescript
{
  type: "round_result"
  round_id: string
  question_idx: number
  alive_count: number            // after this round's eliminations
  eliminated_this_round: number
  // Personalized:
  hearts_remaining: number
  heart_lost: boolean
  is_correct: boolean | null     // null = didn't answer
  is_eliminated: boolean         // true = eliminated THIS round
  is_alive: boolean
}
```

#### `player_state`
**When**: Sent to a single player on WebSocket reconnect during Active
**Purpose**: Rebuild client UI state after disconnect

```typescript
{
  type: "player_state"
  hearts_remaining: number
  is_alive: boolean
  current_round_id: string | null  // null if between rounds
  question_idx: number
  phase: "answer" | "result"
  phase_remaining_ms: number       // ms left in current phase
  question: Question | null        // null if eliminated or in result phase
  alive_count: number
  eliminated_at_question: number | null  // null if alive
}
```

#### `alive_count_update`
**When**: After each round (lightweight update)
**Use**: Update alive/eliminated counters

```typescript
{
  type: "alive_count_update"
  alive_count: number
  eliminated_count: number
  current_round: number  // 0-based question index
}
```

#### `event_ended` (modified)
**When**: Event ends
**New fields for Last Stand**: `reason`, `final_alive_count`, `total_rounds_played`

```typescript
{
  type: "event_ended"
  reason: "all_finished" | "all_eliminated" | "time_ceiling" | null  // null for exam
  final_alive_count: number | null   // null for exam
  total_rounds_played: number | null // null for exam
}
```

---

## 6. State Management

### 6.1 Client State Model

```typescript
interface LastStandState {
  // From join
  mode: "last_stand"
  startingHearts: number
  eventId: string

  // Runtime (updated via WebSocket)
  currentRoundId: string | null
  questionIdx: number
  totalRounds: number
  timeLimit: number
  timerRemaining: number     // local countdown
  isAlive: boolean
  heartsRemaining: number
  aliveCount: number

  // Per-round
  currentQuestion: Question | null
  selectedAnswer: "A" | "B" | "C" | "D" | null
  answerSubmitted: boolean   // locked in via API
  phase: "waiting" | "answer" | "result" | "ended"

  // Result overlay (from round_result)
  lastResult: {
    isCorrect: boolean | null
    heartLost: boolean
    isEliminated: boolean
    eliminatedThisRound: number
  } | null
}
```

### 6.2 Mode-Based Routing

After a successful join, the frontend **MUST** branch on `JoinResponse.mode`:

```
if mode == "exam":
    → Use existing Exam flow
    → Submit all answers via POST /{event_id}/submit
    → Listen for exam_start WebSocket message

if mode == "last_stand":
    → Use Last Stand Game Screen
    → Submit each answer via POST /{event_id}/answer  (per round)
    → Listen for round_start / round_result WebSocket messages
    → NEVER call POST /{event_id}/submit
```

### 6.3 State Transitions

```
WAITING  →(exam_start or first round_start)→  ANSWER
ANSWER   →(round_result received)→             RESULT
RESULT   →(next round_start received)→         ANSWER
ANSWER   →(event_ended received)→              ENDED
RESULT   →(event_ended received)→              ENDED
```

### 6.4 Timer Sync Strategy

- Start local timer from `time_limit` when `round_start` arrives
- Decrement locally every 100ms for smooth UI
- When timer hits 0: disable answer options (visual only — server handles timeout)
- On reconnect: use `phase_remaining_ms` from `player_state` to sync timer

---

## 7. Screen Inventory

| Screen | Mode | New/Modified |
|--------|------|-------------|
| Event Card / List | Both | **Modified** — show mode badge |
| Event Detail | Both | **Modified** — show starting hearts, mode |
| Waiting Room | Both | **Modified** — show hearts for Last Stand |
| Exam Game Screen | Exam only | **Unchanged** |
| Last Stand Game Screen | Last Stand only | **New** |
| Spectator View | Last Stand only | **New** (or overlay on game screen) |
| Result Overlay (per-round) | Last Stand only | **New** |
| Elimination Overlay | Last Stand only | **New** |
| Event End Screen | Last Stand only | **New** |
| Results Screen | Both | **Modified** — show hearts, elimination, avg time |
| Leaderboard Screen | Both | **Modified** — show hearts and eliminated badge |

---

## 8. Animation & Visual Effects (Suggestions)

These are suggestions — adapt to your design system:

| Trigger | Effect |
|---------|--------|
| Heart lost | Heart icon cracks/shatters, fades from filled to empty |
| Elimination | Screen shake + "ELIMINATED" text + dramatic overlay (2s) |
| Correct answer | Selected option flashes green briefly |
| Wrong answer | Selected option flashes red briefly |
| No answer (timeout) | Timer pulses red at 3s, options fade out at 0s |
| Round transition | Brief fade/slide transition between questions |
| Early window close | Timer jumps to 0, brief "All answered!" flash |
| Event end | Full-screen overlay with end reason and stats summary |

---

## 9. Error Handling

All API errors return `{ "detail": { "code": "...", "message": "..." } }`. Use `detail.code` for logic, display `detail.message` to the user.

| Scenario | `detail.code` | Handling |
|----------|---------------|---------|
| WebSocket drops | N/A | Auto-reconnect with exponential backoff. On reconnect, `player_state` restores UI. |
| Answer API fails (network) | N/A | Show brief error toast. Player can retry if timer hasn't expired. |
| Answer API returns 400 | `WINDOW_CLOSED` | Show `detail.message` as toast. Wait for `round_result`. |
| Answer API returns 400 | `ALREADY_ANSWERED` | Ignore — answer is already locked in. |
| Answer API returns 400 | `ROUND_MISMATCH` | Ignore silently — next `round_start` will sync state. |
| Answer API returns 400 | `NOT_ALIVE` | Switch to spectator view. |
| Join API returns 400 | `NO_LATE_JOIN` | Show `detail.message` in modal. Navigate back. |
| Submit API returns 400 | `MODE_NOT_SUPPORTED` | **Bug**: frontend called `/submit` on a Last Stand event. Must use `/answer` per round instead. |
| `round_result` arrives before timer hits 0 | N/A | Stop timer, show result immediately. |
| `round_start` arrives while still showing result | N/A | Clear result overlay, show new question. |
| No `round_start` after result (>15s) | N/A | Show "Waiting for next round..." spinner. |

---

## 10. Testing Checklist

### Happy Path
- [ ] Join a Last Stand event during Waiting → see hearts assigned
- [ ] Receive `round_start` → question displayed with timer
- [ ] Submit correct answer → locked in, no heart lost on `round_result`
- [ ] Submit wrong answer → heart lost animation on `round_result`
- [ ] Timeout (no answer) → heart lost on `round_result`
- [ ] Hearts reach 0 → elimination overlay → spectator mode
- [ ] Spectator sees questions but cannot answer
- [ ] All rounds complete → `event_ended` → results screen
- [ ] Results show score, rank, hearts, avg response time
- [ ] Leaderboard shows hearts and elimination status

### Edge Cases
- [ ] Disconnect mid-round → reconnect → `player_state` restores game
- [ ] Disconnect for multiple rounds → reconnect → hearts already deducted
- [ ] All players eliminated → `event_ended` with `reason: "all_eliminated"`
- [ ] Try to join Active Last Stand → `NO_LATE_JOIN` error handled
- [ ] Answer with stale `round_id` → `ROUND_MISMATCH` handled
- [ ] Double-tap answer → `ALREADY_ANSWERED` handled gracefully
- [ ] Event with 1 heart (instant elimination on first wrong answer)
- [ ] Event with 10 hearts (long survival)
- [ ] Exam mode events → zero regressions, no Last Stand UI elements shown

### Performance
- [ ] 10+ rounds with smooth timer animations (no jank)
- [ ] WebSocket message handling doesn't block UI thread
- [ ] Heart/elimination animations don't cause frame drops

---

## 11. Out of Scope

These are explicitly NOT part of this feature:
- Live leaderboard during the event (leaderboard is post-event only)
- Spectator join for non-participants (only eliminated players spectate)
- New question types (Last Stand uses existing MCQ only)
- Chat or reactions during gameplay
- Admin controls in the mobile app (admin uses web dashboard)
- Replay or round history during the event
