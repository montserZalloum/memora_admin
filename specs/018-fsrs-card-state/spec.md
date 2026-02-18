# Feature Specification: FSRS Card State Persistence

**Feature Branch**: `018-fsrs-card-state`
**Created**: 2026-02-18
**Status**: Draft
**Input**: Fix FSRS card state persistence - add missing state, step, and last_review fields to Memory State table so FSRS cards properly graduate from Learning to Review state

## Problem Statement

The spaced repetition system currently only persists three fields per memory card (stability, difficulty, next_review). The FSRS algorithm requires six fields to correctly reconstruct a card and compute the next review interval. Because the card's progression state, learning step, and last review timestamp are lost between reviews, every card is treated as a brand-new learning card on every review. This causes the algorithm to output short intervals (minutes), which then get clamped to the business-mandated minimum of "tomorrow." The result: no matter how well or how many times a student reviews an item, it always comes back tomorrow.

**Business rule (preserved)**: The minimum review interval is always tomorrow (next calendar day). This is intentional and must not change.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Review Intervals Grow With Mastery (Priority: P1)

As a student, when I consistently answer a review item correctly, the system should schedule it further and further into the future, reflecting my growing retention of the material. Currently, every item returns tomorrow regardless of my performance.

**Why this priority**: This is the core value of spaced repetition. Without growing intervals, the system is just daily repetition with no intelligence. Students get overwhelmed with reviews that never space out, and the algorithm provides no learning benefit over a simple daily quiz.

**Independent Test**: A student reviews the same item correctly multiple times across several days. After each correct review, the next scheduled review date should be further away than the previous interval. Can be verified by inspecting the next_review date in Memory State after each review cycle.

**Acceptance Scenarios**:

1. **Given** a student completes a lesson stage item for the first time with zero errors, **When** the FSRS processor runs, **Then** the item is scheduled for review tomorrow (minimum interval rule applies to the initial learning phase).
2. **Given** a student reviews a due item correctly (zero errors) for the second time, **When** the review is submitted, **Then** the card graduates from learning to review state and the next review is scheduled 2+ days from now.
3. **Given** a student has reviewed an item correctly 4 times (card is in review state with high stability), **When** they review it correctly again, **Then** the next review is scheduled weeks or months away, not tomorrow.
4. **Given** a student reviews a due item with 2+ errors (Again rating), **When** the review is submitted, **Then** the card re-enters the relearning state and the next review is scheduled for tomorrow (minimum interval).

---

### User Story 2 - Difficulty Adjusts Intervals Appropriately (Priority: P2)

As a student, when I make mistakes on a review item, the system should schedule it sooner than items I answer perfectly. Items I struggle with should come back sooner; items I master should come back much later.

**Why this priority**: Differentiated scheduling based on difficulty is what makes spaced repetition effective. Without it, easy and hard items are treated identically.

**Independent Test**: Two items reviewed on the same day: one answered perfectly (Good), one answered with errors (Again). After processing, the "Good" item should have a later next_review date than the "Again" item.

**Acceptance Scenarios**:

1. **Given** a student reviews a mature item (review state) with zero errors, **When** the review is processed, **Then** stability increases and the next review interval grows.
2. **Given** a student reviews a mature item with 2+ errors, **When** the review is processed, **Then** the card enters relearning state, stability decreases, and the next review is scheduled for tomorrow.
3. **Given** a student reviews an item with 1 error (Hard rating), **When** the review is processed, **Then** the interval grows less than a perfect review but more than a failed one.

---

### User Story 3 - Memory Mastery Classification Reflects Real Progress (Priority: P2)

As a student viewing my profile, I want the memory mastery breakdown (mature/learning/new) to accurately reflect my review history. Currently, cards that have been reviewed many times may still show as "learning" because their state never properly progresses.

**Why this priority**: The mastery classification uses stability thresholds. With the current bug, stability values are unreliable (they spike erratically due to broken card reconstruction), making the mature/learning/new counts misleading.

**Independent Test**: A student who has successfully reviewed items over several weeks should see items classified as "mature" (stability >= 21 days) in their profile.

**Acceptance Scenarios**:

1. **Given** a student has reviewed an item correctly across multiple sessions spanning weeks, **When** they view their memory mastery, **Then** the item is counted as "mature."
2. **Given** a student reviews an item for the first time today, **When** they view memory mastery, **Then** the item is counted as "learning" (not "new," since it has been reviewed once).

---

### User Story 4 - Existing Review Data Handled Gracefully (Priority: P1)

As a system operator, I need existing Memory State records (which lack state/step/last_review) to continue working after the update without requiring a full reprocessing of historical data.

**Why this priority**: There are existing records in production. The system must handle records that were created before the new fields existed, without breaking review queries or the FSRS processor.

**Independent Test**: After deployment, existing Memory State records with NULL state/step/last_review should be handled gracefully. The system should treat them as cards needing re-initialization rather than crashing.

**Acceptance Scenarios**:

1. **Given** an existing Memory State record with NULL state/step/last_review, **When** the student reviews that item, **Then** the system treats it as a learning card (state=Learning, step=0), processes the review, and persists all six fields going forward.
2. **Given** an existing Memory State record with NULL state/step/last_review, **When** the review overview query runs, **Then** the item still appears in due counts without errors.
3. **Given** the new columns are added to the partitioned table, **When** the migration runs, **Then** it completes without requiring extended table locks (the table is designed for billions of rows).

---

### Edge Cases

- What happens when a Memory State record has stability > 0 but state is NULL? System treats it as a learning card needing re-initialization on the next review, then persists full state going forward.
- What happens when a card is in relearning state (state=3) after a lapse? The system persists state=Relearning so the next review correctly applies relearning steps rather than treating it as a new card.
- What happens when the background processor and the submit_reviews API process the same item concurrently? The existing idempotency key in the processor and the per-request SQL update in submit_reviews prevent double-processing. No change needed here.
- What happens when existing Redis cached card data (missing new fields) is read after deployment? Cached entries naturally expire (24h TTL) and are replaced with the full state on the next review. NULL/missing fields in cache should be handled as defaults.
- What happens when an existing record has inflated stability/difficulty from the previous broken reconstruction (e.g., stability=4550)? No repair. The system uses the existing values as-is. After the fix, subsequent reviews will apply correct FSRS logic, and values will self-correct over time through natural review cycles.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: The system MUST persist the full FSRS card state for each memory item: stability, difficulty, next_review (existing), plus state, step, and last_review (new).
- **FR-002**: The system MUST restore all six fields when reconstructing an FSRS card for review computation, in both the background processor and the submit reviews endpoint.
- **FR-003**: The system MUST persist all six fields after each review computation, in both the background processor and the submit reviews endpoint.
- **FR-004**: The system MUST treat existing records with NULL state/step/last_review as learning cards (state=Learning, step=0, last_review=NULL) without errors.
- **FR-005**: The system MUST continue enforcing the minimum review interval of tomorrow (next calendar day) regardless of what the FSRS algorithm outputs.
- **FR-006**: The system MUST include the partition key (season_seq) in all queries involving the new columns, consistent with existing raw SQL patterns.
- **FR-007**: The system MUST add the new columns to the partitioned Memory State table using the existing schema management approach (not Frappe's standard migration), ensuring partition compatibility.
- **FR-008**: The system MUST cache the updated card state (including new fields) alongside existing cached data, with graceful handling of cached entries that lack the new fields.

### Key Entities

- **Memory State (Memora Memory State)**: Stores per-item FSRS card data for each player. Currently has stability, difficulty, next_review. Will gain state (card progression phase), step (learning step counter), and last_review (timestamp of most recent review). Partitioned by season_seq. All access via raw SQL only.
- **FSRS Card**: The in-memory representation used by the FSRS library. Contains stability, difficulty, due, state, step, last_review. Must be fully reconstructed from Memory State before computing a review, and fully persisted back after.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: After a student answers an item correctly 3 times across 3 separate days, the next scheduled review is at least 7 days away (not tomorrow).
- **SC-002**: After a student answers an item correctly 5+ times over several weeks, the item is classified as "mature" in the memory mastery profile (stability >= 21 days).
- **SC-003**: A student who consistently fails an item (Again rating) sees it scheduled for tomorrow each time, confirming the minimum interval rule is enforced.
- **SC-004**: Existing Memory State records (pre-migration, with NULL new fields) continue to appear in review queries and are processable without errors.
- **SC-005**: No degradation in review query performance. Due item queries and overview queries remain within existing performance targets.

## Clarifications

### Session 2026-02-18

- Q: Should existing records with corrupted stability/difficulty values (inflated by the broken reconstruction bug) be repaired via a data migration? → A: No. Existing records keep their current stability/difficulty values. No data repair migration. Students will eventually see correct behavior after enough reviews with the fixed reconstruction logic.

## Assumptions

- The FSRS library (v6+) Card object fields (state, step, last_review) are stable and will not change in a breaking way across minor versions.
- The state field maps to an integer enum: 0=New, 1=Learning, 2=Review, 3=Relearning. This is a standard FSRS concept.
- The step field is a nullable integer (NULL when card is in Review state, integer when in Learning/Relearning).
- The last_review field is a datetime (timestamp of when the review was processed).
- Adding nullable columns to the partitioned table can be done without rebuilding partitions.
- The existing "minimum tomorrow" clamp logic is correct business behavior and will be preserved as-is.
- The Redis cached card state will be extended to include the new fields; existing cached entries will naturally expire (24h TTL) and be replaced with the full state on next review.
- Existing records with inflated stability/difficulty values from the broken reconstruction are not repaired. They will self-correct through natural review cycles as the fixed logic is applied on subsequent reviews.
