# Feature Specification: Practice Arena (ساحة التدريب)

**Feature Branch**: `025-practice-arena`
**Created**: 2026-02-23
**Status**: Draft
**Dependencies**: Phase 024 (Review Item DocType + sync infrastructure already implemented)

---

## Problem Statement

Students currently have no way to freely practice content outside the daily FSRS review system. The daily review is algorithm-driven and mandatory — students cannot choose what to practice or when. A separate, student-initiated practice mode is needed where students can select specific content (by track, unit, or topic) and answer multiple-choice questions without affecting their FSRS state, streaks, leaderboards, or XP.

Additionally, review items (the atomic units of reviewable content) are currently embedded inside `config_json` on each lesson stage record. There is no standalone, queryable table of items. This makes it impossible to efficiently serve questions for practice sessions. A new Review Item table is needed to extract and flatten these items into a searchable format.

---

## User Scenarios & Testing

### User Story 1 — Review Item Extraction (Priority: P0)

> **Note**: The `Memora Review Item` DocType and core sync infrastructure (extraction for QUESTION, FILL_BLANK, MATCHING, generic stages + on_save/on_trash hooks + Memory State cascade cleanup) were implemented in phase 024. This user story covers **gap-filling** only: Practice Log cascade deletion (FR-004), `is_reviewable` lesson filtering, SENTENCE_BUILDER/MINDMAP-specific extraction (currently handled by generic fallback), and content_hash debounce.

As a system, when a teacher saves or modifies a lesson, I need to extract all reviewable items from the lesson's stages and populate the Review Item table, so that items are available for practice sessions and future features.

**Why P0**: Without this table populated, the Practice Arena has no questions to serve. This is the foundational data layer.

**Independent Test**: Can be fully tested by saving/modifying lessons in the admin panel and verifying that the Review Item table is correctly populated with extracted items, questions, and hierarchy data.

**Acceptance Scenarios**:

1. **Given** a teacher saves a lesson with 3 non-skippable stages containing 8 total items, **When** the background sync job runs, **Then** 8 rows exist in the Review Item table with correct hierarchy fields, question text, and choices.
2. **Given** a teacher modifies a lesson and changes one item's content, **When** the sync job runs, **Then** the corresponding Review Item row is updated with the new content. Unchanged items are not touched.
3. **Given** a teacher deletes an item from a stage, **When** the sync job runs, **Then** the corresponding Review Item row is deleted, AND any related Practice Log rows for that item are also deleted.
4. **Given** a teacher adds a new item to an existing stage, **When** the sync job runs, **Then** a new Review Item row is created with the correct item_id and hierarchy.
5. **Given** a lesson has `is_reviewable = false`, **When** the sync job runs, **Then** no items from that lesson appear in the Review Item table.
6. **Given** a stage has `is_skippable = true` (per-stage override or global setting), **When** the sync job runs, **Then** no items from that stage appear in the Review Item table.
7. **Given** a teacher changes a stage type (e.g., MATCHING to QUESTION), **When** the sync job runs, **Then** old items are deleted and new items are created with new item_id values.
8. **Given** a teacher saves the same lesson 10 times within 2 minutes, **When** the sync job runs, **Then** the lesson is processed only once (debounce/dedup via content_hash).

---

### User Story 2 — Hierarchy Selection (Priority: P1)

As a student, I want to choose what to practice by navigating through subject, track, unit, and topic, so I can focus on specific content areas.

**Why P1**: This is the entry point to the Practice Arena. Without it, students cannot start a session.

**Independent Test**: Can be fully tested by calling the hierarchy endpoint and verifying that all tracks/units/topics are returned with correct access flags and item counts.

**Acceptance Scenarios**:

1. **Given** a student opens the Practice Arena and selects a subject, **When** the hierarchy is loaded, **Then** all tracks/units/topics are returned, with a flag indicating which ones the student has access to (via subscription).
2. **Given** a student selects "Completed only" filter, **When** the hierarchy is loaded, **Then** only tracks/units/topics where the student has completed at least one lesson are shown. Tracks/units/topics with zero completed lessons are hidden entirely.
3. **Given** a student selects "All content" filter, **When** the hierarchy is loaded, **Then** all tracks/units/topics the student has access to are shown, including unstarted content.
4. **Given** a student selects 3 tracks, **When** the UI updates, **Then** unit/topic selection is disabled (multi-selection at one level prevents drilling deeper).
5. **Given** a student selects exactly 1 track, **When** the UI updates, **Then** unit selection becomes available for that track.

---

### User Story 3 — Practice Session Flow (Priority: P1)

As a student, I want to start a practice session, answer questions, see my results, and optionally continue with more questions using the same filters.

**Why P1**: This is the core user-facing functionality of the Practice Arena.

**Independent Test**: Can be fully tested by starting a session, answering a batch of questions, submitting results, and requesting additional batches — verifying correct question selection, result tracking, and session continuity.

**Acceptance Scenarios**:

1. **Given** a student starts a session with valid filters, **When** the backend processes the request, **Then** it returns up to `practice_session_size` questions with proportional distribution across topics based on content volume.
2. **Given** a student's selected filters match only 7 items but `practice_session_size` is 20, **When** the session starts, **Then** only 7 questions are returned (no padding with items from outside the filters).
3. **Given** a student has never practiced before, **When** questions are selected, **Then** all questions are unseen items (highest priority).
4. **Given** a student has seen all items matching the filters, **When** a new batch is requested, **Then** the response includes `all_seen_warning: true` and questions are ordered by oldest-seen first.
5. **Given** a student completes a batch and requests more, **When** the next batch is served, **Then** questions from the current session have lowest priority, questions from previous sessions have second-lowest priority, and unseen questions have highest priority.
6. **Given** a student completes a batch, **When** results are submitted, **Then** Practice Log is updated immediately (not deferred).
7. **Given** a student exits mid-batch (closes app/browser), **When** the session expires, **Then** nothing is saved for the incomplete batch. Previously completed batches within the session remain saved.
8. **Given** a student submits the same batch results twice (network retry), **When** the backend processes the duplicate, **Then** the second submission is ignored (idempotent via batch sequence number).

---

### User Story 4 — Access Control Enforcement (Priority: P1)

As the system, I need to ensure students can only practice content they have access to, even if the frontend sends invalid requests.

**Why P1**: Security boundary — students must not access paid content they haven't subscribed to.

**Independent Test**: Can be fully tested by attempting to start sessions with various access levels and verifying correct accept/reject behavior.

**Acceptance Scenarios**:

1. **Given** a student starts a session with a paid track they are NOT subscribed to, **When** the backend validates the request, **Then** the request is rejected with an appropriate error. Free topics/units within that track remain accessible.
2. **Given** a student's subscription expires during an active session, **When** they request the next batch (continue), **Then** the session continues (access checked only at session start).
3. **Given** a student has full subject access, **When** they select any track within that subject, **Then** access is granted.
4. **Given** a student has single-track access, **When** they select a different track in the same subject, **Then** access is denied for that track.

---

### Edge Cases

- **Lesson with no reviewable stages**: Silently skipped — no items generated in Review Item table.
- **Stage with null or invalid config**: Silently skipped with a warning log.
- **Teacher updates content while student is mid-session**: Student sees the version of questions that was loaded at session start. Updated content appears in the next session.
- **Item deleted while student has it in an active session**: When results are submitted, the deleted item is silently skipped during save. Other results are saved normally.
- **Student with no completed lessons selects "Completed only"**: Empty hierarchy returned — no content to practice.
- **All items in selected filters are from skippable stages**: Empty result — session cannot start.
- **Session store goes down during a session**: Session state is lost. Student must start a new session. Completed batches already saved to persistent storage are safe.

---

## Requirements

### Functional Requirements

- **FR-001**: System MUST extract reviewable items from lesson stages into a flat, queryable table when lesson content changes.
- **FR-002**: System MUST detect content changes via content_hash to avoid reprocessing unchanged lessons.
- **FR-003**: System MUST skip non-reviewable lessons (`is_reviewable = false`) and skippable stages (`is_skippable = true`) during extraction.
- **FR-004**: System MUST cascade-delete Practice Log entries when a Review Item is deleted.
- **FR-005**: System MUST generate placeholder multiple-choice questions during extraction (QUESTION stages copy original content; MATCHING stages generate "ما هو:" format; other types use generic format).
- **FR-006**: System MUST allow students to browse content hierarchy (subject > track > unit > topic) with access flags and item counts.
- **FR-007**: System MUST support two content filters: "Completed only" (lessons the student has passed) and "All content" (everything accessible).
- **FR-008**: System MUST enforce access control at session start — rejecting requests for paid tracks the student is not subscribed to. Free topics/units within premium subjects are accessible without subscription, consistent with the existing access model.
- **FR-009**: System MUST NOT re-check access on subsequent batch requests within an active session.
- **FR-010**: System MUST return up to `practice_session_size` questions per batch, distributed proportionally across topics by content volume (i.e., count of Review Items per topic).
- **FR-011**: System MUST prioritize unseen items first, then oldest-seen items from previous sessions, then items already seen in the current session.
- **FR-012**: System MUST track per-student per-item practice history: first seen, last seen, last result, attempt count, and correct count.
- **FR-013**: System MUST support idempotent batch submission via batch sequence numbers to prevent double-counting on network retries.
- **FR-014**: System MUST auto-expire abandoned sessions (default 1 hour TTL). Nothing is saved for incomplete batches.
- **FR-015**: System MUST NOT affect FSRS state, streaks, leaderboards, or XP.
- **FR-016**: System MUST debounce rapid lesson saves — processing each lesson only once per sync cycle regardless of how many times it was saved.
- **FR-017**: System MUST provide configurable settings: practice_session_size (default 20), practice_session_ttl (default 3600s). Note: review_item_sync_interval is not needed — sync runs synchronously on lesson save via hook, not on a timer (see research R-008).

### Key Entities

- **Item (آيتم)**: The smallest reviewable content unit inside a stage. Each item has a UUID (`item_id`) generated when the teacher saves content. Example: a MATCHING stage with 5 pairs = 5 items, each with its own `item_id`. A QUESTION stage with one correct answer = 1 item.

- **Review Item** (EXISTS — from phase 024): A flat, queryable record that extracts an item from stage config and stores it with its full hierarchy (subject, track, unit, topic, lesson, stage) plus a pre-generated multiple-choice question and choices. Key attributes: item_id (unique UUID), lesson reference, stage_id, stage_type, hierarchy links (subject/track/unit/topic), question_text, choices (2-4 options), correct_choice index. Estimated ~200,000 rows.

- **Practice Log** (NEW, raw SQL table): One row per student per item, updated on each encounter. Stored as a raw SQL table (not a Frappe DocType) with direct queries — following the Memory State precedent for high-volume tables. Key attributes: player reference, review item reference, first_seen_at, last_seen_at, last_result (Correct/Incorrect), attempt_count, correct_count. Composite unique key: (player_id, item_id). Estimated ~500 million rows at scale.

- **Practice Session** (TEMPORARY, Redis): A short-lived session stored as a Redis hash with native TTL expiry. Holds the student's filter selections and tracks which questions have been served. Key attributes: player_id, subject_id, selected tracks/units/topics, content filter, current batch sequence number, list of served item_ids. Auto-expires after TTL. No MariaDB persistence — lost sessions simply require the student to start a new session.

### Item Extraction Per Stage Type

Each stage type stores items differently:

| Stage Type        | Item Location                           | Items Per Stage |
| ----------------- | --------------------------------------- | --------------- |
| QUESTION          | answers[].item_id (correct answer only) | 1               |
| MATCHING          | pairs[].item_id                         | 1 per pair      |
| FILL_BLANK        | item_id (root level or per blank)       | varies          |
| SENTENCE_BUILDER  | item_id (root level)                    | 1               |
| MINDMAP           | children[].item_id (recursive)          | 1 per node      |
| INFORMATION       | Skippable (no items)                    | 0               |
| REVEAL            | Skippable (no items)                    | 0               |

### Question Selection Algorithm

1. Filter Review Items by subject + tracks + units + topics + lesson completion + reviewability
2. Join with Practice Log for the current student
3. Assign priority: 0 = never seen, 1 = seen before (not this session), 2 = seen this session
4. Order by priority ascending, then oldest-seen first
5. When multiple topics selected, distribute proportionally by content volume per topic
6. Limit to `practice_session_size`

### Completion Filter Logic

- **"Completed only"**: Load student's passed_lessons_bitset, decode to lesson IDs, filter Review Items to only those lessons. Build hierarchy from filtered items.
- **"All content"**: Return all Review Items matching student's accessible tracks. Full hierarchy shown.

### Session Lifecycle

1. **Start**: Validate access > delete any existing session for this player > create temporary session > return first batch
2. **Continue**: Load session > serve next batch with dedup > increment batch_seq
3. **Submit**: Validate batch_seq for idempotency > save to Practice Log > return summary
4. **Abandon**: TTL expires > session auto-deleted > nothing saved for incomplete batch

### API Endpoints

| Endpoint                | Method | Purpose                                          |
| ----------------------- | ------ | ------------------------------------------------ |
| `/practice/hierarchy`   | GET    | Browse content hierarchy with access flags       |
| `/practice/start`       | POST   | Start session, validate access, return 1st batch |
| `/practice/submit`      | POST   | Submit batch results (idempotent)                |
| `/practice/continue`    | POST   | Request next batch in same session               |

### Rate Limiting

| Endpoint                 | Limit  | Window |
| ------------------------ | ------ | ------ |
| GET /practice/hierarchy  | 30/min | 60s    |
| POST /practice/start     | 10/min | 60s    |
| POST /practice/submit    | 30/min | 60s    |
| POST /practice/continue  | 30/min | 60s    |

---

## Assumptions

- **A-001**: The existing content_hash field on lessons is sufficient to detect content changes for the sync job.
- **A-002**: The existing access control infrastructure works for practice arena access checks without modification.
- **A-003**: The existing completion bitset mechanism is sufficient for the "Completed only" filter.
- **A-004**: Placeholder questions are acceptable for the initial release. AI-generated question variants are a future enhancement.
- **A-005**: The sync job running every 2 minutes provides acceptable freshness for teacher content changes.
- **A-006**: 500 million Practice Log rows can be served efficiently as a raw SQL table (not Frappe DocType) with proper indexing, without requiring table partitioning.
- **A-007**: The practice session TTL of 1 hour is sufficient for typical student sessions.
- **A-008**: Multi-track selection disables deeper drilling (unit/topic selection) — deliberate UX constraint.

---

## Relationship to Existing System

The Practice Arena interacts with (read-only):
- **Player Subscription** — determines accessible tracks
- **Structure Progress** — determines completed lessons for the "Completed only" filter
- **Lesson Stages** — source data for Review Item extraction

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
- Integration with FSRS / Memory State
- AI-generated question variants (future phase — placeholders used for now)
- Frontend UI/UX design details (frontend team handles this)
- Real-time multiplayer practice modes

---

## Success Criteria

### Measurable Outcomes

- **SC-001**: A student can start a practice session, answer questions, and see results within 2 seconds of each action (P95 latency).
- **SC-002**: The Review Item sync job processes 100 changed lessons in under 30 seconds.
- **SC-003**: Question selection returns results in under 100ms for a student with 5,000 practice log entries.
- **SC-004**: 100K concurrent students can use the Practice Arena without degradation in daily review system performance.
- **SC-005**: Duplicate batch submissions (network retries) do not corrupt practice log data.
- **SC-006**: Students cannot access content they are not subscribed to, verified by backend enforcement.

---

## Clarifications

### Session 2026-02-23

- Q: Where should Practice Sessions be stored? → A: Redis hash with TTL (ephemeral, fast, native expiry)
- Q: What storage strategy should Practice Log use? → A: Raw SQL table with direct queries (like Memory State — no Frappe DocType overhead)
- Q: Should User Story 1 be removed given 024 already built Review Item? → A: Keep US1 with gap-filling scenarios (Practice Log cascade, is_reviewable filter, debounce, SENTENCE_BUILDER/MINDMAP extraction)
- Q: Can a student have multiple concurrent practice sessions? → A: No — one session at a time per student; starting a new session auto-expires the previous one
- Q: Can students practice free content without a subscription? → A: Yes — free topics/units are practicable without subscription, consistent with existing access model
