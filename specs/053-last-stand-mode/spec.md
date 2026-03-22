# Feature Specification: Live Challenge Mode - Last Stand

**Feature Branch**: `053-last-stand-mode`
**Created**: 2026-03-22
**Status**: Draft
**Input**: User description: "PRD - Live Challenge Mode: Last Stand (Revision 2)"

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Admin Creates a Last Stand Event (Priority: P1)

An admin creates a new Live Challenge Event and selects "Last Stand" as the mode. They configure the starting hearts (e.g., 3) and question time limit. The event is saved and scheduled like any other Live Challenge Event. The mode cannot be changed after creation.

**Why this priority**: Without the ability to create Last Stand events, no other feature can function. This is the entry point for the entire mode.

**Independent Test**: Can be fully tested by creating a Last Stand event via admin interface and verifying all required fields are enforced and the event appears in event listings.

**Acceptance Scenarios**:

1. **Given** an admin is creating a new Live Challenge Event, **When** they select mode "Last Stand", **Then** the system requires `starting_hearts` (1-10) and `question_time_limit` fields.
2. **Given** a Last Stand event has been saved, **When** the admin attempts to change the mode, **Then** the system rejects the change (mode is immutable after creation).
3. **Given** an admin is creating a Last Stand event, **When** they set starting_hearts to 0 or 11, **Then** the system rejects the value with a validation error.
4. **Given** an admin is creating a Last Stand event, **When** they do not enable question_time_limit, **Then** the system rejects the event with a validation error.

---

### User Story 2 - Players Join and Play a Last Stand Event (Priority: P1)

Players join a Last Stand event during the Waiting phase. Once the event becomes Active, all players receive synchronized questions one at a time. Each player starts with the configured number of hearts. A wrong answer or missed answer (timeout) costs one heart. When a player's hearts reach zero, they are eliminated but remain connected as a passive spectator. The event continues until all questions are answered or all players are eliminated.

**Why this priority**: This is the core gameplay loop and the defining feature of Last Stand mode.

**Independent Test**: Can be fully tested by having multiple players join a Last Stand event, playing through rounds, and verifying heart deduction, elimination, and spectator behavior.

**Acceptance Scenarios**:

1. **Given** a Last Stand event is in Waiting state, **When** a player joins, **Then** they are admitted and assigned the configured starting hearts.
2. **Given** a Last Stand event is Active, **When** a new player attempts to join, **Then** the system rejects the join (no late join allowed).
3. **Given** a round is active, **When** a player submits a correct answer within the time limit, **Then** no hearts are deducted.
4. **Given** a round is active, **When** a player submits a wrong answer, **Then** one heart is deducted.
5. **Given** a round is active, **When** a player does not submit an answer before the time limit, **Then** one heart is deducted (treated as wrong).
6. **Given** a player's hearts reach zero, **When** the round result is processed, **Then** the player is eliminated and becomes a passive spectator.
7. **Given** a player is eliminated, **When** a new round starts, **Then** the player receives round updates but cannot submit answers.
8. **Given** all alive players have answered before the time limit, **When** the system detects this, **Then** the answer window ends early (result window still runs in full).

---

### User Story 3 - Round-Based Synchronized Gameplay (Priority: P1)

Each question in a Last Stand event is a "round" with three phases: answer window (duration = question_time_limit), result window (configurable, default 3 seconds), and transition to next round. The server controls timing and synchronization. Each round has a unique round_id, and answers must match the current round_id to be accepted.

**Why this priority**: The round system is the mechanism that makes Last Stand work. Without synchronized rounds, the mode cannot function.

**Independent Test**: Can be tested by running a Last Stand event and verifying phase transitions, timing enforcement, and round_id validation.

**Acceptance Scenarios**:

1. **Given** a round is in the answer window phase, **When** a player submits an answer matching the current round_id, **Then** the answer is accepted.
2. **Given** a round is in the answer window phase, **When** a player submits an answer with a mismatched round_id, **Then** the answer is rejected.
3. **Given** the answer window has ended, **When** a player submits an answer, **Then** the answer is rejected.
4. **Given** the answer window ends, **When** the result window begins, **Then** the server evaluates all answers, updates hearts, and broadcasts elimination results.
5. **Given** the result window ends, **When** there are remaining questions and alive players, **Then** the next round begins automatically.

---

### User Story 4 - Disconnect and Reconnect Handling (Priority: P2)

Players who disconnect during an active round are not compensated. If no answer is received before the round closes, the disconnected player loses a heart. Reconnection is allowed while the event is Active. On reconnect, the player resumes at the current round if still alive, or becomes a spectator if eliminated.

**Why this priority**: Disconnect handling is essential for a production-ready real-time mode, but the core gameplay must work first.

**Independent Test**: Can be tested by simulating player disconnects mid-round and verifying heart deduction and reconnect behavior.

**Acceptance Scenarios**:

1. **Given** a player disconnects during a round, **When** the round closes without their answer, **Then** the player loses one heart.
2. **Given** an eliminated player disconnects, **When** they reconnect while the event is Active, **Then** they rejoin as a spectator.
3. **Given** an alive player disconnects, **When** they reconnect while the event is Active, **Then** they resume at the current round with their remaining hearts.
4. **Given** a player disconnects, **When** they miss multiple rounds, **Then** they lose one heart per missed round (no compensation for missed rounds).

---

### User Story 5 - Event Ends and Results Are Persisted (Priority: P1)

When the event ends (all questions finished or all players eliminated), the system reconciles runtime state into participation records. Each player gets a participation record with final score, hearts remaining, elimination status, and rank. The leaderboard is available after the event ends.

**Why this priority**: Results persistence and ranking are required for the feature to deliver value to players and admins.

**Independent Test**: Can be tested by completing a Last Stand event and verifying participation records, ranking, and leaderboard data.

**Acceptance Scenarios**:

1. **Given** all questions have been answered, **When** the event ends, **Then** participation records are created for all players with correct scores, hearts, and elimination data.
2. **Given** all players are eliminated before all questions are answered, **When** the last player is eliminated, **Then** the event ends.
3. **Given** only one player remains alive, **When** there are still questions remaining, **Then** the event continues until all questions are finished.
4. **Given** the event has ended, **When** ranking is computed, **Then** players are ranked by: score (higher first), hearts remaining (higher first), average response time (lower first).
5. **Given** the event has ended, **When** a player or admin requests the leaderboard, **Then** the final leaderboard is available.

---

### User Story 6 - Admin Monitors Active Last Stand Event (Priority: P2)

During an active Last Stand event, the admin dashboard shows the current alive count, eliminated count, and current round number. After the event ends, the admin can view the final ranking and full leaderboard.

**Why this priority**: Admin visibility is important for operational control but is not part of the core gameplay loop.

**Independent Test**: Can be tested by running a Last Stand event and checking admin dashboard displays correct live stats and post-event results.

**Acceptance Scenarios**:

1. **Given** a Last Stand event is Active, **When** the admin views the dashboard, **Then** they see alive count, eliminated count, and current round number.
2. **Given** a Last Stand event has Ended, **When** the admin views the dashboard, **Then** they see the final ranking and full leaderboard.

---

### User Story 7 - Exam Mode Remains Unchanged (Priority: P1)

All existing exam mode functionality, lifecycle, analytics, scheduling, and infrastructure continue to work exactly as before. The default mode for new events is "exam". Existing events are unaffected.

**Why this priority**: Backward compatibility is critical - no regression in the existing system is acceptable.

**Independent Test**: Can be tested by running all existing exam mode workflows and verifying no behavioral changes.

**Acceptance Scenarios**:

1. **Given** no mode is specified when creating an event, **When** the event is saved, **Then** the mode defaults to "exam".
2. **Given** an existing exam mode event, **When** the Last Stand feature is deployed, **Then** the event continues to function identically.
3. **Given** a player in an exam mode event, **When** they use the /submit endpoint, **Then** it works as before (submission is exam-only).

---

### Edge Cases

- What happens when all remaining players are eliminated in the same round? The event ends, and all eliminated players in that round share the same elimination position.
- What happens when a player submits an answer after being eliminated? The answer is rejected; eliminated players cannot submit.
- What happens if the server crashes during an active round? Runtime state is recoverable; the event resumes or reconciles gracefully.
- What happens when two players have identical scores, hearts, and response times? They share the same rank (tie).
- What happens when a player sends an answer for a previous round? The answer is rejected (round_id mismatch).
- What happens if a Last Stand event has zero players when it becomes Active? The event ends immediately with no results.
- What happens when a player tries to use /submit in Last Stand mode? The endpoint returns a MODE_NOT_SUPPORTED error.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: System MUST support a `mode` field on Live Challenge Event with values `exam` (default) and `last_stand`.
- **FR-002**: System MUST make the `mode` field immutable after event creation.
- **FR-003**: System MUST require `starting_hearts` (integer, 1-10) when mode is `last_stand`.
- **FR-004**: System MUST require `question_time_limit` to be enabled when mode is `last_stand`.
- **FR-005**: System MUST deduct one heart from a player when they answer incorrectly or fail to answer within the time limit.
- **FR-006**: System MUST eliminate a player (mark as eliminated, convert to passive spectator) when their hearts reach zero.
- **FR-007**: System MUST reject join attempts during Active phase for Last Stand events (no late join).
- **FR-008**: System MUST synchronize all players to the same question at the same time (round-based progression).
- **FR-009**: System MUST assign a unique round_id to each round and reject answers with mismatched round_ids.
- **FR-010**: System MUST end the answer window early if all alive players have submitted answers, while still running the full result window.
- **FR-011**: System MUST end the event when all questions are finished OR when no players have hearts remaining.
- **FR-012**: System MUST continue the event if only one player remains alive and questions remain.
- **FR-013**: System MUST treat disconnected players' unanswered rounds as wrong answers (heart deduction per missed round).
- **FR-014**: System MUST allow reconnection while the event is Active, resuming alive players at the current round.
- **FR-015**: System MUST rank players by: score (descending), hearts remaining (descending), average response time (ascending).
- **FR-016**: System MUST compute score as `correct_answers / total_questions * 100`.
- **FR-017**: System MUST compute average response time only from answered questions (server-side timestamps).
- **FR-018**: System MUST create participation records after event end with: final_hearts, is_eliminated, eliminated_at_question, score, rank, avg_response_time.
- **FR-019**: System MUST broadcast round_start, round_result, alive_count_update, and event_ended messages to all connected players (including eliminated spectators).
- **FR-020**: System MUST NOT send correct answers to any player until the event ends.
- **FR-021**: System MUST return MODE_NOT_SUPPORTED error when /submit is called for a Last Stand event.
- **FR-022**: System MUST NOT write to the database during Active gameplay (runtime state is managed externally).
- **FR-023**: System MUST persist all results to the database during reconciliation after event end.
- **FR-024**: System MUST support up to 10,000 concurrent players per event.
- **FR-025**: System MUST provide admin dashboard showing alive count, eliminated count, and current round during Active Last Stand events.

### Key Entities

- **Live Challenge Event**: Extended with `mode` (exam/last_stand), `starting_hearts`, and `result_window_duration` fields. Existing fields and lifecycle states (Draft/Waiting/Active/Ended) remain unchanged.
- **Round**: A single question cycle within a Last Stand event, identified by a unique round_id. Contains an answer window phase and a result window phase.
- **Player Runtime State**: Per-player state tracked during Active gameplay: hearts remaining, alive/eliminated status, answers per round, response timestamps.
- **Participation (Extended)**: Post-event record with new fields: final_hearts, is_eliminated, eliminated_at_question, score, rank, avg_response_time. Created during reconciliation.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: Admins can create and configure a Last Stand event in under 2 minutes using the existing event creation flow.
- **SC-002**: All players in a Last Stand event receive each question simultaneously, with answer windows starting within 500ms of each other.
- **SC-003**: System supports up to 10,000 concurrent players in a single Last Stand event without degradation.
- **SC-004**: Players eliminated mid-event can continue observing as spectators without connection issues.
- **SC-005**: Final rankings are deterministic - identical inputs always produce identical rankings with no ambiguity.
- **SC-006**: All existing exam mode events and workflows continue to function identically after deployment (zero regressions).
- **SC-007**: Event results are fully persisted within 60 seconds of event end, including all participation records, rankings, and leaderboard data.
- **SC-008**: Disconnected players who reconnect within the Active phase can resume gameplay within 3 seconds.
- **SC-009**: No database writes occur during Active gameplay; all runtime state is managed externally.
- **SC-010**: The leaderboard for a completed Last Stand event is available within the same timeframe as exam mode events.

## Assumptions

- Last Stand uses existing MCQ question types only; no new question types are introduced.
- The result window duration defaults to 3 seconds but is configurable per event.
- Eliminated players may remain connected and receive broadcasts, but this does not count as "spectator join" (a non-goal).
- The existing lifecycle states (Draft / Waiting / Active / Ended) are reused without modification.
- Existing scheduling, eligibility, payment, and analytics systems are reused without changes.
- Score is calculated as a percentage (0-100) based on correct answers out of total questions in the event, not just questions the player was alive for.
- Average response time is measured only for questions the player actually answered (not timeouts).
- No live leaderboard is provided during the event; leaderboard is available only after the event ends.
- The existing WebSocket connection model is reused with new message types added.
- There is no migration required for existing data; `mode` defaults to `exam` for all existing events.
