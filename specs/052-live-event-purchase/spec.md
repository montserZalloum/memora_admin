# Feature Specification: Single Live Event Purchase

**Feature Branch**: `052-live-event-purchase`
**Created**: 2026-03-18
**Status**: Draft
**Input**: User description: "Single Live Event Purchase — purchase flow for paid live events with separated Purchase, Access, and Invoice domains"

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Student Purchases a Paid Live Event (Priority: P1)

A student browsing upcoming live events sees a paid event they want to join. They click "Buy", which creates a pending purchase with a 30-minute expiry. After completing payment, the system atomically confirms the purchase, creates a sales invoice for accounting, and grants the student event access. The student can now join the event when it starts.

**Why this priority**: This is the core value proposition. Without the ability to purchase and gain access, no paid events can function.

**Independent Test**: Can be fully tested by creating a paid event, initiating a purchase, confirming payment, and verifying the student can join. Delivers the primary purchase-to-access flow.

**Acceptance Scenarios**:

1. **Given** a paid event exists and a student is on an eligible plan, **When** the student initiates a purchase, **Then** a pending purchase is created with a 30-minute expiry and payment instructions are returned.
2. **Given** a pending purchase exists, **When** payment is confirmed, **Then** the purchase status becomes "paid", a sales invoice is created, and an active event access record is granted — all atomically.
3. **Given** a student has active event access, **When** the student attempts to join the event during the exam window, **Then** the join succeeds without reading any invoice or payment data.
4. **Given** a student has active event access, **When** the student attempts to purchase the same event again, **Then** the request is rejected ("already owns access").
5. **Given** a student has a pending purchase for an event, **When** the student attempts to create another purchase for the same event, **Then** the request is rejected ("pending purchase already exists").

---

### User Story 2 - Join-Time Access Check for Paid Events (Priority: P1)

When a student attempts to join a live event, the system checks: (1) event status, (2) plan eligibility, and (3) for paid events, whether an active event access record exists. The join decision is fast and deterministic — it never reads invoices or payment gateway state.

**Why this priority**: The join path is the hot path under high load. Its correctness and performance are critical to the live event experience.

**Independent Test**: Can be tested by attempting to join paid events with and without access records, and verifying the system only checks the access record (not invoices).

**Acceptance Scenarios**:

1. **Given** a paid event and a student with active access on an eligible plan, **When** the student joins, **Then** the join succeeds.
2. **Given** a paid event and a student without access, **When** the student attempts to join, **Then** the join is blocked with reason "payment_required".
3. **Given** a free event and a student on an eligible plan, **When** the student joins, **Then** the join succeeds (no access record needed).
4. **Given** any event and a student on a non-eligible plan, **When** the student attempts to join, **Then** the join is blocked with reason "plan_not_eligible".
5. **Given** a paid event and a student who purchased access but later changed to a non-eligible plan, **When** the student attempts to join, **Then** the join is blocked with reason "plan_not_eligible" (plan eligibility always applies).

---

### User Story 3 - Access State Inquiry (Priority: P2)

A student viewing an event can check their access state to see whether they can join, whether payment is required, and whether their plan is eligible. The response is a single, extensible object that the frontend uses to render the correct UI (price + "Buy" button, or "Ready to join").

**Why this priority**: Enables the frontend to render the correct purchase/join UI before the student takes action. Prevents confusion and wasted clicks.

**Independent Test**: Can be tested by querying access state for various combinations of paid/free events, with/without access, on eligible/ineligible plans.

**Acceptance Scenarios**:

1. **Given** a paid event and a student without access on an eligible plan, **When** the student queries access state, **Then** the response shows `requires_payment=true`, `has_direct_access=false`, `is_eligible_plan=true`, `can_join=false`, `reason="payment_required"`.
2. **Given** a paid event and a student with active access on an eligible plan, **When** the student queries access state, **Then** the response shows `has_direct_access=true`, `can_join=true`, `reason=null`.
3. **Given** a free event and a student on an eligible plan, **When** the student queries access state, **Then** the response shows `requires_payment=false`, `can_join=true`.

---

### User Story 4 - Auto-Cancellation of Expired Purchases (Priority: P2)

If a student creates a purchase but does not complete payment within 30 minutes, the system automatically cancels the purchase. The student can then start a new purchase if desired.

**Why this priority**: Prevents stale pending purchases from blocking future purchase attempts and keeps the purchase table clean.

**Independent Test**: Can be tested by creating a purchase, waiting for (or simulating) expiry, and verifying the purchase is cancelled and a new purchase can be created.

**Acceptance Scenarios**:

1. **Given** a pending purchase with `expires_at` in the past, **When** the auto-cancel job runs, **Then** the purchase status becomes "cancelled".
2. **Given** a cancelled expired purchase, **When** the student creates a new purchase for the same event, **Then** the new purchase is created successfully.

---

### User Story 5 - Refund with Automatic Cascade (Priority: P2)

When an admin approves a refund, the system atomically revokes the student's event access, marks the purchase as refunded, and creates a credit note linked to the original invoice. The student can no longer join the event.

**Why this priority**: Essential for customer support operations and accounting integrity. Must be atomic to prevent partial states.

**Independent Test**: Can be tested by completing a purchase, then initiating a refund and verifying access is revoked, purchase is marked refunded, and a credit note exists.

**Acceptance Scenarios**:

1. **Given** a paid purchase with active access, **When** a refund is processed, **Then** the purchase status becomes "refunded", the access status becomes "refunded", and a credit note is created — all atomically.
2. **Given** a refunded purchase, **When** the student attempts to join the event, **Then** the join is blocked (no active access).
3. **Given** a refund is in progress and the credit note creation fails, **When** the failure occurs, **Then** the entire operation rolls back — access remains active, purchase remains paid.

---

### User Story 6 - Shared ERPNext Item for Paid Events (Priority: P3)

All paid Live Challenge Events share a single ERPNext Item (`LIVE-EVENT-ACCESS`). The system ensures this shared item exists (idempotently) at after_migrate and lazily before the first invoice is created. Event-specific details are captured in the invoice line item description, not the item code.

**Why this priority**: Administrative convenience and data consistency. Lower priority because it supports the purchase flow rather than being user-facing.

**Independent Test**: Can be tested by verifying the `LIVE-EVENT-ACCESS` item exists after migration or first invoice creation, and that saving paid events does NOT create per-event items.

**Acceptance Scenarios**:

1. **Given** the system runs `after_migrate`, **When** `LIVE-EVENT-ACCESS` does not exist, **Then** it is created as a non-stock service item.
2. **Given** `LIVE-EVENT-ACCESS` already exists, **When** `after_migrate` or `ensure_shared_live_event_item()` runs again, **Then** no duplicate item is created (idempotent).
3. **Given** a paid event purchase is confirmed, **When** the Sales Invoice is created, **Then** the invoice line item uses `LIVE-EVENT-ACCESS` as the item code with the event name and schedule in the description.
4. **Given** old purchases with per-event item codes exist, **When** a refund creates a Credit Note, **Then** the Credit Note reads the item code from the original invoice (backward compatible).

---

### Edge Cases

- What happens when two concurrent payment callbacks arrive for the same purchase? Only one succeeds; the other is blocked by the lock and finds the purchase already paid.
- What happens when invoice creation fails mid-payment-confirmation? The entire atomic transaction rolls back — purchase stays pending, no access granted. Can be retried safely.
- What happens when a student's plan changes after purchase but before the event? The student is blocked from joining due to plan ineligibility. No automatic refund — handled by support on request.
- What happens when a student tries to purchase a free event? The request is rejected — free events do not require purchase.
- What happens when the auto-cancel job runs but a payment callback is processing simultaneously? The lock prevents both from modifying the purchase at the same time.
- What happens when a paid event is cancelled after purchases exist? Students cannot join (event status check fails). Refunds are handled manually through the admin refund flow.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: System MUST allow a student to initiate a purchase for a paid live event, creating a pending purchase with a 30-minute expiry.
- **FR-002**: System MUST reject purchase creation if the student already holds active access for the event.
- **FR-003**: System MUST reject purchase creation if the student already has a pending purchase for the event.
- **FR-004**: System MUST validate that the student is on an eligible plan before allowing purchase creation.
- **FR-005**: System MUST confirm payment atomically — updating purchase status, creating a sales invoice, and granting event access all succeed or all roll back.
- **FR-006**: System MUST never transition a purchase to "paid" unless both the invoice and access record are successfully created.
- **FR-007**: System MUST grant exactly one active access record per (player, event) pair.
- **FR-008**: System MUST check event access at join time for paid events using only the access record — never reading invoices or payment gateway state.
- **FR-009**: System MUST enforce plan eligibility at join time for all events (free and paid).
- **FR-010**: System MUST automatically cancel pending purchases whose expiry time has passed.
- **FR-011**: System MUST process refunds atomically — marking purchase as refunded, revoking access, and creating a credit note all succeed or all roll back.
- **FR-012**: System MUST protect all purchase and access mutations with a per-player-per-event lock to prevent race conditions (double purchase, duplicate callbacks, duplicate access).
- **FR-013**: System MUST ensure a single shared ERPNext Item (`LIVE-EVENT-ACCESS`) exists for all paid event invoices, creating it idempotently at migration and lazily before invoice creation. Event-specific details MUST be captured in the invoice line description.
- **FR-014**: ~~System MUST NOT delete an ERPNext Item when `is_paid` changes from 1 to 0.~~ (Superseded: no per-event items are created. Old per-event items remain for existing invoice references.)
- **FR-015**: System MUST create the sales invoice after payment confirmation (not at purchase creation).
- **FR-016**: System MUST provide an access state query that returns whether the student has access, whether payment is required, whether their plan is eligible, whether they can join, and the reason if they cannot.
- **FR-017**: The lock mechanism MUST have a time-to-live to automatically release if the holding process crashes.

### Key Entities

- **Live Challenge Event**: A scheduled live exam event. May be free or paid. Paid events have a price and currency. Has eligible plans and an exam time window.
- **Live Event Purchase**: Represents a single purchase transaction for a paid event. Tracks payment state (pending, paid, failed, cancelled, refunded), links to the player, event, and (after payment) the sales invoice. Auto-expires after 30 minutes if not paid.
- **Live Event Access**: The join entitlement. One active record per (player, event). Sole source of truth at join time for paid events. Linked to the originating purchase. Statuses: active, revoked, refunded.
- **ERPNext Item**: A single shared service item (`LIVE-EVENT-ACCESS`) used on all event purchase invoices. Event-specific details are in the invoice line description.
- **Sales Invoice**: Accounting document created after payment confirmation. For accounting only — never read at join time. Line item description includes event name and schedule for identification.
- **Credit Note**: Accounting document created during refund, linked to the original invoice. Reads item_code and description from the original invoice for backward compatibility.

### Entity Relationships

- All paid events share a single ERPNext Item (`LIVE-EVENT-ACCESS`).
- An Event has many Purchases and many Access records.
- A Purchase belongs to one Player and one Event; references one Sales Invoice (after payment).
- An Access record belongs to one Player and one Event; references one Purchase.
- A Credit Note is linked to one Sales Invoice (created during refund).
- Access never links directly to a Sales Invoice — always through the Purchase.

## Scope *(mandatory)*

### In Scope

- Single event purchase only (one student, one event)
- Purchase lifecycle: creation, payment confirmation, auto-cancellation, refund
- Atomic payment confirmation (purchase + invoice + access)
- Atomic refund (purchase + access + credit note)
- Access-based join gating for paid events
- Plan eligibility check for all events
- Access state query endpoint
- Shared ERPNext Item (`LIVE-EVENT-ACCESS`) for paid event invoices (idempotent)
- Concurrency protection via per-player-per-event locking with TTL
- Payment gateway as a placeholder for future integration

### Out of Scope

- Premium plan overrides
- Track/subject purchases
- Bundles or bulk event access
- Detailed refund policy (business approval side)
- Full voucher flow
- Full admin grant flow
- Plan change notifications or automatic refund on plan downgrade
- Frontend UI implementation (only API contracts defined)

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: Students can complete the purchase-to-access flow (buy, pay, gain access) in under 30 seconds after payment confirmation.
- **SC-002**: The join endpoint for paid events completes access checks without querying invoices or payment systems — access decision relies solely on the access record.
- **SC-003**: Concurrent duplicate payment callbacks for the same purchase never result in duplicate access records (0% duplication rate).
- **SC-004**: 100% of expired pending purchases (past 30-minute window) are automatically cancelled within one scheduled job cycle.
- **SC-005**: Refund operations are fully atomic — either all three artifacts (purchase status, access status, credit note) are updated, or none are.
- **SC-006**: Shared ERPNext Item creation is idempotent — calling `ensure_shared_live_event_item()` multiple times never creates duplicate items.
- **SC-007**: Students blocked from joining due to missing access or ineligible plan receive a clear, actionable reason in the response.

## Assumptions

- The payment gateway integration is a placeholder — the system provides a `confirm_payment` endpoint that will be called by the actual gateway integration in the future. The purchase flow does not depend on a specific gateway.
- Player/Customer mapping to ERPNext already exists (the system can look up the ERPNext customer for a given player).
- The existing join endpoint can be extended to add the access check without breaking current free-event behavior.
- Redis is available in the runtime environment for distributed locking.
- Scheduled jobs infrastructure exists for running the auto-cancel job periodically.
- ERPNext Item creation, Sales Invoice creation, and Credit Note creation are available via internal APIs.
- The `eligible_plans` field on the event already exists and is populated by admins.

## Dependencies

- ERPNext: Item creation, Sales Invoice creation, Credit Note creation APIs.
- Redis: Distributed locking for concurrency protection.
- Existing Live Challenge Event infrastructure: event creation, join flow, plan eligibility checks.
- Existing Player Profile: player identification and plan lookup.
- Scheduled job runner: for periodic auto-cancellation of expired purchases.
