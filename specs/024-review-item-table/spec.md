# Feature Specification: Review Items Table

**Feature Branch**: `024-review-item-table`
**Created**: 2026-02-22
**Status**: Draft
**Input**: PRD for Memora Review Item — a dedicated database table storing review questions and choices for fast batch retrieval during spaced repetition review sessions.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Student Retrieves Review Questions Instantly (Priority: P1)

A student opens their daily review session. The system fetches 10 due items from Memory State and retrieves the corresponding questions and choices from the Review Item table in a single query. The student sees ready-to-display multiple-choice questions immediately without the client needing to open lesson files.

**Why this priority**: This is the core value proposition — replacing 10 file lookups with 1 database query for 100k+ concurrent students.

**Independent Test**: Can be tested by inserting review items into the table, then querying by a set of item IDs and verifying the response contains complete question data.

**Acceptance Scenarios**:

1. **Given** 10 review items exist in the table, **When** the backend queries by their IDs, **Then** all 10 items are returned with question text, choices, and correct answer in a single query.
2. **Given** a review item has only 2 choices (choice_3 and choice_4 are empty), **When** retrieved, **Then** only the non-empty choices are included in the response.
3. **Given** a student requests review but some item IDs from Memory State have no matching Review Item record, **When** the query runs, **Then** only existing items are returned and missing items are logged for investigation.

---

### User Story 2 - Teacher Saves a Lesson and Review Items Auto-Populate (Priority: P1)

A teacher saves a lesson in the admin panel. The system automatically iterates over every non-skippable stage, extracts items from `config_json`, and creates or updates corresponding Review Item records with question text, choices, correct answer, and full hierarchy references (subject, track, unit, topic, lesson, stage).

**Why this priority**: Without automatic population on save, the Review Item table would remain empty. This is required for User Story 1 to function.

**Independent Test**: Can be tested by saving a lesson with known stages and items, then verifying the Review Item table contains the expected records.

**Acceptance Scenarios**:

1. **Given** a lesson has 3 non-skippable stages with 2 items each, **When** the teacher saves the lesson, **Then** 6 Review Item records are created with correct hierarchy references.
2. **Given** a Review Item already exists for an item, **When** the teacher updates the lesson and saves, **Then** the existing Review Item record is updated (not duplicated).
3. **Given** a stage is marked as skippable (`is_skippable = true`), **When** the teacher saves the lesson, **Then** no Review Items are created for that stage's items, and any previously existing Review Items for that stage are deleted.
4. **Given** the teacher removes an item from a stage, **When** the lesson is saved, **Then** the corresponding Review Item record is deleted and the corresponding Memory State record is also deleted or deactivated.

---

### User Story 3 - Teacher Deletes Content and Orphaned Data is Cleaned Up (Priority: P2)

When a teacher deletes a lesson, stage, or removes items, the system automatically removes the corresponding Review Item records and ensures no orphaned entries remain in Memory State.

**Why this priority**: Data integrity is critical but secondary to the creation flow. Without cleanup, students could be asked to review items that no longer exist.

**Independent Test**: Can be tested by creating review items, then deleting the parent lesson and verifying all child review items and their Memory State entries are removed.

**Acceptance Scenarios**:

1. **Given** a lesson has 10 Review Items, **When** the entire lesson is deleted, **Then** all 10 Review Items are deleted and all 10 corresponding Memory State records are deleted or deactivated.
2. **Given** a stage has 3 Review Items, **When** the stage is deleted, **Then** all 3 Review Items for that stage are deleted.
3. **Given** a stage with 3 Review Items is changed from non-skippable to skippable, **When** the lesson is saved, **Then** all 3 Review Items for that stage are deleted and their Memory State records are cleaned up.

---

### User Story 4 - Review Session Size is Configurable (Priority: P3)

An administrator can change the number of questions per review session from the default of 10 to any other value via Memora Settings, without requiring a code change or redeployment.

**Why this priority**: Nice-to-have configurability. The default of 10 works for most cases.

**Independent Test**: Can be tested by changing the setting value and verifying that the next review session returns the updated number of items.

**Acceptance Scenarios**:

1. **Given** the `review_session_size` setting is set to 15, **When** a student requests a review session, **Then** up to 15 items are fetched.
2. **Given** the setting is not configured (first install), **When** queried, **Then** the default value of 10 is used.

---

### Edge Cases

- What happens when a review item's question text or choices contain special characters or very long Arabic text?
  - The system stores and returns the text as-is. Content is plain text only (no images, icons, or equations).
- What happens when Memory State references an item ID that doesn't exist in the Review Item table?
  - The query returns only the items that exist. Missing items are logged. This can happen for lessons not yet re-saved after this feature is deployed.
- What happens during bulk content operations (e.g., importing hundreds of lessons)?
  - Each lesson save triggers its own review item sync. The system handles this per-lesson without batch optimization (reasonable since lesson saves are admin operations, not student-facing).
- What happens if a lesson has no non-skippable stages?
  - No Review Items are created for that lesson. This is valid — the lesson simply has no reviewable content.
- What happens when the same item ID exists across multiple stages?
  - Item IDs are UUIDs and globally unique. The primary key ensures no duplicates. If a collision somehow occurs, the latest save wins (upsert behavior).

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: System MUST store each reviewable item as an independent record with question text, up to 4 choices, correct choice number, stage reference, stage type, and full hierarchy references (lesson, topic, unit, track, subject).
- **FR-002**: System MUST use the same UUID as the item's primary key that exists in `config_json` and `Memora Memory State`, ensuring a unified identifier across all three data stores.
- **FR-003**: System MUST support fetching multiple review items by a set of IDs in a single query, returning question text, choices, and correct answer for each.
- **FR-004**: System MUST automatically create or update Review Item records whenever a teacher saves a lesson, processing every item inside non-skippable stages.
- **FR-005**: System MUST delete Review Item records and corresponding Memory State records when items are removed from a stage, a stage is deleted, a stage is switched to skippable, or an entire lesson is deleted.
- **FR-006**: System MUST maintain referential integrity — there must never be an item in Memory State that does not have a corresponding Review Item record (enforced via cleanup on delete).
- **FR-007**: System MUST support filtering review items by subject, track, unit, topic, lesson, or stage through appropriate indexes.
- **FR-008**: System MUST provide a configurable `review_session_size` setting in Memora Settings with a default value of 10.
- **FR-009**: System MUST return review items to the client in a format containing: item_id, stage_id, lesson_id, stage_type, question_text, an array of non-empty choices, correct_choice number, and content_json (for non-MCQ stages like FILL_BLANK and MATCHING).
- **FR-010**: System MUST handle the gradual population scenario — existing lessons do not need data migration; the table fills as lessons are saved going forward.
- **FR-011**: System MUST remove the `stability` and `difficulty` fields from the review items API response. These are internal FSRS state not needed by the client. This is a **breaking change** to the existing `GET /api/v1/reviews/{subject}` response format.

### Key Entities

- **Review Item**: A single reviewable question derived from a lesson stage item. Contains the question text, up to 4 choices, the correct choice number, and references to its position in the content hierarchy (subject > track > unit > topic > lesson > stage). Identified by the same UUID used in `config_json` and Memory State.
- **Memory State** (existing): Stores spaced repetition state (stability, difficulty, next review date) for each item per student. Linked to Review Item by the shared UUID (`item_id`).
- **Lesson Stage** (existing): Child table of Lesson containing `config_json` with item definitions. Source of truth for item creation — Review Items are derived from this data on lesson save.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: A review session for 10 items completes data retrieval in under 5 milliseconds, compared to the current multi-file approach.
- **SC-002**: The system supports 40 million review item records without query performance degradation for ID-based lookups.
- **SC-003**: 100% of items in non-skippable stages have corresponding Review Item records after a lesson is saved.
- **SC-004**: Zero orphaned Memory State records exist for items that have been deleted from the Review Item table, verified within one save cycle.
- **SC-005**: Teachers experience no noticeable delay when saving lessons — review item sync completes within the existing save operation.
- **SC-006**: Students always see the latest version of review content — changes made by teachers are reflected immediately after save without requiring any manual sync or cache refresh.

## Assumptions

- Content is plain text only — no images, icons, equations, or rich formatting in questions or choices.
- One review question per item, with a maximum of 4 choices per question.
- The `config_json` field in Lesson Stage contains structured data with identifiable item IDs (UUIDs) that can be parsed programmatically.
- The correct_choice field is sent to the client to enable instant local answer validation without an additional server round-trip.
- Skippable stages (`is_skippable = true`) never produce reviewable content.
- The table is independent from the CDN build process — each has its own lifecycle.
- Memory State cleanup on item deletion uses hard delete (not soft delete), matching the Review Item deletion behavior.
- The hierarchy references (subject, track, unit, topic) are available at lesson save time from the lesson's parent chain.
- Question generation logic (how items are converted to multiple-choice questions) is out of scope for this feature and handled separately.
