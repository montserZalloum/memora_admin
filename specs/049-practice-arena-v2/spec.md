# Feature Specification: Practice Arena V2

**Feature Branch**: `049-practice-arena-v2`
**Created**: 2026-03-14
**Status**: Draft
**Input**: Full backend redesign of Practice Arena to eliminate real-time database queries during gameplay, introduce CDN-based content delivery, and decouple read/write paths for high concurrency.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Start a Practice Session Instantly (Priority: P1)

A student selects a subject and one or more tracks (optionally filtered by units or topics) and receives their first batch of 20 questions within 200ms. Questions are prioritized: unseen first, then previously incorrect, then lowest correct ratio, then oldest seen. The student's prior history across all previous sessions is reflected immediately.

**Why this priority**: This is the core entry point to the feature. Without fast, personalized session start, the entire Practice Arena is unusable. It validates the read path (CDN content + cached player history + in-memory selection) end-to-end.

**Independent Test**: Can be fully tested by starting a session for a player with known history and verifying the returned question IDs match the expected priority order, are drawn from the correct scope, and arrive within latency targets.

**Acceptance Scenarios**:

1. **Given** a student with no prior practice history, **When** they start a session for a subject with 500 questions across 2 tracks, **Then** 20 unseen questions are returned within 200ms with the correct chunk references.
2. **Given** a student who has seen 100 questions (30 incorrect, 70 correct), **When** they start a session for the same scope, **Then** the 20 questions with the worst performance (incorrect first, then lowest correct ratio) are returned first.
3. **Given** a student who has seen all questions in a track, **When** they start a session, **Then** the 20 oldest-seen questions are returned with an `all_seen_warning` flag set to true.
4. **Given** a student selects multiple tracks, **When** they attempt to also filter by units or topics, **Then** the request is rejected with a validation error.
5. **Given** a student with no cached history, **When** they start a session, **Then** the system reads their summary from the database (single row per track), caches it, and still responds within 200ms.

---

### User Story 2 - Submit Answers and See Results Immediately (Priority: P1)

A student submits their answers for the current batch of 20 questions and immediately receives accuracy statistics (correct count, total count, accuracy percentage). The database write happens in the background — the student never waits for it.

**Why this priority**: Submission is the other half of the core loop. Without fast, reliable submission with background persistence, the gameplay experience breaks down.

**Independent Test**: Can be fully tested by submitting a batch of results and verifying the response contains correct stats, the player's cached summary is updated, and a write message is enqueued.

**Acceptance Scenarios**:

1. **Given** a student has an active session with batch_seq 0 containing 20 question IDs, **When** they submit results for all 20 questions, **Then** they receive correct_count, total_count, and accuracy_percent within 100ms.
2. **Given** a student submits results containing an item_id not in the current batch, **Then** the submission is rejected.
3. **Given** a student submits the same batch_seq twice, **Then** the second submission returns the cached stats with `is_duplicate: true` and does not re-enqueue the write.
4. **Given** a student submits results, **When** the database is temporarily unavailable, **Then** the submission still succeeds (stats returned from cache), and the write is queued for background processing.

---

### User Story 3 - Continue to Next Batch Without Repeats (Priority: P1)

After submitting answers, a student requests the next batch. The system uses their updated history (including the just-submitted answers) to select 20 new questions with no repeats within the session.

**Why this priority**: Continue is what keeps students engaged across multiple batches. Without it, sessions are limited to a single batch.

**Independent Test**: Can be tested by submitting batch 0, requesting continue, and verifying batch 1 contains different question IDs that reflect the updated priority ordering.

**Acceptance Scenarios**:

1. **Given** a student has submitted batch_seq 0, **When** they request continue with batch_seq 0, **Then** they receive batch_seq 1 with 20 new question IDs that do not overlap with batch 0.
2. **Given** a student has NOT submitted the current batch, **When** they request continue, **Then** the request is rejected.
3. **Given** a student submits answers where 5 were incorrect, **When** they continue, **Then** those 5 incorrect questions are deprioritized correctly relative to unseen questions but prioritized over previously correct ones.

---

### User Story 4 - Content Updates Propagate Quickly (Priority: P2)

When a content editor publishes, edits, or deletes a question, the change is reflected in new sessions within 60 seconds. Active sessions handle deleted questions gracefully (skip them).

**Why this priority**: Content freshness is important for the platform but not as critical as core gameplay. It enables content editors to iterate quickly.

**Independent Test**: Can be tested by modifying a question, waiting up to 60 seconds, and verifying the map file and content chunks reflect the change. Active session behavior can be tested by removing a question from a chunk and verifying the client skips it.

**Acceptance Scenarios**:

1. **Given** a content editor adds a new question to a topic, **When** a student starts a new session 60 seconds later, **Then** the new question appears in the candidate pool.
2. **Given** a content editor deletes a question, **When** a student's active session references that question, **Then** the client skips the deleted question without error.
3. **Given** a content editor modifies a question's content, **When** the client fetches the chunk, **Then** the latest version is returned.
4. **Given** a chunk containing 100 questions is regenerated, **When** only 1 question changed, **Then** only the affected chunk (not all chunks) is regenerated.

---

### User Story 5 - Session Lifecycle Management (Priority: P2)

Sessions automatically expire after 1 hour of inactivity. Only one active session per player is allowed — starting a new one replaces the old. Session creation is rate-limited to 5 per player per hour.

**Why this priority**: Prevents resource leaks and abuse. Important for operational health but not the core gameplay loop.

**Independent Test**: Can be tested by creating sessions and verifying TTL behavior, replacement behavior, and rate limiting independently.

**Acceptance Scenarios**:

1. **Given** a player has an active session, **When** they are idle for more than 1 hour, **Then** the session expires and any pending (unsubmitted) results are discarded.
2. **Given** a player has an active session, **When** they start a new session, **Then** the old session is replaced.
3. **Given** a player has created 5 sessions in the last hour, **When** they attempt to create a 6th, **Then** the request is rejected with a rate limit error.
4. **Given** a player submits or continues within a session, **Then** the session's inactivity timer is refreshed.

---

### User Story 6 - Player History Persists Across Sessions (Priority: P2)

A player's practice history (which questions they've seen, their results, attempt counts) persists across sessions. Starting a new session the next day reflects all prior practice activity.

**Why this priority**: Long-term progress tracking is what makes the Practice Arena valuable as a learning tool. Without persistence, students lose motivation.

**Independent Test**: Can be tested by having a player complete a session, waiting, starting a new session, and verifying the question selection reflects prior history.

**Acceptance Scenarios**:

1. **Given** a player saw 50 questions yesterday and got 10 wrong, **When** they start a new session today, **Then** the 10 incorrect questions appear with higher priority than the 40 correct ones.
2. **Given** a player's cached summary has expired, **When** they start a new session, **Then** the summary is re-read from the database and re-cached.
3. **Given** the background write worker processes a batch of results, **Then** both the historical record and the player summary are updated consistently.

---

### User Story 7 - Background Write Worker Processes Results (Priority: P2)

Results submitted by players are persisted to the database by a background worker. The worker is idempotent (processing the same message twice does not corrupt data), handles database unavailability with retry and backoff, and keeps the historical record and player summary in sync.

**Why this priority**: Data integrity is critical but happens entirely in the background. The player is never blocked by this process.

**Independent Test**: Can be tested by enqueuing write messages and verifying the database state after processing.

**Acceptance Scenarios**:

1. **Given** a player submits 20 results, **When** the background worker processes the message, **Then** the historical practice log is updated via upsert and the player summary reflects the new results.
2. **Given** the same message is processed twice, **Then** attempt counts and correct counts are not double-incremented.
3. **Given** the database is unavailable, **When** the worker attempts to process, **Then** it retries with exponential backoff (up to 5 retries).
4. **Given** a malformed message, **Then** the worker logs the error and moves the message to a dead-letter queue without crashing.

---

### User Story 8 - Operational Observability (Priority: P3)

Operations engineers can monitor write queue depth, worker health, and cache hit/miss ratios. The system recovers gracefully from infrastructure failures (Redis restart, database outage).

**Why this priority**: Observability enables proactive operations but is not user-facing functionality.

**Independent Test**: Can be tested by checking that metrics/logs are emitted during normal operations and by simulating failure scenarios.

**Acceptance Scenarios**:

1. **Given** the write queue depth exceeds 1000 messages, **Then** an alert is triggered.
2. **Given** Redis is restarted, **When** a player starts a new session, **Then** their summary is re-read from the database (at most one session's worth of data is lost).
3. **Given** the database is down for 1 hour, **Then** active sessions continue unaffected, results are queued, and when the database recovers, all queued results are processed.

---

### Edge Cases

- What happens when a player's selected scope contains zero questions? The system returns an empty batch with `total_available: 0`.
- What happens when the CDN is unreachable? The client cannot load questions; the server-side session still functions but the client cannot render. CDN multi-region failover mitigates this.
- What happens when a player submits results with duplicate item_ids in the same payload? The submission is rejected.
- What happens when the write queue accumulates a large backlog? No player-facing impact; the worker catches up when capacity is available.
- What happens when a player's summary row exceeds 500 KB (5,000+ questions per track)? The system continues to function but row sizes should be monitored; splitting by unit is a future mitigation.
- What happens when a content change triggers chunk regeneration while a player is mid-session? The client loads the latest chunk from CDN; deleted questions are skipped.

## Requirements *(mandatory)*

### Functional Requirements

**Content Delivery**

- **FR-001**: System MUST generate a map file per subject containing all question IDs organized by track/unit/topic, with chunk references for each question.
- **FR-002**: System MUST generate content chunks of approximately 100 questions each, grouped by topic, containing full question content.
- **FR-003**: Map files and content chunks MUST be served via a CDN with long-lived cache and explicit invalidation on content changes.
- **FR-004**: Content generation MUST be triggered automatically when a question is created, updated, or deleted.
- **FR-005**: Only affected chunks MUST be regenerated on content changes (not all chunks for the subject).
- **FR-006**: Map files MUST include subject-level metadata: total question count and generation timestamp.

**Session Management**

- **FR-007**: Players MUST be able to start a session specifying a subject and one or more tracks, optionally filtered by units or topics.
- **FR-008**: Only one active session per player is allowed at a time; starting a new session replaces any existing one.
- **FR-009**: Sessions MUST expire after 1 hour of inactivity; pending unsubmitted results are discarded on expiry.
- **FR-010**: Session creation MUST be rate-limited to a maximum of 5 sessions per player per hour.

**Question Selection**

- **FR-011**: Questions MUST be selected using the map file and player summary with zero database queries in the selection path.
- **FR-012**: Selection MUST filter by the player's chosen scope (tracks, and optionally units or topics).
- **FR-013**: Selection priority order MUST be: never seen > last incorrect > lowest correct ratio > oldest seen.
- **FR-014**: Batch size MUST be 20 questions.
- **FR-015**: No question MUST repeat within a session until all in-scope questions have been served; when all are seen, the system wraps around to oldest-seen questions with a warning flag.
- **FR-016**: Response MUST include question IDs and the chunk references needed by the client.

**Answer Submission**

- **FR-017**: Players MUST submit a batch of results containing item IDs and correctness indicators.
- **FR-018**: Submitted item IDs MUST match the current batch; unknown IDs are rejected.
- **FR-019**: Duplicate submissions for the same batch MUST return cached statistics without re-processing.
- **FR-020**: Player summary MUST be updated immediately in cache on submission.
- **FR-021**: Results MUST be pushed to a write queue for background persistence to the database.
- **FR-022**: Submit response MUST include correct count, total count, and accuracy percentage.

**Continue (Next Batch)**

- **FR-023**: Continue MUST require that the current batch has been submitted.
- **FR-024**: Continue MUST use the updated player summary (reflecting the latest submission) for question selection.
- **FR-025**: Continue MUST return the next batch in the same format as session start.

**Player Practice Summary**

- **FR-026**: A new data store MUST maintain one record per player per track containing per-question history (last result, attempt count, correct count, last seen timestamp).
- **FR-027**: The player summary MUST be the source of truth for question selection priority.
- **FR-028**: The summary MUST be cached with a 2-hour time-to-live; on cache miss, it is read from the database.

**Background Write Worker**

- **FR-029**: A background worker MUST consume from the write queue and persist results to the database.
- **FR-030**: The worker MUST upsert into the historical practice log (preserving existing behavior).
- **FR-031**: The worker MUST update the corresponding player summary record.
- **FR-032**: The worker MUST be idempotent — processing the same message twice MUST NOT corrupt data (no double-counting of attempts or correct answers).
- **FR-033**: The worker MUST handle database unavailability with retry and exponential backoff (maximum 5 retries).
- **FR-034**: Malformed messages MUST be logged and moved to a dead-letter queue.

**Scope Validation**

- **FR-035**: If multiple tracks are selected, unit and topic filters MUST be null (rejected otherwise).
- **FR-036**: If multiple units are selected, topic filters MUST be null (rejected otherwise).

### Key Entities

- **Subject**: Top-level content container. Has a map file on CDN and contains multiple tracks.
- **Track**: A learning path within a subject. Player summaries are scoped per track. Contains units.
- **Unit**: A grouping of topics within a track.
- **Topic**: A grouping of lessons. Questions are organized by topic in both map files and content chunks.
- **Question (Review Item)**: An individual practice item with type, stem, choices, correct answer, and explanation. Identified by UUID. Referenced in map files by ID + chunk number.
- **Player Practice Summary**: One record per (player, track) pair. Contains a JSON history of every question the player has encountered in that track, including last result, attempt count, correct count, and last seen timestamp.
- **Practice Log**: The historical record of all player interactions. One row per (player, question) pair with cumulative statistics. Preserved for reporting and analytics.
- **Session**: An active practice context for a player. Contains the selected scope, current batch, and session metadata. Expires after 1 hour of inactivity.
- **Content Chunk**: A CDN-hosted file containing full question details for approximately 100 questions, grouped by topic.
- **Map File**: A CDN-hosted index of all questions in a subject, organized hierarchically (track > unit > topic). Contains only IDs and chunk references, no question content.
- **Write Queue Message**: A buffered batch of player results awaiting background persistence. Contains player ID, track ID, subject ID, submission timestamp, and individual results.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: Students can start a practice session and receive their first batch of questions in under 200ms (p95), regardless of the number of concurrent users.
- **SC-002**: Students receive submission results (accuracy stats) in under 100ms (p95).
- **SC-003**: Students receive the next batch of questions (continue) in under 150ms (p95).
- **SC-004**: The system supports 10,000 or more concurrent active sessions without performance degradation.
- **SC-005**: Zero database queries occur during active gameplay (session start with warm cache, submit, continue).
- **SC-006**: Content changes (question add/edit/delete) are reflected in new sessions within 60 seconds.
- **SC-007**: No question repeats within a session until all in-scope questions have been served.
- **SC-008**: Duplicate submissions do not inflate attempt or correct counts (idempotent processing).
- **SC-009**: Active sessions are unaffected by temporary database or application server outages.
- **SC-010**: Maximum data loss on cache infrastructure failure is limited to one session (up to 20 unanswered questions).
- **SC-011**: Queued results are never lost — all submitted answers are eventually persisted to the database.
- **SC-012**: Abandoned sessions are automatically cleaned up within 1 hour of inactivity.
- **SC-013**: Each player's summary record remains under 500 KB for tracks with up to 5,000 questions.

## Assumptions

- **A-001**: The content hierarchy (Subject > Track > Unit > Topic > Lesson) remains unchanged.
- **A-002**: The existing practice log table schema remains unchanged; it continues to serve as the historical record for reporting.
- **A-003**: The client application handles access control filtering (which tracks are unlocked) — the server does not enforce access control.
- **A-004**: Answer verification is client-side (self-assessment); the server does not verify correctness.
- **A-005**: The client renders questions locally after fetching content from CDN; the server never reads or returns question content.
- **A-006**: A suitable CDN provider and queue system will be selected during planning (these are open infrastructure decisions).
- **A-007**: The hierarchy endpoint remains unchanged from V1 and is not part of the gameplay hot path.
- **A-008**: Migration from V1 includes a one-time backfill of existing practice log data into the new player summary structure.
- **A-009**: V1 and V2 can coexist during gradual rollout via feature flagging.

## Constraints

- **C-001**: The existing `tabMemora Practice Log` table schema MUST NOT be modified.
- **C-002**: The question format and stage types (QUESTION, FILL_BLANK, MATCHING, INFORMATION) MUST NOT change.
- **C-003**: The content hierarchy (Subject > Track > Unit > Topic > Lesson) MUST NOT be modified.
- **C-004**: Client-side UI/UX changes are out of scope for this feature; the client adapts to the new backend API.

## Dependencies

- **D-001**: CDN infrastructure must be provisioned and configured for content hosting and cache invalidation.
- **D-002**: A message queue system must be selected and deployed for background write processing.
- **D-003**: The client application must be updated to fetch content from CDN and call V2 endpoints (separate frontend work).
- **D-004**: Existing practice log data must be backfilled into the new player summary structure before V2 can serve returning players accurately.
