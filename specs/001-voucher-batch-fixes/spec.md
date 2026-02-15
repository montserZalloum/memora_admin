# Feature Specification: Voucher Batch Counter Fixes & Auto-Close

**Feature Branch**: `001-voucher-batch-fixes`
**Created**: 2026-02-15
**Status**: Draft
**Input**: User description: "Phase 1 from VOUCHER_TEST_SUITE_PLAN.md — fix expired_count tracking and implement batch auto-close"

## Clarifications

### Session 2026-02-15

- Q: Should auto-closed batches be distinguishable from manually closed/voided ones? → A: No new field — absence of `void_reason` implicitly marks auto-close.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Accurate Expired Card Tracking (Priority: P1)

As a platform administrator, I need to see how many voucher cards were expired due to season endings so I can accurately report on batch utilization and distinguish between manually voided cards and automatically expired ones.

Currently, when the daily season expiration job runs and expires cards, the batch-level counters are not updated. The `voided_count` field does not reflect expired cards (they are semantically different), and no `expired_count` field exists. This means batch summaries in the admin panel show stale or incomplete data.

**Why this priority**: Without accurate counters, administrators cannot trust batch reports. Expired and voided cards serve different business purposes (automatic season-based vs. manual with reason), and conflating them causes confusion in financial reconciliation and inventory reporting.

**Independent Test**: Can be fully tested by creating a batch with cards linked to an ended season, running the expiration job, and verifying the batch's `expired_count` reflects the number of expired cards. Delivers accurate batch reporting immediately.

**Acceptance Scenarios**:

1. **Given** a batch with 10 Available cards linked to an ended season, **When** the season expiration job runs, **Then** the batch's `expired_count` equals 10 and `allocated_count` equals 0.
2. **Given** a batch with 5 Available and 3 Allocated cards linked to an ended season, **When** the expiration job runs, **Then** `expired_count` equals 8, `allocated_count` equals 0, and previously terminal cards (Redeemed, Void) remain unchanged.
3. **Given** a batch with 10 cards where 5 are already Redeemed and 5 are Available, **When** the expiration job runs for an ended season, **Then** `expired_count` equals 5 and `redeemed_count` remains unchanged.
4. **Given** a batch linked to an active (future end-date) season, **When** the expiration job runs, **Then** no cards are expired and `expired_count` remains 0.

---

### User Story 2 - Automatic Batch Closure (Priority: P1)

As a platform administrator, I need batches to automatically close when all their cards reach terminal states (Redeemed, Void, or Expired) so that I don't have to manually track and close batches, and so the system accurately reflects which batches still have actionable cards.

Currently, batches only close via the explicit "Void Batch" action. If all cards are individually redeemed, voided, or expired through normal operations, the batch stays Active forever, cluttering the active batch list and misrepresenting inventory.

**Why this priority**: Equal to P1 because stale Active batches create operational noise — administrators cannot distinguish batches with remaining actionable cards from fully consumed ones. This affects daily workflow and reporting accuracy.

**Independent Test**: Can be fully tested by creating a batch, driving all cards to terminal states through various paths (redeem, void, expire), and verifying the batch status transitions to Closed automatically. Delivers clean batch lifecycle management immediately.

**Acceptance Scenarios**:

1. **Given** an Active batch where the last Available card is redeemed, **When** the redemption completes, **Then** the batch status changes to Closed.
2. **Given** an Active batch where the last non-terminal card is manually voided, **When** the void operation completes, **Then** the batch status changes to Closed.
3. **Given** an Active batch where the last non-terminal cards are expired by the season job, **When** the expiration job completes for that batch, **Then** the batch status changes to Closed.
4. **Given** an Active batch with a mix of Redeemed, Void, and Expired cards but no Available or Allocated cards remaining, **When** auto-close is evaluated, **Then** the batch status is Closed.
5. **Given** an Active batch with 8 Redeemed cards and 2 Available cards, **When** one card is redeemed, **Then** the batch remains Active (1 Available card still exists).
6. **Given** a Generated batch (not yet Active), **When** all cards happen to reach terminal states, **Then** the batch status does NOT change (auto-close only applies to Active batches).

---

### Edge Cases

- What happens if the expiration job fails mid-batch (some cards expired, some not)? The counters should still reflect the actual card states via recount, not incremental addition.
- What happens if a batch has zero cards (edge case from generation failure)? Auto-close should not trigger on empty batches since they were never Active.
- What happens if two concurrent operations (e.g., redemption and void) both trigger auto-close simultaneously? Only one should transition the batch; the check must be safe against race conditions.
- What happens if the expiration job runs twice for the same batch? Cards already in Expired status should not be double-counted; the recount approach ensures idempotency.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: The system MUST track expired cards separately from voided cards at the batch level with a dedicated counter.
- **FR-002**: When the season expiration job expires cards in a batch, the system MUST update the batch's expired card counter to reflect the actual count of expired cards (via recount, not increment).
- **FR-003**: When the season expiration job expires cards that were previously in Allocated status, the system MUST also recount and update the batch's allocated card counter.
- **FR-004**: The system MUST automatically evaluate whether a batch should be closed after any operation that moves a card to a terminal state (redemption, voiding, or expiration).
- **FR-005**: Auto-close MUST only apply to batches in Active status. Batches in Draft or Generated status are not eligible for auto-close.
- **FR-006**: A batch MUST be closed when zero cards remain in non-terminal states (Available or Allocated). Terminal states are: Redeemed, Void, Expired.
- **FR-007**: The expired card counter MUST be read-only in the admin interface (not manually editable) and default to zero for new batches.
- **FR-008**: Auto-close evaluation MUST be triggered after: (a) a card is redeemed, (b) a card is individually voided, and (c) the season expiration job processes a batch.
- **FR-009**: Counter updates during expiration MUST use recount queries (count actual card states) rather than incremental arithmetic to ensure idempotency and accuracy after partial failures.

### Key Entities

- **Voucher Batch**: Container for a set of voucher cards. Has a lifecycle status (Draft, Generated, Active, Closed) and counter fields tracking the distribution of card states. The new `expired_count` counter tracks cards expired via season endings.
- **Voucher Card**: Individual voucher within a batch. Has a status that progresses through Available, Allocated, and terminal states (Redeemed, Void, Expired).
- **Season**: Time-bounded period that governs card validity. When a season ends, all non-terminal cards in batches linked to that season are expired.

## Assumptions

- The `expired_count` field is placed in the batch schema alongside the existing counter fields (`generated_count`, `allocated_count`, `redeemed_count`, `voided_count`).
- The auto-close helper is a lightweight check (single count query for non-terminal cards) and does not introduce meaningful latency to redemption or void operations.
- The existing `void_batch()` bulk operation already handles batch closure and is not affected by this change — it continues to work as-is. Auto-closed batches are implicitly distinguished from manually voided batches by the absence of a `void_reason` value.
- The `allocated_count` field already exists in the schema but is not actively maintained by the expiration job — this fix addresses that gap.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: After the season expiration job runs, 100% of affected batches show an expired card count that matches the actual number of expired cards in the batch.
- **SC-002**: After the season expiration job runs, the allocated card count on each affected batch matches the actual number of cards still in Allocated status (zero for fully expired batches).
- **SC-003**: Within 1 operation of the last card reaching a terminal state, the batch status transitions to Closed — no manual intervention required.
- **SC-004**: Batches that still have cards in Available or Allocated status never auto-close, regardless of how many terminal cards exist.
- **SC-005**: The expired card count and voided card count are independent — expiring cards does not alter the voided count, and voiding cards does not alter the expired count.
- **SC-006**: Running the expiration job multiple times for the same batch produces the same counter values (idempotent).
