# Feature Specification: Live Challenges

**Feature Branch**: `037-live-challenges`
**Created**: 2026-03-07
**Status**: Draft
**Input**: User description: "Timed examination feature where admins create scheduled exam events that students join simultaneously via shared link, answer questions at their own pace, receive instant scores, and see a final leaderboard after the event ends."

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Admin Creates and Schedules a Live Challenge Event (Priority: P1)

An admin creates a new exam event by providing a name, description, scheduled start time, waiting room duration, exam duration, capacity, and a set of multiple-choice questions. The admin can add questions manually or import them from the existing question bank. Once saved in Draft status, the event automatically transitions to Waiting Room when the scheduled time arrives.

**Why this priority**: Without event creation and scheduling, no other functionality can operate. This is the foundation of the entire feature.

**Independent Test**: Can be fully tested by creating an event in the admin panel, verifying all fields persist, and confirming the automatic state transition from Draft to Waiting Room at the scheduled time.

**Acceptance Scenarios**:

1. **Given** an admin is on the event creation screen, **When** they fill in all required fields (name, scheduled start, waiting room duration, exam duration, capacity, at least one question with correct answer), **Then** the event is saved in Draft status.
2. **Given** an event is in Draft status, **When** the scheduled start time arrives, **Then** the system automatically transitions the event to Waiting Room status.
3. **Given** an event is in Draft status, **When** the admin attempts to schedule it during a time slot that overlaps with another event's reserved slot (waiting room duration + exam duration + 5-minute buffer), **Then** the system rejects the schedule with a conflict error.
4. **Given** an event has left Draft status, **When** the admin attempts to edit any field, **Then** the system prevents the modification.

---

### User Story 2 - Student Joins, Takes the Exam, and Receives Instant Score (Priority: P1)

A registered student opens a shared link, is validated for eligibility (registered user, eligible study plan, capacity not full), enters the waiting room, waits for the countdown, and then progresses through questions one at a time at their own pace. When finished, the student submits all answers in a single request and immediately sees their score out of 100.

**Why this priority**: This is the core student experience and the primary value proposition of the feature. Without it, the feature has no purpose.

**Independent Test**: Can be fully tested by having a student join via link, answer all questions, submit, and verify that the correct score is returned immediately.

**Acceptance Scenarios**:

1. **Given** an event is in Waiting Room status and has capacity remaining, **When** a registered student with an eligible study plan opens the event link, **Then** the student enters the waiting room and sees the countdown timer.
2. **Given** an event is in Waiting Room status and capacity is full, **When** another student attempts to join, **Then** the system rejects the join with a capacity-full error.
3. **Given** an event transitions from Waiting Room to Active, **When** a student is in the waiting room, **Then** the student receives a start signal and sees the first question.
4. **Given** an event is Active and a student has answered all questions, **When** the student submits their answers, **Then** the server grades the answers, calculates the score (correct answers x 100 / total questions), and returns the score immediately.
5. **Given** an event is Active with a per-question timer enabled, **When** the timer expires for a question the student has not answered, **Then** the client auto-advances to the next question and records the unanswered question as incorrect.
6. **Given** an event is Active and the global exam timer expires, **When** a student has not yet submitted, **Then** the student's attempt is lost and they receive no score.
7. **Given** an event is in Active status and has capacity remaining, **When** a registered student who has not yet joined opens the event link, **Then** the student joins as a late participant, bypasses the waiting room, and can immediately start answering questions with the remaining exam time.

---

### User Story 3 - Waiting Room with WebSocket Start Signal (Priority: P1)

Students who have joined an event in Waiting Room status see a live countdown. When the countdown reaches zero, all connected students simultaneously receive a start signal via WebSocket and the exam begins.

**Why this priority**: The synchronized start is essential for fairness and is tightly coupled with the core exam flow.

**Independent Test**: Can be tested by having multiple clients connect to the waiting room WebSocket, verifying they all receive the start signal within the same moment when the countdown ends.

**Acceptance Scenarios**:

1. **Given** an event is in Waiting Room status, **When** a student connects via WebSocket, **Then** they receive the current countdown state.
2. **Given** multiple students are connected to the waiting room, **When** the countdown reaches zero, **Then** all connected students receive the start signal simultaneously and the event transitions to Active.
3. **Given** a student's WebSocket connection drops during the waiting room, **When** they reconnect before the countdown ends, **Then** they rejoin the waiting room with the current countdown state.

---

### User Story 4 - Leaderboard Calculation and Display After Event Ends (Priority: P2)

After the event ends (either by timer expiry or manual end), the system computes a leaderboard ranking all participants by score in descending order. Students with identical scores share the same rank. The top 20 entries are stored on the event record, and each student's individual rank is stored on their participation record.

**Why this priority**: The leaderboard provides competitive motivation and is a key engagement feature, but the exam can function without it.

**Independent Test**: Can be tested by ending an event with multiple submissions and verifying that ranks are correctly computed (including shared ranks for ties) and stored both on the event (top 20 JSON) and on each participation record.

**Acceptance Scenarios**:

1. **Given** an event has ended with multiple student submissions, **When** the leaderboard is calculated, **Then** students are ranked by score descending, and students with identical scores share the same rank (standard competition ranking: 1, 1, 3 -- not 1, 1, 2).
2. **Given** an event has ended and leaderboard is calculated, **When** a student requests the leaderboard (and show_student_rank is enabled), **Then** the top 20 entries are returned along with the student's own rank.
3. **Given** an event has ended, **When** a student requests their own result, **Then** they see their score and (if show_correct_answers is enabled) which questions they got wrong and the correct answers.

---

### User Story 5 - XP Rewards Distribution (Priority: P2)

After the event ends and the leaderboard is computed, XP rewards are distributed: participation XP to every student who submitted, plus bonus XP for 1st, 2nd, and 3rd place, and default XP for all other participants.

**Why this priority**: XP rewards enhance gamification but the exam experience is complete without them.

**Independent Test**: Can be tested by verifying that after event completion, each student's wallet reflects the correct XP amount based on their rank.

**Acceptance Scenarios**:

1. **Given** an event has ended and leaderboard is computed, **When** XP distribution runs, **Then** every student who submitted receives participation XP.
2. **Given** an event has ended, **When** XP distribution runs, **Then** the 1st-place student receives first_place_xp, 2nd receives second_place_xp, 3rd receives third_place_xp, and all others receive default_xp, each in addition to participation XP.
3. **Given** two students share 1st place, **When** XP distribution runs, **Then** both students receive first_place_xp.

---

### User Story 6 - Admin Monitors Active Event via Dashboard (Priority: P3)

During an active event, the admin can view a dashboard showing the number of connected students, number of submissions received, number still taking the exam, and time remaining. After the event ends, the admin can view the full leaderboard, drill down into individual student answers, and see aggregate statistics.

**Why this priority**: Admin monitoring enhances operational visibility but is not required for the exam to function.

**Independent Test**: Can be tested by starting an event, having students join and submit, and verifying the dashboard counters update correctly. After the event, verify the results drill-down shows correct data.

**Acceptance Scenarios**:

1. **Given** an event is Active, **When** the admin opens the dashboard, **Then** they see: connected count, submitted count, still-taking count, and time remaining.
2. **Given** an event has Ended, **When** the admin opens the dashboard, **Then** they see the full leaderboard, aggregate statistics (average score, highest score, completion rate), and can drill into any student's detailed answers.

---

### User Story 7 - Eligible Study Plans Restriction (Priority: P2)

Events can be restricted to students on specific study plans. Only students whose plan matches one of the eligible plans can join the event.

**Why this priority**: Plan-based eligibility is important for segmenting student populations but could be deferred to a later iteration if needed.

**Independent Test**: Can be tested by creating an event with specific eligible plans, then verifying that a student on an eligible plan can join while a student on a non-eligible plan is rejected.

**Acceptance Scenarios**:

1. **Given** an event has eligible study plans configured, **When** a student on a matching plan attempts to join, **Then** the student is allowed into the waiting room.
2. **Given** an event has eligible study plans configured, **When** a student on a non-matching plan attempts to join, **Then** the system rejects the join with an eligibility error.
3. **Given** an event has no eligible plans configured (empty), **When** any registered student attempts to join, **Then** the student is allowed (no plan restriction).

---

### Edge Cases

- What happens when a student submits answers twice for the same event? System must reject duplicate submissions.
- What happens when the server crashes mid-event with submissions in the in-memory queue? Up to 30 seconds of submissions may be lost (documented acceptable data loss window).
- What happens if no students submit before the event ends? The leaderboard is empty, no XP is distributed, and the event transitions to Ended normally.
- What happens when a student reconnects after a dropped connection during the Active phase? They can continue answering questions if the event is still Active; their client-side stored answers are preserved.
- What happens if the admin creates an event with zero questions? The system must reject the event, requiring at least one question before leaving Draft.
- What happens when exam duration is very short (e.g., 1 minute) and many students submit simultaneously? The batch queue absorbs the spike (50 submissions per batch or every 30 seconds) and a mandatory flush runs when the event ends.
- What happens when two events are scheduled back-to-back with exactly the buffer gap? The system allows both events since the reserved slots do not overlap.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: System MUST allow admins to create exam events with all configuration fields: name, description, scheduled start, waiting room duration, exam duration, capacity, per-question timer settings, display settings, and XP reward amounts.
- **FR-002**: System MUST support adding questions to an event either by manual entry (question text + 4 options + correct answer) or by importing from the existing Memora Review Items question bank.
- **FR-003**: System MUST enforce event lifecycle state transitions: Draft -> Waiting Room -> Active -> Ended, with automatic transitions at the scheduled times.
- **FR-004**: System MUST prevent any edits to an event once it leaves Draft status.
- **FR-005**: System MUST prevent scheduling overlapping events by validating that the new event's reserved time slot (waiting room duration + exam duration + 5-minute buffer) does not conflict with any existing event.
- **FR-006**: System MUST enforce capacity limits using an atomic counter, rejecting joins on a first-come-first-served basis once full.
- **FR-007**: System MUST validate student eligibility before allowing event joins: registered user, eligible study plan (if configured), and capacity check.
- **FR-008**: System MUST broadcast a start signal to all waiting room participants via WebSocket when the waiting room countdown reaches zero.
- **FR-009**: System MUST accept batch submissions containing all answers (question index + selected option) in a single request.
- **FR-010**: System MUST grade submissions server-side by comparing against correct answers stored securely, never exposing correct answers to clients (except after submission if show_correct_answers is enabled).
- **FR-011**: System MUST calculate scores as: (correct answers / total questions) x 100, with equal weight per question.
- **FR-012**: System MUST return the student's score immediately upon submission.
- **FR-013**: System MUST reject duplicate submissions from the same student for the same event.
- **FR-014**: System MUST reject submissions received after the event has ended.
- **FR-015**: System MUST compute the leaderboard after the event ends, using standard competition ranking (students with identical scores share the same rank; next distinct rank equals the count of players ranked above).
- **FR-016**: System MUST store the top 20 leaderboard entries on the event record and each student's individual rank on their participation record.
- **FR-017**: System MUST distribute XP rewards after leaderboard computation: participation XP to all submitters, plus rank-based bonus XP (1st, 2nd, 3rd, default for others).
- **FR-018**: System MUST buffer submissions in a queue and flush to persistent storage in batches (every 50 submissions or every 30 seconds, whichever comes first), with a mandatory flush when the event ends.
- **FR-019**: System MUST provide admin dashboard data during Active events: connected count, submitted count, still-taking count, and time remaining.
- **FR-020**: System MUST provide admin dashboard data after Ended events: full leaderboard, per-student answer details, and aggregate statistics (average score, highest score, completion rate).
- **FR-021**: System MUST support reconnection during the Active phase -- a student whose connection drops can reconnect and continue as long as the event is still Active.
- **FR-022**: System MUST require at least one question to be present before an event can leave Draft status.
- **FR-023**: System MUST use server-authoritative timing for all duration and deadline enforcement -- client device time is never trusted.

### Key Entities

- **Live Challenge Event**: A scheduled exam event containing all configuration (name, timing, capacity, display settings, XP rewards), a set of questions (child records), and the final leaderboard (top 20). Lifecycle states: Draft, Waiting Room, Active, Ended.
- **Live Challenge Question**: A single multiple-choice question belonging to an event. Contains question text, four options (A/B/C/D), the correct answer, and an optional reference to a source question in the question bank. Ordered by index.
- **Live Challenge Participation**: A record of one student's participation in one event. Contains the student reference, score (out of 100), computed rank, start/finish timestamps, and a detailed answer record (array of question index + correct/incorrect).
- **Eligible Study Plan**: A link between an event and the study plans whose students are allowed to participate. If no plans are linked, all registered students are eligible.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: Students can join a waiting room, receive the start signal, complete all questions, and see their score within the exam duration without any interruption or error.
- **SC-002**: System supports at least 1,000 concurrent participants in a single event without degradation of the submission or scoring flow.
- **SC-003**: Students receive their score within 2 seconds of submitting their answers.
- **SC-004**: The leaderboard is computed and available to all participants within 60 seconds of the event ending.
- **SC-005**: XP rewards are distributed to all participants within 120 seconds of the event ending.
- **SC-006**: Zero data loss for submissions that were acknowledged to the student (server returned a score), even under peak load.
- **SC-007**: Scheduling conflict detection correctly prevents 100% of overlapping events.
- **SC-008**: Capacity enforcement is exact -- never allows more participants than the configured capacity.

## Assumptions

- **A-001**: The payment system for paid events is deferred and will be built separately. The `is_paid` flag is stored but payment access checks are not implemented in this iteration.
- **A-002**: Event notifications (how students learn about upcoming events) are out of scope. The admin shares the link manually.
- **A-003**: CSV/PDF export of results is out of scope for this iteration.
- **A-004**: A student event history page is out of scope for this iteration.
- **A-005**: The per-question timer is purely client-side for display; the server does not enforce per-question time limits, only the global exam duration.
- **A-006**: When two students share a rank (e.g., both 1st place), both receive the XP reward for that rank (e.g., both get first_place_xp).
- **A-007**: The "student" entity maps to the existing Memora Player Profile / User in the platform.
- **A-008**: WebSocket is used only for the waiting room countdown and start signal broadcast -- not for any communication during the exam itself.
- **A-009**: The in-memory submission queue has a worst-case data loss window of 30 seconds if the server crashes. This is an accepted trade-off for performance.
- **A-010**: Questions imported from Memora Review Items are copied into the event's child table at creation time -- subsequent changes to the source review item do not affect the event's questions.
