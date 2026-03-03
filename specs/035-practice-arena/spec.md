# Feature Specification: Practice Arena (ساحة التدريب)

**Feature Branch**: `035-practice-arena`
**Created**: 2026-03-02
**Status**: Draft
**Input**: PRD for student-initiated practice mode with review item extraction, hierarchy-based content selection, batched question sessions, and practice logging

## User Scenarios & Testing *(mandatory)*

### User Story 1 — Review Item Extraction (Priority: P0)

As a system, when a teacher saves or modifies a lesson, I need to extract all reviewable items from the lesson's stages and populate a flat, queryable table so that items are available for practice sessions and future features.

**Why P0**: Without this table populated, the Practice Arena has no questions to serve. This is the foundational data layer that all other stories depend on.

**Independent Test**: Can be fully tested by saving a lesson with known stages and verifying the correct number of items appear in the review item table with accurate hierarchy and content data.

**Acceptance Scenarios**:

1. **Given** a teacher saves a lesson with 3 non-skippable stages containing 8 total items, **When** the background sync job runs, **Then** 8 rows exist in the review item table with correct hierarchy fields, question text, and choices.
2. **Given** a teacher modifies a lesson and changes one item's content, **When** the sync job runs, **Then** the corresponding row is updated with the new content. Unchanged items are not touched.
3. **Given** a teacher deletes an item from a stage, **When** the sync job runs, **Then** the corresponding review item row is hard-deleted AND any related practice log rows for that item are also deleted (cascade).
4. **Given** a teacher adds a new item to an existing stage, **When** the sync job runs, **Then** a new review item row is created with the correct item_id and full hierarchy.
5. **Given** a lesson has is_reviewable = false, **When** the sync job runs, **Then** no items from that lesson appear in the review item table. If items previously existed, they are deleted.
6. **Given** a stage has is_skippable = true (per-stage override or global setting), **When** the sync job runs, **Then** no items from that stage appear in the review item table.
7. **Given** a teacher changes a stage type (e.g., MATCHING to QUESTION), **When** the sync job runs, **Then** old items are deleted and new items are created with new item_id values.
8. **Given** a teacher saves the same lesson 10 times within 2 minutes, **When** the sync job runs, **Then** the lesson is processed only once (dedup via content hash comparison).
9. **Given** a stage with null or invalid JSON configuration, **When** the sync job runs, **Then** the stage is silently skipped with a warning log. Other stages in the same lesson are processed normally.
10. **Given** a lesson with all stages being skippable, **When** the sync job runs, **Then** no items are generated. If items previously existed for this lesson, they are deleted.

---

### User Story 2 — Hierarchy Selection (Priority: P1)

As a student, I want to choose what to practice by navigating through subject, track, unit, and topic so I can focus on specific content areas.

**Why P1**: This is the entry point to the Practice Arena. Without it, students cannot start a session.

**Independent Test**: Can be fully tested by calling the hierarchy endpoint for a subject and verifying the response contains tracks/units/topics with correct accessible flags and item counts.

**Acceptance Scenarios**:

1. **Given** a student opens the Practice Arena and selects a subject, **When** the hierarchy is requested, **Then** ALL tracks/units/topics are returned (including locked ones), with an `accessible` flag on each level indicating whether the student has access.
2. **Given** a student selects "Completed only" filter, **When** the hierarchy is loaded, **Then** only tracks/units/topics where the student has completed at least one lesson are shown. Items with zero completed lessons are hidden entirely.
3. **Given** a student selects "All content" filter, **When** the hierarchy is loaded, **Then** all tracks/units/topics are shown, including unstarted content.
4. **Given** a student selects multiple tracks (e.g., 3), **When** the UI updates, **Then** unit/topic selection is disabled (multi-selection at one level prevents drilling deeper).
5. **Given** a student selects exactly 1 track, **When** the UI updates, **Then** unit selection becomes available for that track.
6. **Given** a student selects exactly 1 unit, **When** the UI updates, **Then** topic selection becomes available for that unit.
7. **Given** a student selects multiple units (e.g., 2), **When** the UI updates, **Then** topic selection is disabled; practice spans both units.
8. **Given** a student with no completed lessons selects "Completed only" filter, **Then** empty hierarchy is returned.

---

### User Story 3 — Practice Session Flow (Priority: P1)

As a student, I want to start a practice session, answer questions in batches, see my results, and optionally continue with more questions using the same filters.

**Why P1**: This is the core user-facing functionality of the Practice Arena.

**Independent Test**: Can be fully tested by starting a session, receiving a batch of questions, submitting results, and requesting another batch. Verify correct question counts, proportional distribution, and result persistence.

**Acceptance Scenarios**:

1. **Given** a student starts a session with valid filters, **When** the request is processed, **Then** up to 20 questions are returned (configurable batch size) with proportional distribution across topics based on content volume.
2. **Given** a student's selected filters match only 7 items but batch size is 20, **When** the session starts, **Then** only 7 questions are returned. No padding with items from outside the selected filters.
3. **Given** a student has never practiced before, **When** questions are selected, **Then** all questions are unseen items (first priority).
4. **Given** a student has seen all items matching the filters, **When** a new batch is requested, **Then** the response includes an `all_seen_warning` flag and questions are ordered by oldest seen first.
5. **Given** a batch contains ANY repeat questions (even one), **When** the response is returned, **Then** `all_seen_warning` is true. This flag is checked on every batch.
6. **Given** a student completes a batch and requests more, **When** the next batch is served, **Then** priority order is: (1) never seen, (2) seen in previous sessions oldest first, (3) seen in current session oldest first.
7. **Given** a student completes a batch, **When** results are submitted, **Then** the practice log is updated immediately. Not deferred.
8. **Given** a student exits mid-batch (closes app/browser), **When** the session expires, **Then** nothing is saved for the incomplete batch. Previously completed batches remain saved.
9. **Given** a student submits the same batch results twice (network retry), **When** the backend processes the duplicate, **Then** the second submission is ignored and the cached response is returned (idempotency via batch sequence number).
10. **Given** a student starts a new session while having an active one, **When** the new session is created, **Then** the old session is auto-expired (one active session per student enforced).

---

### User Story 4 — Access Control Enforcement (Priority: P1)

As the system, I need to ensure students can only practice content they have access to, even if the frontend sends invalid requests.

**Why P1**: Security boundary — students must not access paid content they haven't subscribed to.

**Independent Test**: Can be fully tested by attempting to start sessions with tracks the student does/doesn't have access to and verifying correct acceptance or rejection.

**Acceptance Scenarios**:

1. **Given** a student starts a session with a track they are NOT subscribed to and is NOT free, **When** the backend validates the request, **Then** the request is rejected with a 403 error.
2. **Given** a student's subscription expires during an active session, **When** they request the next batch (continue), **Then** the session continues normally. Access is checked only once at session start.
3. **Given** a student has full subject access, **When** they select any track within that subject, **Then** access is granted for all tracks.
4. **Given** a student has access to a single track only, **When** they select a different track in the same subject, **Then** access is denied for the unsubscribed track.
5. **Given** a unit or topic is marked as free, **When** a student without any subscription selects it, **Then** access is granted. Free content is practicable without a subscription.
6. **Given** a subject is in the plan's free subjects set, **When** a student selects tracks in that subject, **Then** access is granted.

---

### Edge Cases

| Scenario | Expected Behavior |
| -------- | ----------------- |
| Lesson with no reviewable stages | Silently skipped — no items generated |
| Stage with null or invalid JSON config | Silently skipped with warning log |
| Teacher updates content while student is mid-session | Student sees the version loaded at session start. Updated content appears in the next session |
| Item deleted while student has it in active session | When results are submitted, the deleted item is silently skipped. Other results saved normally |
| Teacher deletes an item | Hard delete from review item table + cascade delete from practice log |
| All items in selected filters are from skippable stages | Empty result — session cannot start, return error |
| Session storage goes down during a session | Session state lost. Student must start a new session. Previously saved batches are safe |
| Student with no completed lessons + "Completed only" filter | Empty hierarchy returned |
| Available items fewer than batch size | Return only available items, no padding from outside filters |
| Student starts new session with one already active | Old session auto-expired, new one created |
| Duplicate batch submission (network retry) | Ignored via batch sequence idempotency, cached response returned |
| is_reviewable = false on lesson | No items extracted for practice, consistent with daily reviews |

## Requirements *(mandatory)*

### Functional Requirements

**Review Item Extraction**

- **FR-001**: System MUST extract reviewable items from lesson stages into a flat, queryable table with full denormalized hierarchy (subject, track, unit, topic, lesson, stage).
- **FR-002**: System MUST use a dirty-set pattern for extraction: teacher saves enqueue the lesson, a scheduled job (every 2 minutes) processes the queue.
- **FR-003**: System MUST support item extraction from all reviewable stage types: QUESTION (MCQ with individual choice fields and 1-based correct choice index), MATCHING, FILL_BLANK, SENTENCE_BUILDER, and MINDMAP (via structured content field). INFORMATION and REVEAL stages produce zero items.
- **FR-004**: System MUST skip stages marked as skippable (per-stage override or global setting) and stages with null/invalid configuration.
- **FR-005**: System MUST deduplicate processing via content hash comparison — if a lesson's content hasn't changed since the last sync, skip it.
- **FR-006**: System MUST hard-delete review items when items are removed from a stage, and cascade-delete related practice log entries.
- **FR-007**: System MUST retain dirty set entries on processing failure for automatic retry on the next scheduled run.

**Hierarchy & Content Selection**

- **FR-008**: System MUST provide a hierarchy endpoint returning all tracks/units/topics for a subject, with an `accessible` flag on each level.
- **FR-009**: System MUST support two content filters: "Completed only" (shows only content where student completed at least one lesson) and "All content" (shows everything).
- **FR-010**: The `accessible` flag MUST cascade downward: track access flows from subject-level or track-level grants; unit access flows from parent track or unit free-content flag; topic access flows from parent unit or topic free-content flag.
- **FR-011**: System MUST include `item_count` at each hierarchy level (track, unit, topic) representing the number of practicable items.

**Practice Sessions**

- **FR-012**: System MUST enforce one active session per student. Starting a new session auto-expires any existing one.
- **FR-013**: System MUST store session state ephemerally with automatic expiry (default: 1 hour). If session storage is lost, student simply starts over.
- **FR-014**: System MUST distribute questions proportionally across topics based on content volume within the selected filters.
- **FR-015**: System MUST prioritize questions in this order: (1) never seen by this student, (2) seen in previous sessions ordered oldest first, (3) seen in current session ordered oldest first.
- **FR-016**: System MUST set `all_seen_warning` flag to true if ANY question in a batch has been seen before by the student.
- **FR-017**: System MUST save batch results immediately upon submission, not deferred.
- **FR-018**: System MUST enforce idempotency on batch submissions via batch sequence numbers. Duplicate submissions return the cached response without re-processing.
- **FR-019**: System MUST silently skip items in submitted results that no longer exist in the review item table (teacher deleted mid-session).

**Access Control**

- **FR-020**: System MUST validate access at session start for every selected track. If any track is inaccessible, reject the entire request with a 403 error including the denied track IDs.
- **FR-021**: System MUST NOT re-check access on subsequent batch requests within the same session (access checked once at start).
- **FR-022**: System MUST allow access to free content (free units, free topics, plan-level free subjects) without a subscription.

**Rate Limiting**

- **FR-023**: System MUST rate-limit all practice endpoints: hierarchy (30/min), start session (10/min), submit results (30/min), continue session (30/min) — per student.

**Configuration**

- **FR-024**: System MUST support configurable batch size (default: 20 questions) and session expiry time (default: 1 hour).

**Isolation**

- **FR-025**: Practice Arena MUST have zero connection to the FSRS daily review system, streaks, leaderboards, or XP/wallet.

### Key Entities

- **Review Item**: A flat, denormalized record of a single reviewable content item extracted from a lesson stage. Contains full hierarchy path (subject through stage), question data (MCQ choices with 1-based correct index, or structured content for non-MCQ types), and the item's UUID. One row per item, one question per item. Estimated ~200,000 rows.
- **Practice Log**: A per-student-per-item record tracking encounter history. Stores first seen, last seen, last result, total attempts, and correct count. Updated via upsert on each batch submission. One row per student per item — timeless, not tied to seasons. Estimated ~500 million rows at scale.
- **Practice Session**: Ephemeral state holding the student's filter selections (subject, tracks, units, topics, content filter), served item IDs, batch sequence number, and idempotency markers. Keyed by student ID (one session per student). Auto-expires after configurable timeout.
- **Dirty Set**: A protected queue of lesson names awaiting review item extraction. Produced by lesson save hooks, consumed by the scheduled sync job. Entries remain on failure for auto-retry. No expiry — protected from eviction.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: Students can start a practice session, receive questions, and submit results within 2 seconds (P95) per action.
- **SC-002**: The review item sync job processes 100 changed lessons within 30 seconds.
- **SC-003**: Question selection for a student with 5,000 practice log entries completes within 100 milliseconds.
- **SC-004**: System supports 100,000 concurrent students practicing without degradation to the existing daily review system.
- **SC-005**: Duplicate batch submissions do not corrupt practice log data (verified by test).
- **SC-006**: Students cannot practice inaccessible paid content (verified by backend enforcement tests).
- **SC-007**: Free content is accessible for practice without a subscription (verified by test).
