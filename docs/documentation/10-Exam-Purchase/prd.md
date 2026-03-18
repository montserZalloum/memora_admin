# PRD — Single Live Event Purchase

## 1. Overview

This feature allows a student to purchase a single Live Event exam independently. If the event is paid (`is_paid = 1`), the student cannot join it unless they hold an active Event Access record for that event.

The core design principle is separation of concerns:

- **Sales Invoice** = accounting only
- **Purchase** = payment state
- **Event Access** = join entitlement

This ensures the join logic remains fast, deterministic, and does not depend on reading invoices or payment gateway state at join time.

---

## 2. Problem

Currently, `is_paid` exists on the event, but there is no clear, separated domain for:

- Creating a purchase process for a single event
- Linking the purchase to an ERPNext Sales Invoice
- Converting a successful payment into actual join entitlement
- Preventing reliance on Sales Invoice as source of truth for join access

---

## 3. Goal

Build a complete flow for purchasing a single Live Event such that:

- A purchase record can be created for an event
- Upon successful payment, an ERPNext Sales Invoice is issued
- Upon successful payment, the player is granted Event Access
- At join time:
  - If the event is free → check plan eligibility only
  - If the event is paid → must hold an active Live Event Access

---

## 4. Scope

### In Scope

- Single event purchase only
- Purchase record creation
- Linking purchase to ERPNext Sales Invoice (created after payment confirmation)
- Automatic ERPNext Item creation when a paid event is created
- Creating Memora Live Event Access after successful payment
- Blocking join without access if the event is paid
- Plan eligibility check for all events (free and paid)
- Automatic Credit Note creation on refund
- Auto-cancellation of pending purchases after 30 minutes
- Redis-based locking with TTL for concurrency protection
- Payment gateway as a placeholder for future integration

### Out of Scope

- Premium Plan
- Track/Subject purchases
- Bundles
- Detailed refund policy (business approval side)
- Full voucher flow
- Full admin grant flow
- Plan change notifications or automatic refund on plan downgrade

---

## 5. User Story

### Primary Story

> As a student, I want to purchase a single Live Event exam so that I can join it even if it is paid.

### Success Outcome

After successful payment:

- The student has an active access record for the event
- The student can join the event when it starts
- No invoice is read at join time

---

## 6. Business Rules

### Free Events (`is_paid = 0`)

- No purchase required
- No event access required
- Plan eligibility check still applies

### Paid Events (`is_paid = 1`)

- Cannot join unless an active `Memora Live Event Access` exists for `(player, event)`
- Plan eligibility check still applies
- Every paid event must have:
  - `price`
  - `currency`
  - `erpnext_item_code` (auto-created with the event)

### Purchase Constraints

- No duplicate active access per `(player, event)`
- No new purchase if:
  - Active access already exists for `(player, event)`
  - An open (pending) purchase already exists for `(player, event)`
- Pending purchases auto-cancel after **30 minutes**

### Invoice Rules

- Sales Invoice is **not** the source of truth for join access
- Sales Invoice is created **after** payment confirmation, not before
- Sales Invoice is for accounting purposes only

### Plan Eligibility

- Plan eligibility applies to **all events**, both free and paid
- If a student changes to a non-eligible plan after purchasing an event, they are blocked from joining
- No automatic refund on plan change — handled manually by support upon student request

---

## 7. Data Model

### 7.1 Memora Live Challenge Event

Fields relevant to this feature:

```
Memora Live Challenge Event
├── plan                      -> Study Plan
├── is_paid
├── price
├── currency
├── erpnext_item_code         -> Item (auto-created)
├── status
├── eligible_plans[]
├── exam_start_ts
└── exam_end_ts
```

**Notes:**

- `erpnext_item_code` is automatically created when a paid event is created
- Example: `LIVE-EVENT-LC-00042`

### 7.2 Memora Live Event Purchase

```
Memora Live Event Purchase
├── player                    -> Memora Player Profile
├── event                     -> Memora Live Challenge Event
├── plan_snapshot             -> Study Plan (name reference only)
├── status                    = pending | paid | failed | cancelled | refunded
├── amount
├── currency
├── erpnext_item_code         -> Item
├── erpnext_invoice           -> Sales Invoice (set after payment)
├── payment_gateway           (placeholder)
├── payment_reference         (placeholder)
├── created_at
├── paid_at                   (optional)
├── refunded_at               (optional)
├── expires_at                (auto-set to created_at + 30 minutes)
└── notes                     (optional)
```

**Purpose:** Represents the purchase transaction, payment state, and accounting linkage.

**Auto-cancellation:** If `status = pending` and `expires_at` has passed, the system automatically transitions to `status = cancelled`.

### 7.3 Memora Live Event Access

```
Memora Live Event Access
├── player                    -> Memora Player Profile
├── event                     -> Memora Live Challenge Event
├── status                    = active | revoked | refunded
├── access_type               = purchase
├── purchase_ref              -> Memora Live Event Purchase
├── granted_at
├── revoked_at                (optional, set when status = revoked)
├── refunded_at               (optional, set when status = refunded)
└── notes                     (optional)
```

**Purpose:** The actual join entitlement. This is the **sole source of truth** checked at join time for paid events.

**Note:** No `expires_at` on Access. The event itself has `exam_start_ts` and `exam_end_ts` which control timing. Access only answers: "has this student paid?"

---

## 8. Relationship Model

```
Live Event
├── has one ERP Item (auto-created)
├── has many Purchases
└── has many Access records

Live Event Purchase
├── belongs to one Player
├── belongs to one Event
└── references one Sales Invoice (after payment)

Live Event Access
├── belongs to one Player
├── belongs to one Event
└── references one Purchase
```

---

## 9. Purchase Flow

### 9.1 Create Purchase

When the student clicks "Buy":

1. Validate:
   - Event exists
   - Event is paid
   - Student is on an eligible plan
   - No active access already exists for `(player, event)`
   - No pending purchase already exists for `(player, event)`
2. Acquire Redis lock: `memora:live_event_purchase:{player}:{event}` with TTL
3. Re-validate inside lock
4. Create `Memora Live Event Purchase` with:
   - `status = pending`
   - `expires_at = now + 30 minutes`
5. Release lock
6. Return purchase details and payment instructions to frontend

### 9.2 Confirm Payment

When payment is confirmed (via gateway callback or webhook — placeholder for now):

1. Acquire Redis lock: `memora:live_event_purchase:{player}:{event}` with TTL
2. Re-validate:
   - Purchase exists and is still pending
   - No active access already exists
3. **Begin atomic transaction** — all of the following must succeed together, or all roll back:
   1. Update `purchase.status = paid`
   2. Set `purchase.paid_at`
   3. Create ERPNext Sales Invoice linked to:
      - Player/Customer
      - Event Item
      - Event Price
   4. Set `purchase.erpnext_invoice`
   5. Create `Memora Live Event Access` with:
      - `status = active`
      - `access_type = purchase`
      - `purchase_ref = purchase`
      - `granted_at = now`
4. **Commit transaction**
5. Release lock

**Failure handling:** If any step inside the transaction fails (e.g., invoice creation fails or access creation fails), the entire transaction rolls back. The purchase remains `pending`, no invoice is created, and no access is granted. The payment confirmation can be retried safely.

**Important:** `purchase.status` never becomes `paid` unless the invoice AND access are both successfully created. There is no intermediate state.

### 9.3 Auto-Cancel Expired Purchases

A scheduled job runs periodically:

1. Find all purchases where `status = pending` and `expires_at < now`
2. Transition each to `status = cancelled`

### 9.4 Event Join

When a student hits `/join`:

1. Check event status (active, not ended, not cancelled)
2. Check plan eligibility (applies to **all** events)
3. If `event.is_paid = 1`:
   - Check for `Memora Live Event Access.status = active` for `(player, event)`
   - If found → proceed with atomic join
   - If not found → return "payment required / no access"
4. If `event.is_paid = 0`:
   - Proceed with atomic join

---

## 10. ERPNext Integration

### Item Strategy

- Every paid event gets an **automatically created** ERPNext Item
- Item is created when the event is created/saved with `is_paid = 1`
- Example: `LIVE-EVENT-LC-00042`
- The admin does not need to manually create items

**Idempotency rules:**

- If the event is saved multiple times, the item is created **only once** — subsequent saves skip creation if the item already exists
- If `is_paid` changes from `0` to `1` → create the item (once)
- If `is_paid` changes from `1` to `0` → do **not** delete the item (it may be referenced by existing invoices)
- Before creating, always check if the item already exists by `erpnext_item_code`

### Invoice Strategy

- Sales Invoice is created **after** payment confirmation (not at purchase creation)
- Invoice is linked to:
  - Player/Customer
  - Event Item
  - Event Price

### Credit Note Strategy

- When a refund is approved, the system **automatically** creates a Credit Note linked to the original Sales Invoice
- This is part of the atomic refund operation

### Linkage Rule

```
Live Event Access -> Live Event Purchase -> Sales Invoice
```

Never:

```
Live Event Access -> Sales Invoice (directly)
```

---

## 11. Refund Handling

When a refund is approved, the system performs the following **atomically**:

1. Update `Memora Live Event Purchase`:
   - `status = refunded`
   - `refunded_at = now`
2. Update `Memora Live Event Access`:
   - `status = refunded`
   - `refunded_at = now`
3. Create ERPNext Credit Note linked to the original Sales Invoice

**Important:** The system does not decide refund eligibility from a policy perspective. It only executes the technical cascade after a refund is approved.

---

## 12. Performance Requirements

### Hot Path Rule

At join time, the system:

- Does **NOT** read Sales Invoice
- Does **NOT** check payment gateway
- Does **NOT** infer from purchase history

It checks **only**:

1. Event status
2. Plan eligibility
3. Direct event access record (for paid events)

### Why

To keep the join endpoint:

- Fast
- Deterministic
- Minimal queries
- Capable of handling high load

---

## 13. Concurrency / Race Conditions

### Lock Mechanism

All sensitive operations are protected by a **Redis lock** with TTL:

```
Key: memora:live_event_purchase:{player}:{event}
```

### TTL

The lock has a TTL to ensure automatic release if the process crashes or hangs.

### Used In

- Create purchase
- Confirm payment
- Create access

### Sequence

1. Acquire Redis lock with TTL
2. Re-check access / open purchase
3. Create / update records
4. Release lock

### Prevents

- Double purchase
- Double callback processing
- Duplicate access creation

---

## 14. Validation Rules

### 14.1 Memora Live Event Purchase

- `player` required
- `event` required
- `status` in `{pending, paid, failed, cancelled, refunded}`
- `amount > 0`
- `currency` required
- `erpnext_item_code` required
- `created_at` required
- `expires_at` required (auto-set to `created_at + 30 minutes`)
- If `status = paid`:
  - `paid_at` required
  - `erpnext_invoice` required
- If `status = refunded`:
  - `refunded_at` required

### 14.2 Memora Live Event Access

- `player` required
- `event` required
- `status` in `{active, revoked, refunded}`
- `access_type = purchase`
- `purchase_ref` required
- `granted_at` required
- If `status = revoked`:
  - `revoked_at` required
- If `status = refunded`:
  - `refunded_at` required

---

## 15. Constraints & Indexes

### 15.1 Memora Live Event Access

**Constraint:**

- One active access per `(player, event)`

**Indexes:**

- `(player, event)`
- `(event, status)`
- `(player, status)`

### 15.2 Memora Live Event Purchase

**Constraints:**

- No duplicate open (pending) purchase per `(player, event)`
- Reject create if active access already exists

**Indexes:**

- `(player, event)`
- `(status)`
- `(erpnext_invoice)`
- `(payment_reference)`
- `(created_at)`
- `(expires_at, status)` — for the auto-cancel scheduled job

---

## 16. API Surface

### Player-facing

| Endpoint | Description |
|---|---|
| `create_live_event_purchase(event_id)` | Initiate a purchase for a paid event |
| `get_live_event_access_state(event_id)` | Check player's access state for an event (see response schema below) |
| `join_live_event(event_id)` | Join a live event |

**`get_live_event_access_state` response schema:**

```json
{
  "has_direct_access": true,
  "requires_payment": true,
  "is_eligible_plan": true,
  "can_join": true,
  "reason": null
}
```

- `has_direct_access` — player holds an active Event Access for this event
- `requires_payment` — event is paid (`is_paid = 1`)
- `is_eligible_plan` — player's current plan is in the event's eligible plans
- `can_join` — final computed result: can the player join right now?
- `reason` — if `can_join = false`, a human-readable reason (e.g., `"payment_required"`, `"plan_not_eligible"`, `"event_ended"`)

This schema is designed to be extensible for future additions (e.g., `has_premium_override`, `has_voucher_access`) without breaking existing clients.

### Internal / Webhook (Placeholder)

| Endpoint | Description |
|---|---|
| `confirm_live_event_payment(purchase_id, payment_reference)` | Confirm payment and grant access |

### Admin / Internal

| Endpoint | Description |
|---|---|
| `refund_live_event_purchase(purchase_id)` | Process refund with automatic cascade |

---

## 17. UX Expectations

### Event Card / Event Details

If `is_paid = 1`:

- Show price
- Show "Buy" button if no access exists
- Show "Purchased" or "Ready to join" if access exists

### After Payment Success

UI immediately reflects:

- "You own this event"
- or "Ready to join"

### Join Failure Cases

- Event not eligible by current plan (free or paid)
- Event paid and no access
- Event ended / cancelled
- Event full

---

## 18. Acceptance Criteria

| Scenario | Expected Result |
|---|---|
| Paid event, student has not paid | Cannot join |
| Payment succeeds | Purchase = paid, Access = active, Invoice created, student can join |
| Payment fails | No access created |
| Duplicate callback received | No duplicate access created (Redis lock) |
| Student tries to buy event they already own | Request rejected |
| Refund approved | Access = refunded, Credit Note created, student cannot join |
| Join endpoint | Does not read invoice at execution time |
| Pending purchase older than 30 minutes | Auto-cancelled, student can start new purchase |
| Free event, student not on eligible plan | Cannot join |
| Paid event, student changes to non-eligible plan after purchase | Cannot join, manual refund upon request |
| Paid event created by admin | ERPNext Item auto-created |
| Paid event saved multiple times | ERPNext Item created only once (idempotent) |
| Invoice creation fails during payment confirmation | Entire transaction rolls back, purchase stays pending, no access granted |
| Event changed from free to paid | ERPNext Item created |
| Event changed from paid to free | ERPNext Item is NOT deleted |

---

## 19. Non-Goals / Future Extensions

Not in scope for this PRD, but the design accommodates future addition of:

- Voucher-based event access
- Admin grant event access
- Premium plan override
- Bundles
- Bulk event access
- Event pass per track/subject
- Automatic refund on plan downgrade
- Internal notification when a paying student downgrades plan

---

## 20. Final Design Principle

```
Sales Invoice  = accounting only       (created after payment)
Purchase       = payment state         (created before payment, auto-cancels after 30 min)
Event Access   = join entitlement      (sole source of truth at join time)
```