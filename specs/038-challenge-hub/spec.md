# Feature Specification: Challenge Hub (مركز التحدي)

**Feature Branch**: `038-challenge-hub`
**Created**: 2026-03-08
**Status**: Draft
**Input**: User description: "Sequential, game-like challenge mode where students prove topic mastery by answering all MCQ questions, earning Challenge XP, and competing on a separate leaderboard."

## User Scenarios & Testing *(mandatory)*

### User Story 1 — Browse Challenge Hierarchy (Priority: P1)

A student opens Challenge Hub, selects a subject from their plan, and navigates tracks, units, and topics. Each topic shows one of three states: locked, open, or stamped.

**Why this priority**: Entry point to the entire feature. Without browsing, no other functionality works.

**Independent Test**: Can be tested by loading the Challenge Hub screen and verifying that subjects, tracks, units, and topics render with correct states for a given student.

**Acceptance Scenarios**:

1. **Given** a student opens Challenge Hub, **When** the hub loads, **Then** they see only subjects from their plan.
2. **Given** a student selects a subject, **When** tracks load, **Then** all tracks for that subject are shown, with lock indicators on tracks the student has no access to.
3. **Given** a student taps a locked track, **When** the system responds, **Then** it redirects them to the normal path where they discover they need to purchase.
4. **Given** a student selects an accessible track, **When** units and topics load, **Then** the first topic of the first unit is open if the student has completed it in the normal path. Subsequent topics show as locked until the previous one is stamped in Challenge Hub.
5. **Given** a topic has zero MCQ questions, **When** the hierarchy renders, **Then** that topic is hidden from the student but inherits the stamp state of the topic before it (for unlock chain purposes).
6. **Given** Topic 1 is locked, Topic 2 is empty (hidden), Topic 3 exists, **When** Topic 1 is locked, **Then** Topic 2 remains locked (inherited), **And** Topic 3 remains locked.
7. **Given** Topic 1 becomes stamped, Topic 2 is empty (hidden), Topic 3 exists, **When** the hierarchy re-evaluates, **Then** Topic 2 auto-stamps (inherited), **And** Topic 3 becomes open.

---

### User Story 2 — Play a Topic Challenge (Priority: P1)

A student selects an open topic, answers all MCQ questions in random order, and receives a pass/fail result.

**Why this priority**: Core gameplay loop. The product has no value without the ability to play challenges.

**Independent Test**: Can be tested by selecting an open topic, answering all questions, and verifying correct scoring, stamping, and attempt storage.

**Acceptance Scenarios**:

1. **Given** a student selects an open topic, **When** the challenge loads, **Then** all MCQ questions for that topic are presented in random order.
2. **Given** the student is answering questions, **When** they answer incorrectly, **Then** the correct answer is shown immediately. They cannot go back to previous questions.
3. **Given** the student completes all questions, **When** the result is calculated, **Then** a score of 50% or higher = pass (topic stamped), below 50% = fail (can retry).
4. **Given** the student passes, **When** the result is submitted, **Then** the topic is stamped, the next topic in the unit unlocks, and the attempt is saved.
5. **Given** the student fails, **When** the result is submitted, **Then** the topic remains open (not stamped), and the attempt is saved. The student can retry immediately.
6. **Given** the student exits mid-challenge (closes app, navigates away), **When** the session is abandoned, **Then** nothing is saved — no attempt record, no spaced repetition update. As if it never happened.
7. **Given** a topic has 100 questions, **When** the student starts a challenge, **Then** all 100 questions must be answered in one sitting for the attempt to count.
8. **Given** the student retries a topic, **When** questions load, **Then** they appear in a different random order than the previous attempt.

---

### User Story 3 — Retry and Improve (Priority: P1)

A student replays a stamped or failed topic to improve their score and earn more Challenge XP.

**Why this priority**: Replay is the core engagement loop and the primary driver of Challenge XP accumulation.

**Independent Test**: Can be tested by replaying a topic multiple times and verifying best score tracking, XP delta calculation, and that all attempts are stored.

**Acceptance Scenarios**:

1. **Given** a student has stamped a topic with 70%, **When** they replay and score 90%, **Then** the displayed best score updates to 90%, and they earn Challenge XP for the 20% improvement.
2. **Given** a student has stamped a topic with 70%, **When** they replay and score 50%, **Then** the displayed best score remains 70%, and they earn zero additional Challenge XP.
3. **Given** a student failed a topic with 40%, **When** they replay and score 30%, **Then** the best score remains 40%, and they earn zero additional Challenge XP.
4. **Given** a student failed a topic with 40%, **When** they replay and score 70%, **Then** the displayed best score updates to 70% (best overall), and they earn Challenge XP for the 30% improvement. The topic is now stamped.
5. **Given** a student plays any attempt (pass or fail), **When** the result is submitted, **Then** every individual question result is sent to the spaced repetition algorithm regardless of pass/fail outcome.
6. **Given** unlimited retries, **When** a student replays the same topic 10 times, **Then** all 10 attempts are stored with full details.

---

### User Story 4 — Challenge XP System (Priority: P1)

A student earns Challenge XP for correct answers, motivating replays to maximize points. Challenge XP is completely isolated from the main game XP.

**Why this priority**: XP drives the leaderboard, which drives engagement. Without XP, there is no competitive motivation.

**Independent Test**: Can be tested by completing challenges and verifying XP calculation, delta-only rewards, and that Challenge XP never appears in main profile or leaderboard.

**Acceptance Scenarios**:

1. **Given** a fixed XP value per correct answer (e.g., 5 XP), **When** a topic has 20 questions and the student scores 40% (8 correct), **Then** they earn 8 x 5 = 40 Challenge XP.
2. **Given** the student replays the same topic and scores 70% (14 correct), **When** the result is submitted, **Then** they earn only the delta: (14 - 8) x 5 = 30 additional Challenge XP. Total: 70.
3. **Given** the student replays and scores 50% (10 correct), **When** the result is submitted, **Then** they earn zero additional Challenge XP because 10 correct does not exceed their previous best of 14 correct.
4. **Given** the student replays and scores 90% (18 correct), **When** the result is submitted, **Then** they earn (18 - 14) x 5 = 20 additional Challenge XP. Total: 90.
5. **Given** Challenge XP, **When** the main profile XP is queried, **Then** Challenge XP is completely absent — it exists only within Challenge Hub.
6. **Given** Challenge XP, **When** the main leaderboard is queried, **Then** Challenge XP does not appear — it feeds only the Challenge leaderboard.

---

### User Story 5 — Challenge Leaderboard (Priority: P2)

A student views their Challenge XP ranking among peers in the same plan.

**Why this priority**: Enhances motivation but the core gameplay loop works without it.

**Independent Test**: Can be tested by viewing the leaderboard after earning XP and verifying rankings, filtering by subject, and own-rank display.

**Acceptance Scenarios**:

1. **Given** a student opens the Challenge leaderboard, **When** it loads, **Then** they see the top 20 students from their plan ranked by Challenge XP, plus their own rank even if not in the top 20.
2. **Given** the student filters by subject, **When** the leaderboard loads, **Then** only Challenge XP earned in that subject is counted for ranking.
3. **Given** the student views the plan-level leaderboard (all subjects), **When** it loads, **Then** Challenge XP from all subjects is summed for ranking.
4. **Given** the leaderboard data updates on each attempt, **When** a student earns XP, **Then** their updated rank is visible the next time the client polls (controlled by configurable refresh interval).
5. **Given** a student has no plan assigned, **When** they open the Challenge leaderboard, **Then** they see an empty leaderboard with a clear indicator.
6. **Given** only 3 students in a plan have Challenge XP, **When** the leaderboard loads, **Then** only those 3 appear (no padding).

---

### User Story 6 — Season Reset (Priority: P3)

When a season ends, all Challenge Hub progress is archived and reset.

**Why this priority**: Seasonal behavior, not needed for initial launch within a season.

**Independent Test**: Can be tested by triggering a season end and verifying all challenge data is cleared and the hub appears fresh.

**Acceptance Scenarios**:

1. **Given** a season ends, **When** the reset process runs, **Then** all challenge progress (stamped topics, best scores, Challenge XP, leaderboard) is cleared.
2. **Given** a season ends, **When** the reset process runs, **Then** all challenge attempt data is archived before clearing. (Archival mechanism details are deferred.)
3. **Given** the new season starts, **When** a student opens Challenge Hub, **Then** all topics are unstamped, Challenge XP is zero, and the leaderboard is empty.

---

### Edge Cases

| #  | Scenario                                             | Expected Behavior                                                                                    |
|----|------------------------------------------------------|------------------------------------------------------------------------------------------------------|
| 1  | Topic has 0 MCQ questions                            | Hidden from hierarchy, auto-stamps when predecessor is stamped                                       |
| 2  | Student exits mid-challenge                          | Nothing saved, no spaced repetition update, attempt discarded                                        |
| 3  | Student has no plan                                  | Show empty leaderboard with clear indicator                                                          |
| 4  | Plan has 1 student                                   | Student sees themselves as #1, no neighbors                                                          |
| 5  | Student changes plan mid-season                      | Challenge XP earned under old plan stays there. New XP goes to new plan.                             |
| 6  | Teacher adds questions after student stamped a topic  | Student can replay with new questions. Stamp and best score unchanged.                               |
| 7  | Teacher deletes all questions from a topic           | Topic becomes empty — hidden, auto-stamps based on predecessor                                       |
| 8  | Topic has 1 question                                 | Student answers 1 question. Must answer correctly to pass (50% threshold).                           |
| 9  | First topic in unit has no access                    | Entire unit is effectively locked. Message redirects to normal path.                                 |
| 10 | Multiple consecutive empty topics                    | Each inherits from predecessor. Chain: stamped -> auto-stamp -> auto-stamp -> next real topic opens. |
| 11 | All topics in a unit are empty                       | Unit appears empty. All auto-stamp when predecessor (last topic of previous unit) is stamped.        |
| 12 | Student submits attempt but network fails            | Client should retry. System should handle duplicate submissions gracefully (idempotency).            |

## Requirements *(mandatory)*

### Functional Requirements

#### Hierarchy & Navigation

- **FR-001**: System MUST display only subjects from the student's plan in Challenge Hub.
- **FR-002**: System MUST show all tracks for a subject, with lock indicators on tracks the student has no access to.
- **FR-003**: System MUST present topics within units, following the same Subject > Track > Unit > Topic hierarchy as the normal path (without the Lesson level).
- **FR-004**: System MUST hide topics that have zero MCQ questions from the student view while preserving them in the unlock chain logic.

#### Topic Unlock Logic

- **FR-005**: System MUST require ALL three conditions before a topic is open: (1) student has content access, (2) student completed all lessons for this topic on the normal path, (3) previous topic in Challenge Hub is stamped OR this is the first topic in the unit.
- **FR-006**: When condition 1 fails, system MUST show a message directing the student to the normal path.
- **FR-007**: When condition 2 fails, system MUST show a message to complete the topic's lessons first.
- **FR-008**: When condition 3 fails, system MUST show a message to complete the previous topic first.
- **FR-009**: System MUST auto-stamp empty (hidden) topics when their predecessor topic is stamped, preserving the unlock chain.

#### Challenge Gameplay

- **FR-010**: System MUST present all MCQ questions for a topic in a randomized order on each attempt.
- **FR-011**: System MUST require the student to answer all questions in one sitting for the attempt to count.
- **FR-012**: System MUST stamp a topic when the student scores at or above the pass threshold (configurable, default 50%).
- **FR-013**: System MUST allow unlimited retries on any topic (both stamped and failed).
- **FR-014**: System MUST discard incomplete/abandoned attempts entirely — no data saved, no spaced repetition update.
- **FR-015**: System MUST show the correct answer immediately after an incorrect answer, with no option to go back.

#### Attempt Recording & Analytics

- **FR-016**: System MUST store every completed attempt with: student, topic, attempt number, total questions, correct count, score percentage, pass/fail, time spent, and XP earned (delta).
- **FR-017**: System MUST store per-question details for each attempt: question reference, correct/incorrect, time spent, and chosen answer.
- **FR-018**: System MUST handle duplicate attempt submissions gracefully (idempotency).

#### Challenge XP

- **FR-019**: System MUST award a fixed, configurable amount of XP per correct answer.
- **FR-020**: System MUST award XP only for improvement — the delta between the current attempt's correct count and the student's previous best correct count for that topic.
- **FR-021**: System MUST track two separate high-water marks per topic: best overall score (for XP calculation) and best passing score (for display).
- **FR-022**: Challenge XP MUST be completely isolated from the main game XP — it MUST NOT appear in the main profile, main leaderboard, wallet, or any non-Challenge system.

#### Spaced Repetition Integration

- **FR-023**: System MUST send every individual question result from completed attempts to the spaced repetition algorithm (correct = "Good" rating, incorrect = "Again" rating).
- **FR-024**: Abandoned attempts MUST NOT send any data to the spaced repetition algorithm.

#### Challenge Leaderboard

- **FR-025**: System MUST provide a leaderboard ranked by Challenge XP, scoped to students in the same plan.
- **FR-026**: System MUST support two leaderboard views: per-subject (XP from one subject) and per-plan (XP summed across all subjects).
- **FR-027**: System MUST display the top N students (configurable, default 20) plus the current student's own rank.
- **FR-028**: Leaderboard data updates on each attempt submission. Client-side polling frequency MUST be configurable (default 300s) to control how often the student sees updated rankings.

#### Season Behavior

- **FR-029**: System MUST clear all Challenge Hub data (progress, scores, XP, leaderboard) when a season ends.
- **FR-030**: System MUST archive challenge data before clearing. (Archival mechanism is deferred.)
- **FR-031**: Season reset MUST happen at the END of the season (upon season expiry), not at the start of a new one.

#### Question Delivery

- **FR-032**: Questions MUST be pre-built as cached files (one per topic) and served from a content delivery layer — zero database load for question delivery.
- **FR-033**: Cached question files MUST be rebuilt when questions are added, edited, or deleted by teachers.

#### Configurable Settings

- **FR-034**: XP per correct answer MUST be configurable (default: 5).
- **FR-035**: Pass threshold percentage MUST be configurable (default: 50%).
- **FR-036**: Leaderboard refresh interval MUST be configurable.
- **FR-037**: Leaderboard top-N count MUST be configurable (default: 20).

### Key Entities

- **Challenge Attempt**: A single completed attempt by a student on a topic. Contains scoring data (total questions, correct count, score percentage, pass/fail), timing (total time, per-question time), XP earned (delta), and per-question details (question reference, correct/incorrect, chosen answer). One student can have many attempts per topic. Sequential attempt numbering per student per topic.
- **Challenge Progress**: One record per student per topic. Tracks stamp status, best overall score (for XP), best passing score (for display), cumulative XP earned, and attempt count. This is the "current state" entity that drives unlock logic and leaderboard aggregation.
- **Challenge Leaderboard**: Aggregated Challenge XP rankings scoped by plan, with per-subject and all-subject views. Updated periodically, not in real-time. Displays top N plus the requesting student's own rank.

### Isolation Guarantees

| System               | Affected by Challenge Hub? |
|----------------------|----------------------------|
| Main XP              | No                         |
| Main Leaderboard     | No                         |
| Streak               | No                         |
| Profile Stats        | No                         |
| Wallet               | No                         |
| Spaced Repetition    | Yes — every answer sent    |
| Challenge XP         | Yes — separate system      |
| Challenge Leaderboard | Yes — separate board      |

### Decisions Log

| #     | Decision                                                 | Rationale                                                             |
|-------|----------------------------------------------------------|-----------------------------------------------------------------------|
| D-001 | Hierarchy: Subject > Track > Unit > Topic (no Lesson)    | Challenge tests topic-level mastery, not individual lessons.          |
| D-002 | Same access system as normal path                        | No new access logic needed. Consistency.                              |
| D-003 | Three unlock conditions (access + completion + sequence)  | Ensures students learn before challenging, and progress sequentially. |
| D-004 | Empty topics: hidden + auto-stamp from predecessor       | Prevents chain breakage without confusing the student.                |
| D-005 | All MCQ questions per topic, no limit                    | Challenge = comprehensive topic review.                               |
| D-006 | Random order every attempt                               | Prevents memorizing answer positions.                                 |
| D-007 | No going back to previous questions                      | Correct answer shown after wrong — going back would break this.       |
| D-008 | 50% pass threshold (configurable)                        | Balance between challenge and accessibility.                          |
| D-009 | Incomplete attempt = fully discarded                     | Simplicity. No partial saves, no partial FSRS updates.                |
| D-010 | Unlimited retries, even after stamp                      | Drives engagement and XP accumulation.                                |
| D-011 | Best passing score for display, best overall for XP      | Two separate high-water marks with different purposes.                |
| D-012 | Every answer sent to spaced repetition                   | Algorithm needs full picture for accurate scheduling.                 |
| D-013 | Frontend calculates pass/fail                            | Frontend has correct answers (shows them to student).                 |
| D-014 | One submission per attempt (not per question)             | Reduces server load. Frontend batches everything.                     |
| D-015 | Per-question analytics: time + chosen answer             | Enables identifying hard questions and common wrong answers.          |
| D-016 | Visual progress computed client-side                     | Unit stamp = all topics stamped. No server storage for unit state.    |
| D-017 | Challenge XP completely separate from main XP            | Isolation. Challenge should not inflate main progression.             |
| D-018 | Fixed XP per correct answer                              | Fairness across topics with different question counts.                |
| D-019 | XP based on best overall score (not best passing)        | Even failed attempts reward effort and motivate retries.              |
| D-020 | XP delta only (earn difference when improving)           | Prevents XP farming by repeated easy attempts.                        |
| D-021 | Leaderboard: plan-scoped, subject + plan level           | Fair competition among peers. Two views for granularity.              |
| D-022 | Leaderboard: top 20 + own rank                           | Standard pattern. Matches existing leaderboard design.                |
| D-023 | Leaderboard: periodic refresh, not real-time             | Reduces system load.                                                  |
| D-024 | Cached question files per topic                          | Zero database load for question delivery.                             |
| D-025 | Season reset at END of season, not start of new one      | Matches system behavior — season expiry triggers cleanup.             |
| D-026 | Archive before reset (details deferred)                  | Data preservation for analytics. Mechanism TBD.                       |
| D-027 | No time limit per attempt                                | Simplicity. Time recorded for analytics, not enforced.                |
| D-028 | Competition only between same-plan students              | Fairness — same curriculum, same difficulty.                          |

### Dependencies

| Dependency                     | Description                                                                |
|--------------------------------|----------------------------------------------------------------------------|
| Review Item table              | Source of all MCQ questions. Must be populated and synced.                 |
| Existing hierarchy system      | Challenge Hub reads the same Subject > Track > Unit > Topic structure.     |
| Existing access system         | Same access keys, free content flags, plan-level free subjects.           |
| Existing progress system       | Bitmap + stats used to verify topic completion on normal path.            |
| Existing spaced repetition     | Challenge reuses the same method for sending question results.             |
| Content delivery infrastructure | For hosting topic question cache files.                                   |
| Plan system                    | Leaderboard is scoped by plan. Students must have a plan for it to work.  |

### Assumptions

- The existing hierarchy (Subject > Track > Unit > Topic > Lesson) is stable and unlikely to change structurally.
- The spaced repetition system exposes a method for submitting individual question results that Challenge Hub can reuse.
- MCQ is the only question type in the Review Item table.
- The existing access control system covers all access scenarios Challenge Hub needs — no new access types are required.
- Content delivery infrastructure already exists and can host additional cached files without new infrastructure.
- A student always belongs to at most one plan at a time.
- The pass threshold (50%) and XP-per-question (5) are initial defaults that may be tuned post-launch.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: Students can load and begin a topic challenge within 2 seconds of selecting a topic.
- **SC-002**: Attempt submission (save + spaced repetition update) completes within 2 seconds for 95% of submissions.
- **SC-003**: Challenge hierarchy browsing (subject > track > unit > topic) loads within 1 second for 95% of requests.
- **SC-004**: Leaderboard (top 20 + own rank) loads within 1 second for 95% of requests.
- **SC-005**: Challenge XP never appears in main XP, main leaderboard, wallet, or any non-Challenge system — verified by test.
- **SC-006**: Every answer from completed attempts is received by the spaced repetition algorithm — verified by test.
- **SC-007**: Incomplete/abandoned attempts leave zero trace in any data store — verified by test.
- **SC-008**: Empty topics do not break the sequential unlock chain — verified by test.
- **SC-009**: Students cannot play challenges for content they don't have access to — verified by enforcement test.
- **SC-010**: Season reset clears all challenge data (progress, XP, leaderboard) — verified by test.
- **SC-011**: 95% of students who start a challenge can complete it and see their result without errors.
- **SC-012**: Students can replay any topic (stamped or failed) and see correct XP delta calculation.
