# Product Requirements Document: Challenge Hub (مركز التحدي)

**Feature Name**: `challenge-hub`
**Audience**: Backend AI agent + Mobile team AI agent
**Primary Sources**: Brainstorming session (challenge-hub-hierarchy.md)
**Status**: Ready for spec writing
**Date**: 2026-03-08

---

## 1. Purpose

Build a sequential, game-like challenge mode where students prove mastery of each topic one by one. Students answer all available MCQ questions (from Review Item) for a single topic, must score ≥50% to stamp it complete, and progress linearly through units. The feature has its own separate XP system and leaderboard, completely isolated from the main game loop.

---

## 2. Product Summary

Challenge Hub is a separate game mode that lets students:
- choose a subject from their plan's subjects,
- navigate the hierarchy (track → unit → topic),
- play a topic challenge by answering all available MCQ questions in random order,
- stamp the topic on ≥50% success,
- earn Challenge XP (separate from main XP) on every attempt,
- compete on a Challenge-specific leaderboard scoped to their plan.

The experience must feel like a sequential game progression — topics unlock one after another, and students are motivated to replay for higher scores and more XP.

---

## 3. Scope

### In Scope

- Hierarchy browsing: subject → track → unit → topic
- Topic unlock logic (access + normal path completion + sequential challenge completion)
- Empty topic handling (auto-stamp inheritance)
- Topic question loading from CDN (JSON cache files)
- Challenge attempt submission and storage
- FSRS integration (every answer sent to the algorithm)
- Challenge XP system (separate from main XP)
- Challenge leaderboard (plan-scoped, subject-level and plan-level)
- Season reset (with archival note)

### Out of Scope

- Admin/teacher dashboards for challenge analytics — separate session
- Archival mechanism details — future work, noted for reference
- Batch/queue write optimization details — system-wide architecture decision, not challenge-specific
- Offline mode
- Non-MCQ question types (challenge uses MCQ only from Review Item)
- Purchase/paywall flow beyond showing locked state and redirecting to normal path

---

## 4. Hierarchy

```
Challenge Hub
  │
  ├── Subject (from student's plan subjects)
  │    │
  │    ├── Track 1
  │    │    │
  │    │    ├── Unit 1
  │    │    │    ├── Topic 1  ← lowest level (no lessons)
  │    │    │    ├── Topic 2
  │    │    │    └── Topic 3
  │    │    │
  │    │    └── Unit 2
  │    │         ├── Topic 4
  │    │         └── Topic 5
  │    │
  │    └── Track 2
  │         └── ...
  │
  └── Another Subject
       └── ...
```

Mirrors the normal path hierarchy (Subject → Track → Unit → Topic) without the Lesson level. Same source tables as the existing hierarchy system.

---

## 5. User Stories

### User Story 1 — Browse Challenge Hierarchy (Priority: P1)

A student opens Challenge Hub, selects a subject from their plan, and navigates tracks → units → topics. Each topic shows one of three states: locked, open, or stamped.

**Why P1**: Entry point to the entire feature. Without browsing, nothing else works.

**Acceptance Scenarios**:

1. **Given** a student opens Challenge Hub, **When** the hub loads, **Then** they see only subjects from their plan.
2. **Given** a student selects a subject, **When** tracks load, **Then** all tracks for that subject are shown, with lock icons on tracks the student has no access to.
3. **Given** a student taps a locked track, **When** the system responds, **Then** it redirects them to the normal path where they discover they need to purchase.
4. **Given** a student selects an accessible track, **When** units and topics load, **Then** the first topic of the first unit is open if the student has completed it in the normal path. Subsequent topics show as locked until the previous one is stamped in Challenge Hub.
5. **Given** a topic has zero MCQ questions in Review Item, **When** the hierarchy renders, **Then** that topic is hidden from the student but inherits the stamp state of the topic before it (for unlock chain purposes).
6. **Given** Topic 1 is locked, Topic 2 is empty (hidden), Topic 3 exists, **When** Topic 1 is locked, **Then** Topic 2 remains locked (inherited), **And** Topic 3 remains locked.
7. **Given** Topic 1 becomes stamped, Topic 2 is empty (hidden), Topic 3 exists, **When** the hierarchy re-evaluates, **Then** Topic 2 auto-stamps (inherited), **And** Topic 3 becomes open.

---

### User Story 2 — Play a Topic Challenge (Priority: P1)

A student selects an open topic, answers all MCQ questions in random order, and receives a pass/fail result.

**Why P1**: Core gameplay loop.

**Acceptance Scenarios**:

1. **Given** a student selects an open topic, **When** the challenge loads, **Then** all MCQ questions for that topic are loaded from the CDN JSON file in random order.
2. **Given** the student is answering questions, **When** they answer incorrectly, **Then** the correct answer is shown immediately. They cannot go back to previous questions.
3. **Given** the student completes all questions, **When** the frontend calculates the result, **Then** ≥50% correct = pass (topic stamped), <50% = fail (can retry).
4. **Given** the student passes, **When** the result is submitted, **Then** the topic is stamped, the next topic in the unit unlocks, and the attempt is saved.
5. **Given** the student fails, **When** the result is submitted, **Then** the topic remains open (not stamped), and the attempt is saved. The student can retry immediately.
6. **Given** the student exits mid-challenge (closes app, navigates away), **When** the session is abandoned, **Then** nothing is saved — no attempt record, no FSRS update. As if it never happened.
7. **Given** a topic has 100 questions, **When** the student starts a challenge, **Then** all 100 questions must be answered in one sitting for the attempt to count.
8. **Given** the student retries a topic, **When** questions load, **Then** they appear in a different random order than the previous attempt.

---

### User Story 3 — Retry and Improve (Priority: P1)

A student replays a stamped or failed topic to improve their score and earn more Challenge XP.

**Why P1**: Replay is the core engagement loop and the primary driver of Challenge XP accumulation.

**Acceptance Scenarios**:

1. **Given** a student has stamped a topic with 70%, **When** they replay and score 90%, **Then** the displayed best score updates to 90%, and they earn Challenge XP for the 20% improvement.
2. **Given** a student has stamped a topic with 70%, **When** they replay and score 50%, **Then** the displayed best score remains 70%, and they earn zero additional Challenge XP.
3. **Given** a student failed a topic with 40%, **When** they replay and score 30%, **Then** the best score remains 40%, and they earn zero additional Challenge XP.
4. **Given** a student failed a topic with 40%, **When** they replay and score 70%, **Then** the displayed best score updates to 70% (best overall), and they earn Challenge XP for the 30% improvement. The topic is now stamped.
5. **Given** a student plays any attempt (pass or fail), **When** the result is submitted, **Then** every individual question result is sent to FSRS regardless of pass/fail outcome.
6. **Given** unlimited retries, **When** a student replays the same topic 10 times, **Then** all 10 attempts are stored in the analytics table with full details.

---

### User Story 4 — Challenge XP (Priority: P1)

A student earns Challenge XP for correct answers, motivating replays to maximize points.

**Why P1**: XP drives the leaderboard, which drives engagement.

**Acceptance Scenarios**:

1. **Given** a fixed XP value per correct answer (e.g., 5 XP), **When** a topic has 20 questions and the student scores 40% (8 correct), **Then** they earn 8 × 5 = 40 Challenge XP.
2. **Given** the student replays the same topic and scores 70% (14 correct), **When** the result is submitted, **Then** they earn only the delta: (14 - 8) × 5 = 30 additional Challenge XP. Total: 70 Challenge XP.
3. **Given** the student replays and scores 50% (10 correct), **When** the result is submitted, **Then** they earn zero additional Challenge XP because 50% < their previous best of 70%.
4. **Given** the student replays and scores 90% (18 correct), **When** the result is submitted, **Then** they earn (18 - 14) × 5 = 20 additional Challenge XP. Total: 90 Challenge XP.
5. **Given** Challenge XP, **When** the main profile XP is queried, **Then** Challenge XP is completely absent — it exists only within Challenge Hub.
6. **Given** Challenge XP, **When** the main leaderboard is queried, **Then** Challenge XP does not appear — it feeds only the Challenge leaderboard.

---

### User Story 5 — Challenge Leaderboard (Priority: P2)

A student views their Challenge XP ranking among peers in the same plan.

**Why P2**: Enhances motivation but the core gameplay loop works without it.

**Acceptance Scenarios**:

1. **Given** a student opens the Challenge leaderboard, **When** it loads, **Then** they see the top 20 students from their plan ranked by Challenge XP, plus their own rank even if not in the top 20.
2. **Given** the student filters by subject, **When** the leaderboard loads, **Then** only Challenge XP earned in that subject is counted for ranking.
3. **Given** the student views the plan-level leaderboard (all subjects), **When** it loads, **Then** Challenge XP from all subjects is summed for ranking.
4. **Given** the leaderboard updates periodically (not real-time), **When** a student earns XP, **Then** their updated rank appears after the next refresh cycle. The refresh interval is determined by the backend implementation.
5. **Given** a student has no plan assigned, **When** they open the Challenge leaderboard, **Then** they see an empty leaderboard with a clear indicator.
6. **Given** only 3 students in a plan have Challenge XP, **When** the leaderboard loads, **Then** only those 3 appear (no padding).

---

### User Story 6 — Season Reset (Priority: P3)

When a season ends, all Challenge Hub progress is archived and reset.

**Why P3**: Seasonal behavior, not needed for initial launch within a season.

**Acceptance Scenarios**:

1. **Given** a season ends, **When** the reset process runs, **Then** all challenge progress (stamped topics, best scores, Challenge XP, leaderboard) is cleared.
2. **Given** a season ends, **When** the reset process runs, **Then** all challenge attempt data is archived before clearing. (Archival mechanism details are deferred — not in scope for this PRD.)
3. **Given** the new season starts, **When** a student opens Challenge Hub, **Then** all topics are unstamped, Challenge XP is zero, and the leaderboard is empty.

**Important note**: The reset happens at the END of the season (upon season expiry), NOT at the start of a new season.

---

## 6. Topic Unlock Logic

Three conditions must ALL be true for a topic to be open:

| # | Condition | How to Check |
|---|-----------|-------------|
| 1 | Student has access (subscription, purchase, or free content) | Same access system as normal path — `memora:access:{player_id}` keys, `is_free` flags, plan-level free subjects |
| 2 | Student completed all lessons in this topic on the normal path | Bitmap + stats cache (existing system) — topic total == topic completed |
| 3 | Previous topic in Challenge Hub is stamped (or this is the first topic in the unit) | Challenge progress storage |

If condition 1 fails → message: "Go complete from the normal path" (redirects to normal path where they see the purchase requirement).
If condition 2 fails → message: "Go complete the lesson first."
If condition 3 fails → message: "Complete the previous topic first."

---

## 7. Empty Topic Handling

A topic with zero MCQ questions in Review Item:
- Is hidden from the student (not rendered in the hierarchy).
- Auto-stamps ONLY if the topic before it is stamped (inherits predecessor state).
- This prevents chain breakage: if Topic 2 is empty and hidden, Topic 3 still unlocks correctly when Topic 1 is stamped.

---

## 8. Question Source and Delivery

### Source

All questions come from the `Memora Review Item` table, filtered by topic, MCQ type only (which is the only type in Review Item).

### CDN Cache (JSON Files)

To avoid hitting the database on every challenge start:
- One JSON file per topic is pre-built and stored on CDN.
- The file contains all MCQ questions for the topic plus topic metadata.
- The frontend reads directly from CDN — zero database load for question delivery.
- The JSON file is rebuilt when a teacher adds, edits, or deletes questions (same rebuild mechanism as the existing hierarchy cache).
- The backend agent should inspect the current hierarchy build process and determine what additional metadata fields to include in the topic JSON file.

### Question Randomization

The frontend shuffles questions into random order on each attempt. Different order every time, even on retries.

---

## 9. Attempt Submission Flow

```
Frontend completes challenge
  │
  ├── Calculates: score %, pass/fail, Challenge XP delta
  │
  └── Sends ONE request to backend containing:
       ├── Topic ID
       ├── Total questions
       ├── Correct count
       ├── Score percentage
       ├── Pass / Fail
       ├── Total time spent
       ├── Per-question details:
       │    ├── item_id
       │    ├── correct (bool)
       │    ├── time spent on this question
       │    └── chosen answer
       │
       └── Challenge XP earned (delta)

Backend receives and handles:
  ├── Saves attempt to Challenge Attempt table (analytics)
  ├── Updates best score if improved
  ├── Updates Challenge XP total if delta > 0
  ├── Sends each question result to FSRS (same method as normal path — details left to backend agent)
  └── Returns confirmation to frontend
```

**Incomplete attempts**: If the student exits mid-challenge, the frontend sends nothing. No record, no FSRS update, nothing saved.

**Write optimization note**: Database writes may go through background jobs or a queue system at the system level. This is a system-wide architecture decision, not specific to Challenge Hub. The backend agent should follow existing patterns or propose improvements.

---

## 10. Data Model

### Challenge Attempt (Analytics — one row per attempt)

| Field | Type | Description |
|-------|------|-------------|
| student | Link | Player reference |
| topic | Link | Topic reference |
| attempt_number | Int | Sequential: 1, 2, 3, ... |
| total_questions | Int | Total questions in this attempt |
| correct_count | Int | Number of correct answers |
| score_pct | Decimal | Percentage score |
| passed | Bool | True if score ≥ 50% |
| time_spent | Int | Total seconds for the attempt |
| xp_earned | Int | Challenge XP earned in this attempt (delta) |
| timestamp | Datetime | When the attempt was submitted |

### Challenge Attempt Detail (Child table — one row per question per attempt)

| Field | Type | Description |
|-------|------|-------------|
| item_id | Data | Review Item UUID |
| correct | Bool | Answered correctly |
| time_spent | Int | Seconds spent on this question |
| chosen_answer | Int | The answer option chosen (1-based index) |

### Challenge Progress (one row per student per topic)

| Field | Type | Description |
|-------|------|-------------|
| student | Link | Player reference |
| topic | Link | Topic reference |
| stamped | Bool | Whether the topic is stamped |
| best_score_pct | Decimal | Highest score across all attempts (pass or fail — used for XP calculation) |
| best_passing_pct | Decimal | Highest passing score (≥50% — used for display) |
| total_xp_earned | Int | Cumulative Challenge XP earned for this topic |
| attempt_count | Int | Total number of completed attempts |

### Challenge XP Leaderboard

Separate from the main leaderboard system. Plan-scoped.

Two levels:
- **Per-subject**: Challenge XP earned by a student in a specific subject, within their plan.
- **Per-plan (all subjects)**: Sum of Challenge XP across all subjects, within their plan.

Top 20 + student's own rank. Periodic refresh (interval determined by backend). Resets at end of season.

---

## 11. XP Calculation Rules

Fixed XP per correct answer (e.g., 5 XP — configurable in settings).

**Tracking basis**: best score overall (not best passing score). Even failed attempts contribute to the XP high-water mark.

**Calculation on each attempt**:
1. Count correct answers in this attempt → `current_correct`
2. Retrieve `best_correct` from Challenge Progress (derived from `best_score_pct × total_questions`)
3. If `current_correct > best_correct` → XP earned = `(current_correct - best_correct) × xp_per_question`
4. If `current_correct ≤ best_correct` → XP earned = 0
5. Update `best_score_pct` if `current_correct / total_questions > best_score_pct`

**Example walkthrough** (topic with 20 questions, 5 XP per correct):

| Attempt | Score | Correct | Best So Far | XP This Attempt | Total XP |
|---------|-------|---------|-------------|-----------------|----------|
| 1 | 40% (fail) | 8 | 8 | 40 | 40 |
| 2 | 30% (fail) | 6 | 8 | 0 | 40 |
| 3 | 70% (pass) | 14 | 14 | 30 | 70 |
| 4 | 50% (pass) | 10 | 14 | 0 | 70 |
| 5 | 90% (pass) | 18 | 18 | 20 | 90 |

---

## 12. FSRS Integration

Every completed attempt sends all question results to FSRS, regardless of pass/fail:
- Correct answer → sent as "Good" rating
- Incorrect answer → sent as "Again" rating

The backend agent should use the same FSRS integration method as the normal path. This ensures the spaced repetition algorithm sees the full picture of the student's knowledge.

**Important**: Incomplete (abandoned) attempts do NOT send anything to FSRS.

---

## 13. Isolation Guarantees

Challenge Hub is completely isolated from the main game loop:

| System | Affected? |
|--------|-----------|
| Main XP | ✗ No |
| Main Leaderboard | ✗ No |
| Streak | ✗ No |
| Profile Stats | ✗ No |
| Wallet | ✗ No |
| FSRS Algorithm | ✓ Yes — every answer is sent |
| Challenge XP | ✓ Yes — separate accumulator |
| Challenge Leaderboard | ✓ Yes — separate board |

---

## 14. Season Behavior

- All Challenge Hub data resets at the **end of the season** (not at the start of a new one).
- Before reset: all student data is archived (stamped topics, scores, XP, attempts).
- After reset: all topics unstamped, XP zeroed, leaderboard cleared.
- **Archival mechanism details are deferred** — not in scope for this PRD. Noted here so it is not forgotten.

---

## 15. Edge Cases

| # | Scenario | Behavior |
|---|----------|----------|
| 1 | Topic has 0 MCQ questions | Hidden from hierarchy, auto-stamps when predecessor is stamped |
| 2 | Student exits mid-challenge | Nothing saved, no FSRS update, attempt discarded |
| 3 | Student has no plan | Show empty leaderboard with clear indicator |
| 4 | Plan has 1 student | Student sees themselves as #1, no neighbors |
| 5 | Student changes plan mid-season | Challenge XP earned under old plan stays there. New XP goes to new plan. |
| 6 | Teacher adds questions to a topic after student stamped it | Student can replay with the new questions. JSON file rebuilt on CDN. Stamp and best score unchanged. |
| 7 | Teacher deletes all questions from a topic | Topic becomes empty — hidden, auto-stamps based on predecessor |
| 8 | Topic has 1 question | Student answers 1 question. ≥50% = pass (i.e., must answer correctly). |
| 9 | First topic in unit has no access | Entire unit is effectively locked. Message redirects to normal path. |
| 10 | Multiple consecutive empty topics | Each inherits from predecessor. Chain: stamped → auto-stamp → auto-stamp → next real topic opens. |
| 11 | All topics in a unit are empty | Unit appears empty. All auto-stamp when predecessor (last topic of previous unit) is stamped. |
| 12 | Student submits attempt but network fails | Frontend should retry. Backend should handle idempotency (implementation detail for backend agent). |

---

## 16. Configurable Settings

| Setting | Description | Default | Stored In |
|---------|-------------|---------|-----------|
| `challenge_xp_per_question` | XP awarded per correct answer | 5 | Memora Settings |
| `challenge_pass_threshold` | Minimum score % to stamp a topic | 50 | Memora Settings |
| `challenge_lb_refresh_interval` | Leaderboard refresh frequency | TBD by backend | Memora Settings |
| `challenge_lb_top_count` | Number of top students shown | 20 | Memora Settings |

---

## 17. Dependencies

| Dependency | Description |
|------------|-------------|
| Review Item table | Source of all MCQ questions. Must be populated and synced. |
| Existing hierarchy system | Challenge Hub reads the same Subject → Track → Unit → Topic structure. |
| Existing access system | Same `memora:access:{player_id}` keys, `is_free` flags, plan-level free subjects. |
| Existing progress system | Bitmap + stats cache used to verify topic completion on normal path. |
| Existing FSRS integration | Challenge reuses the same method for sending question results. |
| CDN infrastructure | For hosting topic JSON cache files. Same infrastructure as hierarchy cache. |
| Plan system | Leaderboard is scoped by plan. Students must have a plan for leaderboard to work. |

---

## 18. Decisions Log

| # | Decision | Rationale |
|---|----------|-----------|
| D-001 | Hierarchy: Subject → Track → Unit → Topic (no Lesson) | Challenge tests topic-level mastery, not individual lessons. |
| D-002 | Same access system as normal path | No new access logic needed. Consistency. |
| D-003 | Three unlock conditions (access + normal completion + challenge sequence) | Ensures students learn before challenging, and progress sequentially. |
| D-004 | Empty topics: hidden + auto-stamp inherited from predecessor | Prevents chain breakage without confusing the student. |
| D-005 | All MCQ questions per topic, no limit | Challenge = comprehensive topic review. |
| D-006 | Random order every attempt | Prevents memorizing answer positions. |
| D-007 | No going back to previous questions | Correct answer shown after wrong answer — going back would break this. |
| D-008 | 50% pass threshold | Balance between challenge and accessibility. |
| D-009 | Incomplete attempt = fully discarded | Simplicity. No partial saves, no partial FSRS updates. |
| D-010 | Unlimited retries, even after stamp | Drives engagement and XP accumulation. |
| D-011 | Best passing score for display, best overall score for XP | Two separate high-water marks with different purposes. |
| D-012 | Every answer sent to FSRS | Algorithm needs full picture for accurate scheduling. |
| D-013 | Frontend calculates pass/fail | Frontend already has correct answers (shows them to student). No security concern. |
| D-014 | One request per attempt (not per question) | Reduces server load. Frontend batches everything. |
| D-015 | Per-question analytics: time + chosen answer | Enables identifying hard questions and common wrong answers. |
| D-016 | Visual progress computed client-side | Unit stamp = all topics stamped. No server storage for unit state. |
| D-017 | Challenge XP completely separate from main XP | Isolation. Challenge should not inflate main progression. |
| D-018 | Fixed XP per correct answer | Fairness across topics with different question counts. |
| D-019 | XP based on best overall score (not best passing) | Even failed attempts reward effort and motivate retries. |
| D-020 | XP delta only (earn difference when improving) | Prevents XP farming by repeated easy attempts. |
| D-021 | Leaderboard: plan-scoped, subject + plan level | Fair competition among peers. Two views for granularity. |
| D-022 | Leaderboard: top 20 + own rank | Standard pattern. Matches existing leaderboard design. |
| D-023 | Leaderboard: periodic refresh, not real-time | Reduces database/cache load. |
| D-024 | CDN JSON cache per topic | Zero database load for question delivery. Same pattern as hierarchy cache. |
| D-025 | Season reset at END of season, not start of new one | Matches system behavior — season expiry triggers cleanup. |
| D-026 | Archive before reset (details deferred) | Data preservation for analytics. Mechanism TBD. |
| D-027 | No time limit per attempt | Simplicity. Time recorded for analytics, not enforced. Future enhancement possible. |
| D-028 | Competition only between same-plan students | Fairness — same curriculum, same difficulty. |

---

## 19. Success Criteria

| ID | Criterion | Target |
|----|-----------|--------|
| SC-001 | Topic JSON loads from CDN | < 500ms P95 |
| SC-002 | Attempt submission (save + FSRS) | < 2 seconds P95 |
| SC-003 | Hierarchy browsing latency | < 1 second P95 |
| SC-004 | Leaderboard load (top 20 + my rank) | < 500ms P95 |
| SC-005 | Challenge XP never appears in main XP/leaderboard | Verified by test |
| SC-006 | FSRS receives every answer from completed attempts | Verified by test |
| SC-007 | Incomplete attempts leave zero trace | Verified by test |
| SC-008 | Empty topics do not break unlock chain | Verified by test |
| SC-009 | Students cannot play inaccessible content | Verified by backend enforcement |
| SC-010 | Season reset clears all challenge data | Verified by test |