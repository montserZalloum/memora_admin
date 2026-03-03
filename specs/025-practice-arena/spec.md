# Feature Specification: Practice Arena (ساحة التدريب)

**Feature Branch**: `025-practice-arena`
**Created**: 2026-02-23
**Updated**: 2026-03-02
**Status**: Final
**Dependencies**: None (introduces new tables; uses existing tables read-only)

---

## Problem Statement

Students currently have no way to freely practice content outside the daily FSRS review system. The daily review is algorithm-driven and mandatory — students cannot choose what to practice or when. A separate, student-initiated practice mode is needed where students can select specific content (by track, unit, or topic) and answer multiple-choice questions without affecting their FSRS state, streaks, leaderboards, or XP.

Additionally, review items (the atomic units of reviewable content) are currently embedded inside `config_json` on each lesson stage record. There is no standalone, queryable table of items. This makes it impossible to efficiently serve questions for practice sessions. A new Review Item table is needed to extract and flatten these items into a searchable format.

### Key Distinction: Practice vs. Daily Reviews

The Practice Arena is completely separate from the FSRS daily review system:

| Aspect              | Practice Arena                     | Daily Reviews                      |
| ------------------- | ---------------------------------- | ---------------------------------- |
| Initiation          | Student-initiated                  | Algorithm-driven (FSRS)            |
| Obligation          | Optional                           | Mandatory                          |
| Rewards             | No XP, no streak, no leaderboard   | Awards XP, affects streak          |
| Storage             | Practice Log                       | Memory State                       |
| Question source     | Review Item table                  | Memory State                       |
| Connection          | Zero connection between them       | Zero connection between them       |

---

## User Scenarios & Testing

### User Story 1 — Review Item Extraction (Priority: P0)

As a system, when a teacher saves or modifies a lesson, I need to extract all reviewable items from the lesson's stages and populate the Review Item table, so that items are available for practice sessions and future features.

**Why P0**: Without this table populated, the Practice Arena has no questions to serve. This is the foundational data layer.

**Independent Test**: Can be fully tested by saving/modifying lessons in the admin panel and verifying that the Review Item table is correctly populated with extracted items, questions, and hierarchy data.

**Acceptance Scenarios**:

1. **Given** a teacher saves a lesson with 3 non-skippable stages containing 8 total items, **When** the background sync job runs, **Then** 8 rows exist in the Review Item table with correct hierarchy fields, question text, and choices.
2. **Given** a teacher modifies a lesson and changes one item's content, **When** the sync job runs, **Then** the corresponding Review Item row is updated with the new content. Unchanged items are not touched.
3. **Given** a teacher deletes an item from a stage, **When** the sync job runs, **Then** the corresponding Review Item row is hard-deleted, AND any related Practice Log rows for that item are also deleted (cascade).
4. **Given** a teacher adds a new item to an existing stage, **When** the sync job runs, **Then** a new Review Item row is created with the correct item_id and full hierarchy.
5. **Given** a lesson has `is_reviewable = false`, **When** the sync job runs, **Then** no items from that lesson appear in the Review Item table. If items previously existed, they are deleted.
6. **Given** a stage has `is_skippable = true` (per-stage override or global setting), **When** the sync job runs, **Then** no items from that stage appear in the Review Item table.
7. **Given** a teacher changes a stage type (e.g., MATCHING to QUESTION), **When** the sync job runs, **Then** old items are deleted and new items are created with new item_id values (because changing the stage type changes the item_ids).
8. **Given** a teacher saves the same lesson 10 times within 2 minutes, **When** the sync job runs, **Then** the lesson is processed only once (dedup via content_hash comparison).
9. **Given** a stage with null or invalid config, **When** the sync job runs, **Then** the stage is silently skipped with a warning log. Other stages in the same lesson are processed normally.
10. **Given** a lesson with all stages being skippable, **When** the sync job runs, **Then** no items are generated. If items previously existed for this lesson, they are deleted.

---

### User Story 2 — Hierarchy Selection (Priority: P1)

As a student, I want to choose what to practice by navigating through subject, track, unit, and topic, so I can focus on specific content areas.

**Why P1**: This is the entry point to the Practice Arena. Without it, students cannot start a session.

**Independent Test**: Can be fully tested by calling the hierarchy endpoint and verifying that all tracks/units/topics are returned with correct access flags and item counts.

**Acceptance Scenarios**:

1. **Given** a student opens the Practice Arena and selects a subject, **When** the hierarchy is loaded, **Then** ALL tracks/units/topics are returned (including locked ones), with an `accessible` flag on each level indicating whether the student has access. The frontend uses this flag to visually distinguish locked vs. unlocked content (e.g., lock icon to encourage purchase).
2. **Given** a student selects "Completed only" filter, **When** the hierarchy is loaded, **Then** only tracks/units/topics where the student has completed at least one lesson are shown. Items with zero completed lessons are hidden entirely (not shown as disabled).
3. **Given** a student selects "All content" filter, **When** the hierarchy is loaded, **Then** all tracks/units/topics are shown, including unstarted content.
4. **Given** a student selects 3 tracks, **When** the UI updates, **Then** unit/topic selection is disabled (multi-selection at one level prevents drilling deeper).
5. **Given** a student selects exactly 1 track, **When** the UI updates, **Then** unit selection becomes available for that track.
6. **Given** a student selects exactly 1 unit, **When** the UI updates, **Then** topic selection becomes available for that unit.
7. **Given** a student selects 2 units, **When** the UI updates, **Then** topic selection is disabled, practice spans both units.
8. **Given** a student with no completed lessons selects "Completed only" filter, **Then** empty hierarchy is returned — nothing to practice.

---

### User Story 3 — Practice Session Flow (Priority: P1)

As a student, I want to start a practice session, answer questions, see my results, and optionally continue with more questions using the same filters.

**Why P1**: This is the core user-facing functionality of the Practice Arena.

**Independent Test**: Can be fully tested by starting a session, answering a batch of questions, submitting results, and requesting additional batches — verifying correct question selection, result tracking, and session continuity.

**Acceptance Scenarios**:

1. **Given** a student starts a session with valid filters, **When** the backend processes the request, **Then** it returns up to `practice_session_size` questions (default: 20) with proportional distribution across topics based on content volume.
2. **Given** a student's selected filters match only 7 items but `practice_session_size` is 20, **When** the session starts, **Then** only 7 questions are returned. No padding with items from outside the selected filters — the filters must be respected strictly.
3. **Given** a student has never practiced before, **When** questions are selected, **Then** all questions are unseen items (first priority).
4. **Given** a student has seen all items matching the filters, **When** a new batch is requested, **Then** the response includes `all_seen_warning: true` and questions are ordered by oldest-seen first.
5. **Given** a batch contains ANY repeat questions (even one), **When** the response is returned, **Then** `all_seen_warning: true` is set. This flag is checked on every batch, not just the first time.
6. **Given** a student completes a batch and requests more, **When** the next batch is served, **Then** priority order is: (1) never seen, (2) seen in previous sessions oldest first, (3) seen in current session oldest first.
7. **Given** a student completes a batch, **When** results are submitted, **Then** Practice Log is updated immediately (not deferred) so data survives if the student leaves.
8. **Given** a student exits mid-batch (closes app/browser), **When** the session expires, **Then** nothing is saved for the incomplete batch. Previously completed batches within the session remain saved (they were saved immediately upon completion).
9. **Given** a student submits the same batch results twice (network retry), **When** the backend processes the duplicate, **Then** the second submission is ignored and the cached response is returned. Idempotency enforced via batch sequence number.
10. **Given** a student starts a new session while having an active one, **When** the new session is created, **Then** the old session is auto-expired (one active session per student).

---

### User Story 4 — Access Control Enforcement (Priority: P1)

As the system, I need to ensure students can only practice content they have access to, even if the frontend sends invalid requests.

**Why P1**: Security boundary — students must not access paid content they haven't subscribed to.

**Independent Test**: Can be fully tested by attempting to start sessions with various access levels and verifying correct accept/reject behavior.

**Acceptance Scenarios**:

1. **Given** a student starts a session with a track they are NOT subscribed to and is NOT free, **When** the backend validates the request, **Then** the request is rejected with a 403 error.
2. **Given** a student's subscription expires during an active session, **When** they request the next batch (continue), **Then** the session continues normally. Access is checked only once at session start, not on subsequent batch requests.
3. **Given** a student has full subject access, **When** they select any track within that subject, **Then** access is granted for all tracks.
4. **Given** a student has single-track access, **When** they select a different track in the same subject, **Then** access is denied for the unsubscribed track.
5. **Given** a unit or topic is marked as free, **When** a student without any subscription selects it, **Then** access is granted. Free content is practicable without a subscription, consistent with the existing lesson access model.
6. **Given** a subject is in the plan's free subjects set, **When** a student selects tracks in that subject, **Then** access is granted.

---

### Edge Cases

| Scenario | Behavior |
| -------- | -------- |
| Lesson with no reviewable stages | Silently skipped — no items generated |
| Stage with null or invalid config | Silently skipped with warning log |
| Teacher updates content while student is mid-session | Student sees the version loaded at session start. Updated content appears in the next session |
| Item deleted while student has it in active session | When results are submitted, the deleted item is silently skipped. Other results saved normally |
| Teacher deletes an item | Hard delete from Review Item + cascade delete from Practice Log |
| All items in selected filters are from skippable stages | Empty result — session cannot start, return error NO_ITEMS |
| Session store goes down during a session | Session state lost. Student must start a new session. Previously saved batches are safe |
| Student with no completed lessons + "Completed only" filter | Empty hierarchy returned |
| Available items < session_size | Return only available items, no padding from outside filters |
| Student starts new session with one already active | Old session auto-expired, new one created |
| Duplicate batch submission (network retry) | Ignored via batch_seq idempotency, cached response returned |
| `is_reviewable = false` on lesson | No items extracted for practice, same as daily reviews |

---

## Requirements

### Functional Requirements

- **FR-001**: System MUST extract reviewable items from lesson stages into a flat, queryable table when lesson content changes.
- **FR-002**: System MUST detect content changes via content_hash to avoid reprocessing unchanged lessons.
- **FR-003**: System MUST skip non-reviewable lessons (`is_reviewable = false`) and skippable stages (`is_skippable = true`) during extraction.
- **FR-004**: System MUST cascade-delete Practice Log entries when a Review Item is deleted.
- **FR-005**: System MUST generate placeholder questions during extraction (QUESTION stages copy original MCQ content; MATCHING stages generate "what is" format; other types store structured content data).
- **FR-006**: System MUST allow students to browse the full content hierarchy (subject > track > unit > topic) with an `accessible` flag on each level and item counts per level.
- **FR-007**: System MUST support two content filters: "Completed only" (lessons the student has passed) and "All content" (all accessible content including unstarted).
- **FR-008**: System MUST enforce access control at session start — rejecting requests for paid tracks the student is not subscribed to. Free topics/units within premium subjects are accessible without subscription, consistent with the existing access model. Plan-level free subjects are also accessible.
- **FR-009**: System MUST NOT re-check access on subsequent batch requests within an active session.
- **FR-010**: System MUST return up to `practice_session_size` questions per batch, distributed proportionally across topics by content volume.
- **FR-011**: System MUST prioritize unseen items first, then oldest-seen items from previous sessions, then items already seen in the current session.
- **FR-012**: System MUST track per-student per-item practice history: first seen, last seen, last result, attempt count, and correct count.
- **FR-013**: System MUST support idempotent batch submission via batch sequence numbers to prevent double-counting on network retries.
- **FR-014**: System MUST auto-expire abandoned sessions (default 1 hour TTL). Nothing is saved for incomplete batches.
- **FR-015**: System MUST NOT affect FSRS state, streaks, leaderboards, or XP.
- **FR-016**: System MUST debounce rapid lesson saves — processing each lesson only once per sync cycle regardless of how many times it was saved.
- **FR-017**: System MUST provide configurable settings: practice_session_size (default 20) and practice_session_ttl (default 3600s).
- **FR-018**: System MUST enforce one active session per student — starting a new session auto-expires any existing session.
- **FR-019**: System MUST return response metadata with each batch: total_in_batch, total_available, has_more, unseen_remaining, and all_seen_warning.
- **FR-020**: System MUST validate session start requests: tracks must be non-empty; multi-track selection disables unit/topic drill-down; multi-unit selection disables topic drill-down.
- **FR-021**: System MUST enforce that previous batch is submitted before allowing next batch (continue) request.
- **FR-022**: System MUST silently skip deleted items when processing batch results — other results in the same batch are saved normally.

### Key Entities

- **Item (آيتم)**: The smallest reviewable content unit inside a stage. Each item has a UUID (`item_id`) generated when the teacher saves content. Examples: a MATCHING stage with 5 pairs = 5 items; a QUESTION stage with one correct answer = 1 item; a FILL_BLANK stage = 1 item per blank; a MINDMAP stage = 1 item per node.

- **Review Item**: A flat, queryable record that extracts an item from stage config and stores it with its full denormalized hierarchy (subject, track, unit, topic, lesson, stage). For MCQ stages: stores individual choice fields and a 1-based correct_choice index. For non-MCQ stages (FILL_BLANK, MATCHING, MINDMAP, SENTENCE_BUILDER): stores structured content data. Estimated ~200,000 rows.

- **Practice Log**: One row per student per item, updated on each encounter. High-volume write-heavy table (~500 million rows at scale). Key attributes: player reference, item reference (UUID), first_seen_at, last_seen_at, last_result (Correct/Incorrect), attempt_count, correct_count. Practice history is timeless — does not reset with seasons.

- **Practice Session**: A short-lived session with native TTL expiry. Holds the student's filter selections, tracks which questions have been served, batch sequence numbers, and idempotency markers. One session per student keyed by player_id. Auto-expires after TTL. If lost, student simply starts a new session.

### Item Extraction Per Stage Type

| Stage Type       | Item Location                           | Items Per Stage | Storage Target                |
| ---------------- | --------------------------------------- | --------------- | ----------------------------- |
| QUESTION         | answers[].item_id (correct answer only) | 1               | choice fields + correct index |
| MATCHING         | pairs[].item_id                         | 1 per pair      | structured content data       |
| FILL_BLANK       | blanks[].item_id                        | 1 per blank     | structured content data       |
| SENTENCE_BUILDER | words[].item_id or root item_id         | 1               | structured content data       |
| MINDMAP          | children[].item_id (recursive)          | 1 per node      | structured content data       |
| INFORMATION      | Skippable (no items)                    | 0               | N/A                           |
| REVEAL           | Skippable (no items)                    | 0               | N/A                           |

### Question Selection Algorithm

1. Filter Review Items by subject + tracks + units + topics + accessible lessons
2. Left-join with Practice Log for the current student
3. Assign priority: 0 = never seen, 1 = seen before (not this session), 2 = seen this session
4. Order by priority ascending, then oldest-seen first (nulls first for unseen)
5. When multiple topics selected, distribute proportionally by content volume per topic
6. Limit each topic to its allocated quota; merge results across topics
7. Set `all_seen_warning = true` if any returned question has priority > 0

### Proportional Distribution

When multiple topics are selected, questions are distributed proportionally to content volume:
- Count items per topic after all filters are applied
- Allocate per-topic quotas: `quota = round(topic_count / total_items * batch_size)`
- Adjust remainder by adding/removing from the largest topic
- Example: Topic A (100 items) + Topic B (10 items) + batch_size 20 = ~18 from A, ~2 from B

### Completion Filter Logic

- **"Completed only"**: Load student's passed_lessons_bitset, decode to lesson IDs via bit_index mapping, filter Review Items to only those lessons. Build hierarchy from filtered items — only show levels with at least one matching item.
- **"All content"**: Return all Review Items matching student's accessible content. Full hierarchy shown with `accessible` flag.

### Access Control

Access is determined by checking the student's access grants:

| Access Type      | Grants                                                |
| ---------------- | ----------------------------------------------------- |
| Subject-level    | All tracks in that subject                            |
| Track-level      | Only that specific track                              |
| Free content     | Unit or topic marked free, no subscription needed     |
| Plan-level free  | Full subject access, no subscription needed           |

The `accessible` flag cascades downward: track access flows to its units; unit access flows to its topics; free content overrides upward (a free topic is accessible even if its parent track is not).

### Session Lifecycle

1. **Start**: Delete any existing session for this player > validate access (once) > resolve accessible lessons > create session > serve first batch
2. **Submit**: Validate batch_seq for idempotency > save results to Practice Log immediately > return summary
3. **Continue**: Verify previous batch was submitted > extend served items list > serve next batch with priority ordering > increment batch_seq
4. **Abandon**: TTL expires > session auto-deleted > nothing saved for incomplete batch

### API Endpoints

| Endpoint              | Method | Purpose                                          |
| --------------------- | ------ | ------------------------------------------------ |
| `/practice/hierarchy` | GET    | Browse content hierarchy with access flags       |
| `/practice/start`     | POST   | Start session, validate access, return 1st batch |
| `/practice/submit`    | POST   | Submit batch results (idempotent)                |
| `/practice/continue`  | POST   | Request next batch in same session               |

### Rate Limiting

| Endpoint                | Limit  | Window |
| ----------------------- | ------ | ------ |
| GET /practice/hierarchy | 30/min | 60s    |
| POST /practice/start    | 10/min | 60s    |
| POST /practice/submit   | 30/min | 60s    |
| POST /practice/continue | 30/min | 60s    |

---

## Assumptions

- **A-001**: The existing content_hash field on lessons is sufficient to detect content changes for the sync job.
- **A-002**: The existing access control infrastructure works for practice arena access checks without modification.
- **A-003**: The existing completion bitset mechanism is sufficient for the "Completed only" filter.
- **A-004**: Placeholder questions are acceptable for the initial release. AI-generated question variants are a future enhancement.
- **A-005**: The sync job running on a short interval provides acceptable freshness for teacher content changes.
- **A-006**: 500 million Practice Log rows can be served efficiently with proper indexing, without requiring table partitioning.
- **A-007**: The practice session TTL of 1 hour is sufficient for typical student sessions.
- **A-008**: Multi-track selection disables deeper drilling (unit/topic selection) — deliberate UX constraint.
- **A-009**: One session per student simplifies implementation and avoids confusion; starting a new session auto-expires the previous one.

---

## Relationship to Existing System

The Practice Arena interacts with (read-only):
- **Player Subscription** — determines accessible tracks (subject-level and track-level grants)
- **Structure Progress** — determines completed lessons for the "Completed only" filter (via passed_lessons_bitset)
- **Lesson Stages** — source data for Review Item extraction (config_json)
- **Plan Subjects** — determines plan-level free subjects
- **Hierarchy** — provides content structure for filtering

The Practice Arena does NOT affect:
- FSRS / Memory State (daily reviews only)
- Streak system
- Leaderboard
- XP / Wallet

---

## Out of Scope

- Offline practice mode (online only)
- XP or rewards for practice sessions
- Impact on streaks or leaderboards
- Integration with FSRS / Memory State (zero connection)
- AI-generated question variants (future phase — placeholders used for now)
- Frontend UI/UX design details (frontend team handles this)
- Real-time multiplayer practice modes
- Multiple simultaneous sessions per student

---

## Implementation Phases

### Phase 1: Review Item Table & Sync Job
- Create Review Item table with choice fields, correct index, content data, and full hierarchy fields
- Implement background sync job using dirty-set pattern
- On-save hook marks lessons as dirty; on-trash hook immediately deletes Review Items with cascade
- Scheduled job processes dirty set, extracts items per stage type, handles insert/update/delete with content_hash dedup
- On sync failure: lesson stays in dirty set for auto-retry
- **Deliverable**: Table populated with all existing reviewable items

### Phase 2: Practice Log Table & Session Infrastructure
- Create Practice Log table (high-volume raw table, not standard DocType)
- Implement session create/read/expire with TTL
- Implement batch_seq idempotency logic
- **Deliverable**: Storage and session infrastructure ready

### Phase 3: Core APIs
- Hierarchy API with `accessible` flag on all levels, completion filter, item counts
- Start session API with access validation, proportional topic distribution, question selection
- Submit results API with Practice Log upsert, idempotency, deleted item handling
- Continue session API with dedup, priority ordering, all_seen_warning
- Rate limiting on all endpoints
- **Deliverable**: Full API suite functional

### Phase 4: Integration & Testing
- End-to-end testing with realistic data volumes
- Load testing with concurrent sessions (target: 100K concurrent students)
- Edge case testing (deleted items, expired sessions, network retries, free content access)
- **Deliverable**: Production-ready feature

---

## Success Criteria

### Measurable Outcomes

- **SC-001**: A student can start a practice session, answer questions, and see results within 2 seconds of each action (P95 latency).
- **SC-002**: The Review Item sync job processes 100 changed lessons in under 30 seconds.
- **SC-003**: Question selection returns results in under 100ms for a student with 5,000 practice log entries.
- **SC-004**: 100K concurrent students can use the Practice Arena without degradation in daily review system performance.
- **SC-005**: Duplicate batch submissions (network retries) do not corrupt practice log data — verified by test.
- **SC-006**: Students cannot access paid content they are not subscribed to — verified by backend enforcement.
- **SC-007**: Free content is accessible for practice without a subscription — verified by test.

---

## Decisions Log

| # | Decision | Rationale |
| --- | --- | --- |
| D-001 | One item = one question (for now) | Simplicity. Future multi-variant support possible. |
| D-002 | Questions pre-generated (placeholders for now) | Decouples question generation from practice feature. |
| D-003 | Sync via dirty-set + scheduled job, not on-save | Dedup: 10 saves in 1 minute = 1 processing. On failure, auto-retry. |
| D-004 | Hard delete items (not soft delete) | Deleted items have no future value. Cascade to Practice Log. |
| D-005 | Full hierarchy denormalization in Review Item | Performance: avoids 3-4 JOINs per query with 100K concurrent users. |
| D-006 | Practice Log not tied to seasons | Practice history is timeless — "have you seen this?" doesn't reset. |
| D-007 | Per-topic selection queries for distribution | Avoids complex SQL window functions. Each topic query is fast. |
| D-008 | Proportional distribution across topics | Fair representation of content volume. |
| D-009 | Save results per batch, not per session | Prevents data loss if student leaves after batch 2 of 5. |
| D-010 | Ephemeral session storage (not persistent DB) | Native TTL, sub-ms latency, existing pattern. |
| D-011 | Raw table for Practice Log (not standard DocType) | 500M rows with frequent UPSERTs — ORM overhead prohibitive. |
| D-012 | One active session per student | Simpler UX and implementation, avoids confusion. |
| D-013 | Free content practicable without subscription | Consistent with existing lesson access model. |
| D-014 | Access checked once at session start | Simplicity, no mid-session disruption. |
| D-015 | Individual choice fields (not JSON array) | Allows admin panel display, searching, and direct filtering. |
| D-016 | Correct_choice is 1-based (1-4) | Matches existing validation patterns. |
| D-017 | all_seen_warning on every batch with repeats | Student always informed, not just first time. |
| D-018 | No session_id in API — player-keyed sessions | One session per player; player identified via JWT. |
| D-019 | Practice Log keys on item UUID | Stable canonical identifier. Survives table rebuilds. |
| D-020 | has_more derived from per-topic counts | Zero additional query cost — counts already computed for distribution. |
| D-021 | unseen_remaining via single COUNT per batch | One query (~3-5ms). Useful UX signal. |

---

## Clarifications

### Session 2026-02-23

- Q: Where should Practice Sessions be stored? A: Ephemeral session store with TTL (fast, native expiry)
- Q: What storage strategy should Practice Log use? A: Raw table with direct queries (like Memory State — no standard DocType overhead)
- Q: Can a student have multiple concurrent practice sessions? A: No — one session at a time per student; starting a new session auto-expires the previous one
- Q: Can students practice free content without a subscription? A: Yes — free topics/units are practicable without subscription, consistent with existing access model

### Session 2026-03-02

- Q: Should locked content be hidden from hierarchy? A: No — show ALL content with `accessible` flag. Frontend shows lock icons for upsell.
- Q: Should access be re-checked on each batch request? A: No — check once at session start only (D-014).
- Q: How should multi-level selection work? A: Multi-selection at one level disables deeper drill-down (e.g., 3 tracks selected = no unit/topic selection).
- Q: Should continue require previous batch to be submitted? A: Yes — prevents skipping batches and ensures Practice Log integrity.
