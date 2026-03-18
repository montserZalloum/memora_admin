# Feature Specification: Monetized Access

**Feature Branch**: `051-monetized-access`
**Created**: 2026-03-18
**Status**: Draft
**Input**: PRD: `/home/corex/aurevia-bench/apps/memora_admin/docs/documentation/09-Premium-Player/PRD.md`

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Player Purchases Plan Premium (Priority: P1)

A player enrolled in a Study Plan wants to unlock all premium content inside that plan for the remainder of the Season. They initiate a one-time purchase, complete payment, and immediately gain access to all gated content within their plan.

**Why this priority**: This is the primary monetization path. Plan Premium is the broadest access entitlement — it unlocks everything in a plan including paid live events. Without this, the feature delivers no revenue from plan content.

**Independent Test**: Can be fully tested by having a player initiate a purchase, complete payment, and verify that gated content within the plan becomes accessible. Delivers the core "pay once, unlock all" value proposition.

**Acceptance Scenarios**:

1. **Given** a player is enrolled in a plan with premium content, **When** they initiate a purchase, complete payment, and the webhook confirms payment, **Then** a Plan Premium entitlement is created with `status = active` and `source_type = purchase`, and all gated content within the plan is accessible.
2. **Given** a player already holds a usable Plan Premium for a plan, **When** they attempt to purchase another premium for the same plan, **Then** the system rejects the request with a clear message.
3. **Given** a player has a pending purchase for a plan, **When** they attempt to create another purchase for the same plan, **Then** the system rejects the duplicate.
4. **Given** a player completed payment and the webhook fires twice, **When** the second webhook is processed, **Then** no duplicate premium is created and no error is returned.

---

### User Story 2 - Player Accesses a Paid Live Event (Priority: P1)

A player wants to join a paid live challenge event. If they already hold a usable Plan Premium for the event's plan, they join directly without purchasing a ticket. If not, they must purchase an event ticket or redeem a voucher to gain access.

**Why this priority**: Paid live events are the second monetization path and interact directly with Plan Premium (premium bypasses paid gates). This story validates the access resolution logic that is central to the entire feature.

**Independent Test**: Can be tested by configuring a paid event, having one player with a Plan Premium join (bypasses payment), and another player without premium purchase a ticket and join. Delivers the "paid event" value proposition.

**Acceptance Scenarios**:

1. **Given** a paid live event and a player with a usable Plan Premium for that event's plan, **When** the player attempts to join, **Then** they are allowed without purchasing a separate ticket.
2. **Given** a paid live event and a player without a Plan Premium, **When** the player purchases a ticket and payment is confirmed, **Then** a Live Event Access entitlement is created and the player can join.
3. **Given** a paid live event and a player with a usable Plan Premium, **When** the player attempts to buy an event ticket, **Then** the system rejects the purchase (prevents double-charging).
4. **Given** a free live event (`is_paid = 0`), **When** any eligible player attempts to join, **Then** normal access rules apply with no payment check.

---

### User Story 3 - Player Redeems a Voucher (Priority: P2)

A player receives a promotional voucher code that grants either a Plan Premium or Live Event Access. They enter the code and the corresponding entitlement is created without going through the payment gateway.

**Why this priority**: Vouchers support promotional campaigns and are the non-payment path to entitlements. Important for marketing but not the core revenue flow.

**Independent Test**: Can be tested by creating a voucher card, having a player redeem the code, and verifying the entitlement is created. Delivers promotional access granting capability.

**Acceptance Scenarios**:

1. **Given** an active plan_premium voucher with remaining redemptions, **When** a player redeems the code, **Then** a Voucher Redemption and Plan Premium entitlement are created atomically.
2. **Given** an active live_event_access voucher, **When** a player redeems the code, **Then** a Voucher Redemption and Live Event Access entitlement are created atomically.
3. **Given** a voucher that has reached its `max_redemptions` limit, **When** a player attempts to redeem it, **Then** the system rejects the redemption.
4. **Given** a voucher with `valid_until` in the past, **When** a player attempts to redeem it, **Then** the system rejects with an expiry message.
5. **Given** a player who has already successfully redeemed a specific voucher card, **When** they attempt to redeem the same code again, **Then** the system rejects the duplicate.
6. **Given** a plan_premium voucher and a player who already holds a usable premium for the target plan, **When** they attempt to redeem, **Then** the system rejects to prevent duplication.

---

### User Story 4 - Admin Grants or Revokes Entitlements (Priority: P2)

An administrator needs to manually grant Plan Premium or Live Event Access to a player (e.g., for a support case or promotional deal), or revoke an existing entitlement.

**Why this priority**: Essential for support operations and exception handling, but not the primary player-facing flow.

**Independent Test**: Can be tested by an admin granting premium to a player, verifying access, then revoking it and verifying access is denied. Delivers administrative control over entitlements.

**Acceptance Scenarios**:

1. **Given** a player without premium on a plan, **When** an admin grants premium, **Then** a Plan Premium is created with `source_type = admin` and `granted_by` set to the admin user.
2. **Given** a player with an active premium, **When** an admin revokes it, **Then** the premium `status` becomes `revoked` and `revoked_at` is set, and access checks fail.
3. **Given** a player with an active event access, **When** an admin revokes it, **Then** the access `status` becomes `revoked` and the player can no longer join the event.

---

### User Story 5 - Admin Processes a Refund (Priority: P2)

An administrator approves a refund for a Plan Premium Purchase or Live Event Purchase. The system atomically marks the purchase as refunded and revokes the linked entitlement.

**Why this priority**: Refunds are critical for customer trust and financial integrity, but are a reactive flow, not the primary purchase path.

**Independent Test**: Can be tested by completing a purchase, then processing a refund and verifying both the purchase status and entitlement status are updated atomically. Delivers refund safety guarantees.

**Acceptance Scenarios**:

1. **Given** a paid Plan Premium Purchase with a linked active premium, **When** the admin processes a refund, **Then** within a single transaction the purchase becomes `refunded` and the premium becomes `revoked`.
2. **Given** a paid Live Event Purchase with a linked active access, **When** the admin processes a refund, **Then** within a single transaction the purchase becomes `refunded` and the access becomes `refunded`.
3. **Given** a refund has been processed, **When** the player checks their access, **Then** the previously accessible content is no longer available.

---

### User Story 6 - Plan Change Impacts Premium Usability (Priority: P3)

A player who holds a Plan Premium changes their Study Plan. Their premium automatically becomes unusable for content in the new plan (without changing the stored status). If they change back to the original plan within the season, the premium becomes usable again.

**Why this priority**: This is a consequence of the computed-validity design. Important for correctness but not a standalone user action.

**Independent Test**: Can be tested by granting premium, changing plan (verify access denied), changing back (verify access restored). Validates the computed-validity model.

**Acceptance Scenarios**:

1. **Given** a player with an active Plan Premium on Plan A, **When** they switch to Plan B, **Then** the premium record stays `active` but access checks for Plan A content return unusable with reason `plan_mismatch`.
2. **Given** a player who previously switched away from Plan A, **When** they switch back to Plan A within the season, **Then** their existing premium becomes usable again.
3. **Given** a player with an active Plan Premium, **When** the season ends, **Then** the premium record stays `active` but access checks return unusable with reason `season_ended`.

---

### User Story 7 - Player Checks Access State (Priority: P3)

A player views their current access status for a plan or live event. The system returns a complete access state in a single response so the frontend can render the appropriate UI (e.g., "Premium Active", "Buy Ticket", "Already Purchased").

**Why this priority**: Supports the frontend rendering but is a read-only query, not a transactional flow.

**Independent Test**: Can be tested by querying the access state endpoint for various player states and verifying the response matches expectations. Delivers UI rendering support.

**Acceptance Scenarios**:

1. **Given** a player with a usable Plan Premium, **When** they query plan access state, **Then** the response includes `has_usable_premium = true`, `season_end`, and `source_type`.
2. **Given** a player without premium querying a paid event, **When** they query event access state, **Then** the response indicates no access and `is_covered_by_premium = false`.
3. **Given** a player with a Plan Premium querying a paid event under the same plan, **When** they query event access state, **Then** the response indicates access via premium with `is_covered_by_premium = true`.

---

### Edge Cases

- What happens when two players simultaneously attempt to redeem the last use of a single-use voucher? Only one succeeds; the other receives a "voucher exhausted" rejection.
- What happens when a payment webhook arrives after the player's purchase has been manually cancelled by an admin? The idempotency check prevents creating a premium if the purchase status is no longer `pending`.
- What happens when the distributed lock system is temporarily unavailable during a purchase flow? A secondary safety mechanism prevents duplicate entitlements, and the system returns an appropriate error.
- What happens when a player attempts to redeem a `live_event_access` voucher for an event they already have access to via Plan Premium? The system rejects the redemption to prevent unnecessary entitlements.
- What happens when a season ends while a purchase is in `pending` status? The purchase can still be completed (webhook arrives), but the created premium will immediately be computed as unusable. The system does not block this — refund handling is a support decision.
- What happens when an admin grants premium to a player who already has a voucher-sourced premium on the same plan? The system rejects the grant due to the one-active-premium-per-(player, plan) constraint.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: System MUST allow a player to purchase a one-time Plan Premium that unlocks all gated content within a specific Study Plan for the remainder of the Season.
- **FR-002**: System MUST compute Plan Premium validity at runtime from three conditions: premium status is `active`, premium plan matches player's current plan, and current time is within the season's `end_at`.
- **FR-003**: System MUST centralize all premium usability checks in a single service function that returns structured results including usability status, reason, premium reference, season end date, and source type. No other code path may re-implement this logic.
- **FR-004**: System MUST enforce at most one active Plan Premium per (player, plan) combination, preventing duplicates even under concurrent requests.
- **FR-005**: System MUST support three sources for Plan Premium creation: payment gateway purchase, voucher redemption, and admin grant — each with appropriate reference fields.
- **FR-006**: System MUST allow individual paid live events to be gated behind a ticket purchase, with a usable Plan Premium automatically bypassing the event-level paid gate.
- **FR-007**: System MUST reject event ticket purchases when the player already holds a usable Plan Premium covering that event, to prevent double-charging.
- **FR-008**: System MUST enforce at most one active Live Event Access per (player, event) combination, preventing duplicates even under concurrent requests.
- **FR-009**: System MUST support voucher cards that grant either Plan Premium or Live Event Access, with configurable maximum redemptions, optional expiry date, and active/inactive toggle.
- **FR-010**: System MUST enforce one successful redemption per voucher card per player, and total successful redemptions must not exceed the voucher's `max_redemptions`.
- **FR-011**: System MUST perform voucher redemption and entitlement creation atomically — either both succeed or neither is persisted.
- **FR-012**: System MUST process refunds atomically — marking the purchase as `refunded` and the linked entitlement as `revoked`/`refunded` within a single transaction.
- **FR-013**: System MUST preserve Plan Premium records with `status = active` when a player changes plans or a season ends — status is only modified by explicit business actions (admin revoke, refund cascade), never by time or plan changes.
- **FR-014**: System MUST provide single-call access state queries for plan access and event access that return everything the frontend needs for UI rendering in one response.
- **FR-015**: System MUST enforce that the event join operation performs its own full access check regardless of frontend state, serving as the source-of-truth gate.
- **FR-016**: System MUST handle duplicate payment webhooks idempotently — if the entitlement already exists, skip creation silently without error.
- **FR-017**: System MUST create a corresponding accounting invoice for every purchase, referenced by the purchase document but never used for access checks.
- **FR-018**: System MUST separate financial records (Purchase, Invoice) from access entitlements (Premium, Event Access) — access checks must never query purchase or invoice documents.
- **FR-019**: System MUST enforce source-type-specific field requirements: `purchase_ref` required for purchase-sourced, `voucher_ref` required for voucher-sourced, `granted_by` required for admin-sourced entitlements.
- **FR-020**: System MUST restrict player-facing reads to own records only, hide raw financial documents from players, and reserve grant/revoke/refund actions for administrators.

### Key Entities

- **Memora Plan Premium**: The entitlement proving a player has premium access to a specific plan. Linked to a player and plan, with source tracking (purchase/voucher/admin). Validity is computed, not stored.
- **Memora Plan Premium Purchase**: The financial record of a player's payment for plan premium. Tracks payment lifecycle (pending → paid → refunded), references the sellable item and accounting invoice.
- **Memora Live Event Access**: The entitlement proving a player can join a specific paid event. Linked to a player and event, with source tracking.
- **Memora Live Event Purchase**: The financial record of a player's ticket payment for a live event. Same lifecycle pattern as Plan Premium Purchase.
- **Memora Voucher Card**: A redeemable code/card asset that grants either a Plan Premium or Live Event Access. Has usage limits, optional expiry, and active toggle.
- **Memora Voucher Redemption**: The event record of a player redeeming a voucher. Links the voucher card to the created entitlement.
- **Memora Live Challenge Event** (existing, extended): Extended with paid/free toggle, price, currency, and sellable item reference for monetization.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: Players can complete a plan premium purchase (from initiation to access confirmation) in under 60 seconds, excluding payment gateway time.
- **SC-002**: Access resolution for any gated resource completes in under 50 milliseconds (p95), ensuring no perceptible delay when accessing content or joining events.
- **SC-003**: Zero duplicate entitlements occur across all creation paths (purchase, voucher, admin) under concurrent usage.
- **SC-004**: Refund processing (from admin approval to entitlement revocation) completes in under 5 seconds on the system side.
- **SC-005**: Voucher redemption (from code submission to entitlement creation) completes in under 3 seconds.
- **SC-006**: All payment webhook duplicates are handled silently without creating duplicate entitlements or returning errors.
- **SC-007**: Players who change plans and change back within a season regain access to their existing premium without any manual intervention.
- **SC-008**: Access state endpoints return all data needed for frontend rendering in a single call, with no need for the frontend to assemble state from multiple requests.
- **SC-009**: Financial records (invoices, purchase documents) are never queried during access resolution, maintaining strict separation of concerns.

## Assumptions

- Existing sellable item and accounting invoice capabilities are available and functional for integration.
- A distributed locking mechanism is available for concurrency control; temporary unavailability is handled by a secondary data-level safety net.
- The platform's permission system supports the required read-own-records filtering pattern.
- Payment gateway integration details (specific gateway, webhook format) will be determined during planning — the feature design is gateway-agnostic.
- Voucher codes will be alphanumeric strings; the specific format (human-readable vs UUID) is a configuration decision that does not affect the feature's behavior.
- Maximum pricing limits, if any, will be enforced by admin policy rather than hard-coded system constraints.
- The existing plan-change logic already handles removal of event-related records; this feature hooks into that existing mechanism.
- Currency is determined per-transaction (single currency per purchase); multi-currency conversion is not in scope.
