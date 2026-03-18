# Memora — Monetized Access Feature

## Product Requirements Document (PRD)

| Field             | Value                                    |
|-------------------|------------------------------------------|
| **Document Owner**| Memora Product Team                      |
| **Status**        | Final Draft                              |
| **Created**       | 2026-03-18                               |
| **Last Updated**  | 2026-03-18                               |
| **Target Release**| TBD                                      |
| **Stack**         | Frappe Framework / ERPNext / Python / Redis |

---

## Table of Contents

1. [Overview](#1-overview)
2. [Goals & Success Metrics](#2-goals--success-metrics)
3. [Core References & Dependencies](#3-core-references--dependencies)
4. [Domain 1 — Plan Premium](#4-domain-1--plan-premium)
5. [Domain 2 — Paid Live Events](#5-domain-2--paid-live-events)
6. [Domain 3 — Voucher System](#6-domain-3--voucher-system)
7. [Domain 4 — ERP / Accounting Layer](#7-domain-4--erp--accounting-layer)
8. [Runtime Access Resolution](#8-runtime-access-resolution)
9. [Creation Flows](#9-creation-flows)
10. [Refund & Revocation Flows](#10-refund--revocation-flows)
11. [Prevention & Validation Rules](#11-prevention--validation-rules)
12. [Plan Change Interaction](#12-plan-change-interaction)
13. [Concurrency & Race Condition Handling](#13-concurrency--race-condition-handling)
14. [API Surface](#14-api-surface)
15. [Permission Model](#15-permission-model)
16. [Source of Truth Summary](#16-source-of-truth-summary)
17. [Testing Strategy](#17-testing-strategy)
18. [Out of Scope](#18-out-of-scope)
19. [Open Questions](#19-open-questions)
20. [Appendix — Entity Relationship Summary](#20-appendix--entity-relationship-summary)

---

## 1. Overview

### 1.1 What This Feature Is

Memora is an educational platform where players follow Study Plans organized by Seasons. Today, all content inside a plan is free once a player enrolls. This feature introduces **monetized access** — the ability to sell premium plan-wide unlocks and charge for individual live challenge events.

The feature introduces three monetization paths:

- **Plan Premium**: a one-time purchase that unlocks everything inside a specific Study Plan for the remainder of its Season.
- **Paid Live Events**: individual live challenge events that require a ticket (purchased or granted) to join.
- **Voucher Redemption**: promotional codes that grant either a Plan Premium or a Live Event Access without going through the payment gateway.

### 1.2 What This Feature Is NOT

- Not a subscription system — there are no recurring charges.
- Not a general paywall engine — access is scoped to plans and events only.
- Not a storefront — there is no catalog or cart; each purchase is a single targeted action.
- Not a replacement for existing free content — free plans and free events continue to work exactly as before.

### 1.3 Design Principles

1. **Separation of concerns**: financial records (Purchase, Invoice) are strictly separated from access entitlements (Premium, Event Access). A purchase proves payment happened; an entitlement proves the player has access.
2. **Computed validity over stored expiry**: Plan Premium has no `expires_at` field. Validity is computed at runtime from the Season's `end_at` and the player's current plan. This avoids cron jobs and stale flags.
3. **Single source of truth per concern**: each document type owns exactly one concern (see Section 16).
4. **Performance first**: hot-path checks (access resolution) must be lightweight. Heavy operations (invoice creation, voucher validation) happen off the hot path.
5. **No stacking, no duplicates**: a player can hold at most one usable Premium per plan and one active Access per event. No exceptions for regular users.

---

## 2. Goals & Success Metrics

### 2.1 Business Goals

- Enable revenue generation from premium plan content.
- Enable revenue generation from paid live challenge events.
- Support promotional campaigns via voucher distribution.
- Maintain full financial auditability through ERPNext integration.

### 2.2 Success Metrics

| Metric                              | Target             |
|--------------------------------------|--------------------|
| Premium purchase conversion rate     | Tracked post-launch |
| Paid event ticket sell-through rate  | Tracked post-launch |
| Voucher redemption rate              | Tracked post-launch |
| Access resolution latency (p95)      | < 50ms             |
| Duplicate entitlement incidents      | Zero                |
| Refund processing time               | < 5 seconds (system side) |

---

## 3. Core References & Dependencies

These are existing entities that the Monetized Access feature depends on. They are **not created or modified** by this feature.

### 3.1 Memora Player Profile

The user entity. Every access entitlement is scoped to a player. The critical field is `current_plan`, which links a player to their active Study Plan.

### 3.2 Study Plan

A structured learning path. Each plan belongs to exactly one Season. Plans contain content (lessons, quizzes, etc.) and may host Live Challenge Events. A plan is the **scope boundary** for Plan Premium — premium unlocks everything inside the plan.

### 3.3 Season

A time-bounded period defined by `start_at` and `end_at`. Seasons govern the temporal validity of Plan Premiums. When a season ends, all premiums tied to plans in that season become unusable — no cron job needed.

### 3.4 ERPNext Item

Each sellable thing in the system has a dedicated ERPNext Item. This means:

- One Item for each plan's premium product.
- One Item for each paid live event.

Items are created by administrators before the product becomes available for sale.

### 3.5 ERPNext Sales Invoice

The accounting record for a completed sale. Created during the purchase flow, referenced by Purchase documents. The Sales Invoice is **never** used as a source of truth for access — it exists purely for accounting and financial reporting.

---

## 4. Domain 1 — Plan Premium

### 4.1 Memora Plan Premium (DocType)

The entitlement record that proves a player has premium access to a specific plan.

| Field          | Type     | Description                                                  |
|----------------|----------|--------------------------------------------------------------|
| `player`       | Link     | → Memora Player Profile                                     |
| `plan`         | Link     | → Study Plan                                                |
| `status`       | Select   | `active` \| `revoked`                                       |
| `source_type`  | Select   | `purchase` \| `voucher` \| `admin`                          |
| `purchase_ref` | Link     | → Memora Plan Premium Purchase *(optional)*                  |
| `voucher_ref`  | Link     | → Memora Voucher Redemption *(optional)*                     |
| `granted_by`   | Link     | → User *(optional, required when source_type = admin)*       |
| `granted_at`   | Datetime | When the premium was created                                 |
| `revoked_at`   | Datetime | When the premium was revoked *(optional)*                    |
| `notes`        | Text     | Free-text notes *(optional)*                                 |

**Unique constraint**: At most one record with `status = active` per `(player, plan)` combination.

### 4.2 Memora Plan Premium Purchase (DocType)

The financial record tracking a player's payment for plan premium.

| Field                 | Type     | Description                                          |
|-----------------------|----------|------------------------------------------------------|
| `player`              | Link     | → Memora Player Profile                             |
| `plan`                | Link     | → Study Plan                                        |
| `status`              | Select   | `pending` \| `paid` \| `failed` \| `cancelled` \| `refunded` |
| `amount`              | Currency | Price charged                                        |
| `currency`            | Link     | Currency code                                        |
| `erpnext_item_code`   | Link     | → Item                                              |
| `erpnext_invoice`     | Link     | → Sales Invoice                                     |
| `payment_gateway`     | Data     | Gateway identifier *(optional)*                      |
| `payment_reference`   | Data     | External transaction ID *(optional)*                 |
| `created_at`          | Datetime | When the purchase was initiated                      |
| `paid_at`             | Datetime | When payment was confirmed *(required if status = paid)* |
| `refunded_at`         | Datetime | When refund was processed *(required if status = refunded)* |
| `notes`               | Text     | Free-text notes *(optional)*                         |

### 4.3 What Plan Premium Means

- Premium is scoped to **one plan only**.
- Premium unlocks **everything** inside that plan — all gated content, all paid events belonging to that plan.
- Premium **overrides** any individual paid gate inside the plan.
- Premium **includes future content** added to the plan during the season.
- Premium **does not transfer** to another plan.
- Premium **becomes unusable** if the player changes to a different plan.

### 4.4 Plan Premium Validity (Computed)

There is **no `expires_at` field** stored on the premium. Validity is computed at runtime. A premium is **usable** if and only if all three conditions hold:

1. `premium.status == active`
2. `premium.plan == player.current_plan`
3. `now() <= premium.plan.season.end_at`

This computation must be centralized in a single service helper (see Section 4.5).

### 4.5 Usability Service Helper

All premium usability checks **must** go through a single centralized function. No other code path should re-implement this logic.

```
is_plan_premium_usable(player_id, plan_id=None) -> dict
```

**Returns:**

```python
{
    "usable": True | False,
    "reason": None | "no_active_premium" | "plan_mismatch" | "season_ended" | "revoked",
    "premium_name": "MPP-00001" | None,
    "season_end": "2026-06-30" | None,
    "source_type": "purchase" | "voucher" | "admin" | None
}
```

**Design decisions:**

- Returns a dict, not a bool — callers need the reason for UX messaging and the `premium_name` for downstream operations.
- If `plan_id` is `None`, defaults to `player.current_plan`.
- This function is the **only** place where the three validity conditions are evaluated.
- Future optimization: this function can be backed by a short-TTL Redis cache (30–60 seconds) without changing the interface.

### 4.6 Plan Premium State Rules

**Stored statuses:**

| Status     | Meaning                          |
|------------|----------------------------------|
| `active`   | Premium was granted and not revoked |
| `revoked`  | Premium was manually revoked by an admin, or cascade-revoked due to refund |

**Not stored as status (computed at runtime):**

| Condition                | Meaning                                         |
|--------------------------|-------------------------------------------------|
| Season ended             | Premium is unusable but record stays `active`   |
| Player changed plan      | Premium is unusable but record stays `active`   |

The `status` field is only modified by explicit business actions (admin revoke, refund cascade). It is **never** modified by time passing or by the player switching plans.

---

## 5. Domain 2 — Paid Live Events

### 5.1 Memora Live Challenge Event (DocType)

An existing DocType extended with monetization fields.

| Field               | Type     | Description                                          |
|---------------------|----------|------------------------------------------------------|
| `event_name`        | Data     | Display name                                         |
| `status`            | Select   | Event lifecycle status                               |
| `plan`              | Link     | → Study Plan                                        |
| `is_paid`           | Check    | Whether this event requires payment (0 = free, 1 = paid) |
| `price`             | Currency | Ticket price *(required if is_paid = 1)*             |
| `currency`          | Link     | Currency code                                        |
| `erpnext_item_code` | Link     | → Item *(required if is_paid = 1)*                  |
| `eligible_plans`    | Table    | List of Study Plans whose players may join           |
| `capacity`          | Int      | Maximum number of participants                       |
| `exam_start_ts`     | Datetime | Exam window start                                    |
| `exam_end_ts`       | Datetime | Exam window end                                      |

### 5.2 Memora Live Event Purchase (DocType)

| Field                 | Type     | Description                                          |
|-----------------------|----------|------------------------------------------------------|
| `player`              | Link     | → Memora Player Profile                             |
| `event`               | Link     | → Memora Live Challenge Event                       |
| `plan_snapshot`       | Link     | → Study Plan (player's plan at time of purchase)     |
| `status`              | Select   | `pending` \| `paid` \| `failed` \| `cancelled` \| `refunded` |
| `amount`              | Currency | Price charged                                        |
| `currency`            | Link     | Currency code                                        |
| `erpnext_item_code`   | Link     | → Item                                              |
| `erpnext_invoice`     | Link     | → Sales Invoice                                     |
| `payment_gateway`     | Data     | *(optional)*                                         |
| `payment_reference`   | Data     | *(optional)*                                         |
| `created_at`          | Datetime | When the purchase was initiated                      |
| `paid_at`             | Datetime | *(required if status = paid)*                        |
| `refunded_at`         | Datetime | *(required if status = refunded)*                    |
| `notes`               | Text     | *(optional)*                                         |

### 5.3 Memora Live Event Access (DocType)

The entitlement record proving a player can join a specific paid event.

| Field          | Type     | Description                                          |
|----------------|----------|------------------------------------------------------|
| `player`       | Link     | → Memora Player Profile                             |
| `event`        | Link     | → Memora Live Challenge Event                       |
| `status`       | Select   | `active` \| `revoked` \| `refunded`                 |
| `access_type`  | Select   | `purchase` \| `voucher` \| `admin`                  |
| `purchase_ref` | Link     | → Memora Live Event Purchase *(optional)*            |
| `voucher_ref`  | Link     | → Memora Voucher Redemption *(optional)*             |
| `granted_by`   | Link     | → User *(optional)*                                  |
| `granted_at`   | Datetime | When access was granted                              |
| `revoked_at`   | Datetime | *(optional)*                                         |
| `notes`        | Text     | *(optional)*                                         |

**Unique constraint**: At most one record with `status = active` per `(player, event)` combination.

### 5.4 Memora Live Challenge Participation (DocType)

Existing DocType — tracks actual participation after the player joins.

| Field          | Type     | Description                                          |
|----------------|----------|------------------------------------------------------|
| `event`        | Link     | → Memora Live Challenge Event                       |
| `player`       | Link     | → Memora Player Profile                             |
| `joined_at`    | Datetime | When the player joined                               |
| `submitted_at` | Datetime | When answers were submitted                          |
| `score`        | Float    | Player's score                                       |
| `rank`         | Int      | Player's rank in the event                           |
| `xp_awarded`   | Int      | XP earned from participation                         |

### 5.5 Paid Event Access Logic

| Condition            | Behavior                                               |
|----------------------|--------------------------------------------------------|
| `event.is_paid = 0`  | Normal access rules apply — no payment check needed    |
| `event.is_paid = 1`  | Requires **either** a usable Plan Premium on the same plan **or** an active Memora Live Event Access for that player/event |

**Premium wins**: if a player holds a usable plan premium, they bypass the event-level paid gate entirely. No separate event access record is needed.

### 5.6 Event Access State Rules

| Status     | Meaning                                 |
|------------|-----------------------------------------|
| `active`   | Event entitlement granted               |
| `revoked`  | Manually removed by admin               |
| `refunded` | Removed because the purchase was refunded |

---

## 6. Domain 3 — Voucher System

### 6.1 Memora Voucher Card (DocType)

The voucher asset itself — a redeemable code or card.

| Field              | Type     | Description                                          |
|--------------------|----------|------------------------------------------------------|
| `code`             | Data     | Unique voucher code                                  |
| `voucher_type`     | Select   | `plan_premium` \| `live_event_access`                |
| `target_plan`      | Link     | → Study Plan *(required if voucher_type = plan_premium)* |
| `target_event`     | Link     | → Memora Live Challenge Event *(required if voucher_type = live_event_access)* |
| `max_redemptions`  | Int      | Maximum number of times this voucher can be redeemed (default: 1) |
| `used_redemptions` | Int      | Denormalized counter *(updated under lock, source of truth is redemption count)* |
| `valid_until`      | Datetime | Expiration date *(optional — if null, no expiry)*    |
| `is_active`        | Check    | Whether the voucher can be redeemed                  |

**Validation rules:**

- If `voucher_type = plan_premium` → `target_plan` is required.
- If `voucher_type = live_event_access` → `target_event` is required.
- `used_redemptions` is a **denormalized cache** — the true count is `COUNT(Memora Voucher Redemption WHERE voucher_card = X AND status = success)`. The counter must be updated under the same Redis lock as the redemption operation.

### 6.2 Memora Voucher Redemption (DocType)

The event record of a player redeeming a voucher.

| Field          | Type     | Description                                          |
|----------------|----------|------------------------------------------------------|
| `voucher_card` | Link     | → Memora Voucher Card                               |
| `player`       | Link     | → Memora Player Profile                             |
| `status`       | Select   | `success` \| `reversed`                             |
| `redeemed_at`  | Datetime | When the redemption occurred                         |
| `redeemed_plan`| Link     | → Study Plan (player's plan at redemption time)      |
| `premium_ref`  | Link     | → Memora Plan Premium *(if voucher granted a premium)* |
| `event_access_ref` | Link | → Memora Live Event Access *(if voucher granted event access)* |
| `notes`        | Text     | *(optional)*                                         |
| `reversed_at`  | Datetime | *(required if status = reversed)*                    |

### 6.3 Voucher Rules

- A voucher card is **not** the entitlement itself — it is the instrument. The redemption event creates the actual entitlement (Premium or Event Access).
- One successful redemption per voucher card per player (no player can redeem the same card twice).
- Total successful redemptions across all players must not exceed `max_redemptions`.
- Redemption must be performed under the same Redis lock as the entitlement creation to prevent race conditions.
- If `valid_until` is set and `now() > valid_until`, redemption is rejected.
- If `is_active = 0`, redemption is rejected.

---

## 7. Domain 4 — ERP / Accounting Layer

### 7.1 Integration Principles

- Sales Invoice is **accounting only** — it is never queried for access checks.
- Item is the sellable representation in ERPNext — one dedicated Item per paid live event, one dedicated Item per plan premium product.
- Purchase documents (Plan Premium Purchase, Live Event Purchase) reference the Sales Invoice.
- Access/entitlement documents (Plan Premium, Live Event Access) **never** reference the Sales Invoice directly.

### 7.2 Invoice Creation

Invoices are created during the purchase initiation flow:

1. Purchase document is created with `status = pending`.
2. Sales Invoice is created in ERPNext with the appropriate Item.
3. Invoice reference is stored on the purchase document.
4. Payment gateway session is initiated (if applicable).

Invoice status is managed by ERPNext's standard workflow. The Memora purchase document tracks its own lifecycle independently.

---

## 8. Runtime Access Resolution

These are the hot-path checks that run every time a player attempts to access gated content or join a live event. They must be **fast, deterministic, and centralized**.

### 8.1 For Any Gated Resource Inside a Plan

```
function can_access_gated_resource(player, resource):
    1. Is resource inside player.current_plan?
       → No  → DENY
    
    2. Does player have usable Plan Premium? (call is_plan_premium_usable)
       → Yes → ALLOW immediately
    
    3. Is resource specifically paid with its own access gate?
       → Yes → Check direct resource access record
       → No  → DENY
```

### 8.2 For Live Event Join

```
function can_join_live_event(player, event):
    1. Is event.status valid for joining?
       → No  → DENY
    
    2. Is player.current_plan in event.eligible_plans?
       → No  → DENY
    
    3. Does player have usable Plan Premium for this event's plan?
       → Yes → ALLOW (premium bypasses paid gate)
    
    4. Is event.is_paid = 1?
       → Yes → Does player have active Memora Live Event Access for this event?
               → Yes → ALLOW
               → No  → DENY (must purchase or redeem voucher)
       → No  → ALLOW (free event, normal rules apply)
    
    5. Proceed to atomic join (capacity check, participation creation)
```

### 8.3 Performance Requirements

- `is_plan_premium_usable()` must complete in < 10ms (single indexed query + season lookup).
- `can_join_live_event()` must complete in < 50ms including all checks.
- Access resolution must **never** query Sales Invoice or Purchase documents.
- Future optimization: cache `is_plan_premium_usable` result in Redis with 30–60 second TTL. Cache key: `memora:premium_usable:{player}:{plan}`.

---

## 9. Creation Flows

### 9.1 Plan Premium via Payment Gateway

```
1. Player initiates purchase
2. Acquire Redis lock: memora:plan_premium:{player}:{plan}
3. Validate: no usable premium exists, no open pending purchase
4. Create Memora Plan Premium Purchase (status = pending)
5. Create ERPNext Sales Invoice
6. Release lock
7. Redirect player to payment gateway
8. ... player completes payment ...
9. Payment webhook fires
10. Acquire Redis lock: memora:plan_premium:{player}:{plan}
11. Re-validate: no usable premium exists (idempotency check)
12. Mark purchase as paid (paid_at = now)
13. Create Memora Plan Premium (status = active, source_type = purchase)
14. Release lock
```

**Idempotency**: If step 11 finds a premium already exists (duplicate webhook), skip steps 12–13 silently. Do not error.

### 9.2 Plan Premium via Voucher

```
1. Player submits voucher code
2. Validate voucher card: exists, is_active, not expired, has remaining redemptions
3. Acquire Redis lock: memora:plan_premium:{player}:{plan}
4. Re-validate: no usable premium exists
5. Create Memora Voucher Redemption (status = success)
6. Create Memora Plan Premium (status = active, source_type = voucher)
7. Increment voucher used_redemptions
8. Release lock
```

### 9.3 Plan Premium via Admin

```
1. Admin initiates grant
2. Acquire Redis lock: memora:plan_premium:{player}:{plan}
3. Validate: no usable premium exists
4. Create Memora Plan Premium (status = active, source_type = admin, granted_by = admin_user)
5. Release lock
```

### 9.4 Live Event Access via Payment Gateway

```
1. Player initiates purchase
2. Validate: no usable plan premium covers this event (reject if covered — see Section 11.4)
3. Acquire Redis lock: memora:event_access:{player}:{event}
4. Validate: no active event access exists, no open pending purchase
5. Create Memora Live Event Purchase (status = pending)
6. Create ERPNext Sales Invoice
7. Release lock
8. Redirect player to payment gateway
9. ... player completes payment ...
10. Payment webhook fires
11. Acquire Redis lock: memora:event_access:{player}:{event}
12. Re-validate: no active event access exists (idempotency check)
13. Mark purchase as paid
14. Create Memora Live Event Access (status = active, access_type = purchase)
15. Release lock
```

### 9.5 Live Event Access via Voucher

```
1. Player submits voucher code
2. Validate voucher: type = live_event_access, target matches event
3. Validate: no usable plan premium covers this event (reject if covered)
4. Acquire Redis lock: memora:event_access:{player}:{event}
5. Re-validate: no active event access exists
6. Create Memora Voucher Redemption
7. Create Memora Live Event Access (status = active, access_type = voucher)
8. Increment voucher used_redemptions
9. Release lock
```

### 9.6 Live Event Access via Admin

```
1. Admin initiates grant
2. Acquire Redis lock: memora:event_access:{player}:{event}
3. Validate: no active event access exists
4. Create Memora Live Event Access (status = active, access_type = admin, granted_by = admin_user)
5. Release lock
```

---

## 10. Refund & Revocation Flows

### 10.1 Plan Premium Purchase Refund

When a Plan Premium Purchase is refunded (decision made by support/admin, system only executes):

```
1. Admin approves refund
2. Within a single transaction:
   a. Mark purchase: status = refunded, refunded_at = now
   b. Mark linked premium: status = revoked, revoked_at = now
3. Process financial refund via payment gateway (if applicable)
4. Update ERPNext Sales Invoice per accounting rules
```

**Critical**: Steps 2a and 2b must be atomic. The premium must **never** remain `active` while its purchase is `refunded`.

### 10.2 Live Event Purchase Refund

```
1. Admin approves refund
2. Within a single transaction:
   a. Mark purchase: status = refunded, refunded_at = now
   b. Mark linked access: status = refunded, revoked_at = now
3. Process financial refund via payment gateway
4. Update ERPNext Sales Invoice
```

### 10.3 Admin Revocation (No Refund)

```
Premium revocation:
  premium.status = revoked
  premium.revoked_at = now

Event access revocation:
  access.status = revoked
  access.revoked_at = now
```

No financial records are modified. The purchase (if any) retains its `paid` status.

### 10.4 Refund Eligibility Policy

The system does **not** enforce refund eligibility rules (e.g., "used the content" or "event already started"). Whether a refund should be approved is a **support/admin policy decision** made outside the system. The system only executes the approved refund.

This keeps the refund hot path simple and avoids embedding business policy that changes frequently into code.

---

## 11. Prevention & Validation Rules

### 11.1 Plan Premium

| Rule                                                    | Enforcement           |
|---------------------------------------------------------|-----------------------|
| One active premium per (player, plan)                   | Redis lock + DB unique partial index |
| Reject grant if usable premium already exists            | Checked under lock     |
| `source_type = purchase` → `purchase_ref` required       | DocType validation     |
| `source_type = voucher` → `voucher_ref` required         | DocType validation     |
| `source_type = admin` → `granted_by` required            | DocType validation     |

### 11.2 Plan Premium Purchase

| Rule                                                    | Enforcement           |
|---------------------------------------------------------|-----------------------|
| Reject if usable premium already exists for (player, plan) | Checked under lock  |
| No duplicate open (pending) purchase per (player, plan)  | Checked under lock     |
| `status = paid` → `paid_at` required                     | DocType validation     |
| `status = refunded` → `refunded_at` required             | DocType validation     |

### 11.3 Voucher Redemption

| Rule                                                    | Enforcement           |
|---------------------------------------------------------|-----------------------|
| One successful redemption per voucher card per player    | Checked under lock     |
| Total redemptions ≤ `max_redemptions`                    | Checked under lock (source of truth: COUNT query) |
| Reject if usable premium already exists (for plan vouchers) | Checked under lock |
| `status = reversed` → `reversed_at` required             | DocType validation     |

### 11.4 Live Event Access

| Rule                                                    | Enforcement           |
|---------------------------------------------------------|-----------------------|
| One active access per (player, event)                    | Redis lock + DB unique partial index |
| **Reject** purchase/voucher if usable plan premium already covers the event | Checked before lock acquisition |
| `access_type = purchase` → `purchase_ref` required       | DocType validation     |

**On the reject-if-premium-covers rule**: This is a hard reject for regular users, not a warning. Rationale: prevents double-charging, reduces support tickets, reduces refunds, simplifies the data model. An admin override can be added later if a business need arises, but is not in scope for v1.

---

## 12. Plan Change Interaction

When a player changes their Study Plan:

| What happens                                    | Details                                       |
|-------------------------------------------------|-----------------------------------------------|
| Old Plan Premium record remains                 | Historical record is preserved                |
| Old Premium becomes **unusable** automatically  | Computed validity fails (`plan != current_plan`) |
| Old Premium status does **NOT** change           | Status stays `active` — it was not revoked    |
| Old event access records                         | Removed by existing plan-change logic         |
| Plan history snapshot                            | Stored elsewhere (existing system)            |

**Important**: `Memora Plan Premium.status` is never set to `revoked` as a side effect of a plan change. Revocation is always an explicit action.

---

## 13. Concurrency & Race Condition Handling

### 13.1 Strategy: Redis Lock + DB Safety Net

All entitlement-creating operations are protected by a two-layer strategy:

**Layer 1 — Redis Lock (primary)**

Short-lived Redis locks with a consistent key pattern:

| Operation scope        | Lock key                                    | TTL     |
|------------------------|---------------------------------------------|---------|
| Plan Premium (all flows) | `memora:plan_premium:{player}:{plan}`      | 10 sec  |
| Event Access (all flows) | `memora:event_access:{player}:{event}`     | 10 sec  |

The lock is acquired before the existence check and released after the entitlement is created. This covers: purchase creation, voucher redemption, admin grant, and payment webhook callback.

**Layer 2 — DB Unique Partial Index (safety net)**

```sql
CREATE UNIQUE INDEX idx_active_premium_player_plan 
ON `tabMemora Plan Premium` (player, plan) 
WHERE status = 'active';

CREATE UNIQUE INDEX idx_active_event_access_player_event 
ON `tabMemora Live Event Access` (player, event) 
WHERE status = 'active';
```

If a race condition somehow bypasses the Redis lock (e.g., Redis failure), the DB constraint catches it. The application must handle the `IntegrityError` gracefully and return a user-friendly "already granted" response.

### 13.2 Idempotency

Payment webhooks may fire multiple times. The payment confirmation flow must be idempotent:

- Acquire lock.
- Check if premium/access already exists.
- If yes → skip creation, release lock, return success.
- If no → proceed with creation.

This ensures duplicate webhooks are handled silently without errors.

---

## 14. API Surface

### 14.1 Plan Premium APIs

| # | Endpoint                                    | Caller   | Description                                                     |
|---|---------------------------------------------|----------|-----------------------------------------------------------------|
| 1 | `create_plan_premium_purchase(plan)`         | Player   | Creates pending purchase + invoice, returns payment session URL  |
| 2 | `confirm_plan_premium_payment(purchase_ref)` | Webhook  | Marks purchase paid, creates premium                            |
| 3 | `redeem_plan_premium_voucher(code)`          | Player   | Validates voucher, creates redemption + premium                  |
| 4 | `grant_plan_premium_admin(player, plan)`     | Admin    | Creates premium directly                                        |
| 5 | `revoke_plan_premium(premium_id)`            | Admin    | Sets status to revoked                                          |
| 6 | `get_plan_access_state(player, plan)`        | Player   | Returns: `{has_usable_premium, season_end, source_type}`        |

### 14.2 Paid Live Event APIs

| # | Endpoint                                       | Caller   | Description                                                     |
|---|------------------------------------------------|----------|-----------------------------------------------------------------|
| 1 | `create_live_event_purchase(event)`             | Player   | Creates pending purchase + invoice, returns payment session URL  |
| 2 | `confirm_live_event_payment(purchase_ref)`      | Webhook  | Marks purchase paid, creates event access                       |
| 3 | `redeem_live_event_voucher(code)`               | Player   | Validates voucher, creates redemption + event access             |
| 4 | `grant_live_event_access_admin(player, event)`  | Admin    | Creates event access directly                                   |
| 5 | `get_live_event_access_state(event, player)`    | Player   | Returns: `{has_access, access_type, is_covered_by_premium}`     |
| 6 | `join_live_event(event)`                        | Player   | Full access check + atomic join (source of truth gate)           |

### 14.3 Design Principles

- **Minimize frontend round-trips**: `get_plan_access_state` and `get_live_event_access_state` return everything the frontend needs in one call. The frontend should never assemble access state from multiple API calls.
- **Backend is source of truth**: The `join_live_event` endpoint performs its own full access check regardless of what the frontend believes. The frontend uses access state endpoints for UI rendering only.
- **Lightweight access state queries**: These endpoints must use single indexed queries with JOINs, not multiple `frappe.get_doc()` calls. They are the most frequently called endpoints and must stay under 20ms.

---

## 15. Permission Model

### 15.1 Player Permissions

| Action                            | Allowed |
|-----------------------------------|---------|
| View own access state             | Yes     |
| View own purchase history         | Yes (limited fields — no raw financial docs) |
| Initiate purchase                 | Yes     |
| Redeem voucher                    | Yes     |
| View other players' data          | No      |
| View raw Sales Invoice            | No      |
| Grant/revoke any entitlement      | No      |

### 15.2 Admin Permissions

| Action                            | Allowed |
|-----------------------------------|---------|
| Grant premium / event access      | Yes     |
| Revoke premium / event access     | Yes     |
| Process refunds                   | Yes     |
| View all purchase records         | Yes     |
| View all entitlement records      | Yes     |
| Modify voucher cards              | Yes     |

### 15.3 Frappe DocPerm Implementation

| DocType                        | Player         | Admin          |
|--------------------------------|----------------|----------------|
| Memora Plan Premium            | Read (own)     | Read, Write, Create |
| Memora Plan Premium Purchase   | Read (own, limited) | Full       |
| Memora Live Event Access       | Read (own)     | Read, Write, Create |
| Memora Live Event Purchase     | Read (own, limited) | Full       |
| Memora Voucher Card            | None           | Full           |
| Memora Voucher Redemption      | Read (own)     | Full           |

All player-facing reads are filtered by `player = current_player` via standard Frappe permission queries.

---

## 16. Source of Truth Summary

| Concern                          | Source of Truth Document         | Never Use                        |
|----------------------------------|----------------------------------|----------------------------------|
| Plan-wide unlock for a player    | Memora Plan Premium              | Sales Invoice, Purchase doc      |
| Direct paid event unlock         | Memora Live Event Access         | Sales Invoice, Purchase doc      |
| Payment lifecycle                | Purchase docs (Premium / Event)  | Premium or Access docs           |
| Voucher usage event              | Memora Voucher Redemption        | Voucher Card counter alone       |
| Accounting / financial record    | ERPNext Sales Invoice            | Premium, Access, or Redemption   |
| Whether premium is currently usable | Computed at runtime            | Any stored field                 |

---

## 17. Testing Strategy

### 17.1 Unit Tests

| Test area                                | Cases                                                        |
|------------------------------------------|--------------------------------------------------------------|
| `is_plan_premium_usable` helper          | Active premium + matching plan + valid season → usable       |
|                                          | Active premium + mismatched plan → unusable (plan_mismatch)  |
|                                          | Active premium + expired season → unusable (season_ended)    |
|                                          | Revoked premium → unusable (revoked)                         |
|                                          | No premium exists → unusable (no_active_premium)             |
| Prevention rules                         | Reject duplicate active premium for same (player, plan)      |
|                                          | Reject purchase when usable premium exists                   |
|                                          | Reject event purchase when plan premium covers it            |
| Voucher validation                       | Expired voucher → reject                                     |
|                                          | Inactive voucher → reject                                    |
|                                          | Max redemptions reached → reject                             |
|                                          | Type mismatch → reject                                       |
| Refund cascade                           | Purchase refunded → premium becomes revoked (atomic)         |
|                                          | Event purchase refunded → event access becomes refunded      |

### 17.2 Integration Tests

| Test area                                | Scenario                                                     |
|------------------------------------------|--------------------------------------------------------------|
| Full purchase flow                       | Initiate → pay → premium created → access check passes       |
| Full voucher flow                        | Submit code → redemption created → premium created → access passes |
| Full refund flow                         | Purchase paid → refund → premium revoked → access check fails |
| Plan change interaction                  | Has premium → change plan → access check fails → change back → access passes again |
| Payment webhook idempotency              | Send webhook twice → only one premium created, no error      |

### 17.3 Concurrency Tests

| Test area                                | Scenario                                                     |
|------------------------------------------|--------------------------------------------------------------|
| Concurrent purchase attempts             | Two simultaneous purchases for same (player, plan) → only one succeeds |
| Concurrent voucher redemptions           | Two simultaneous redemptions of a max_redemptions=1 voucher → only one succeeds |
| Webhook during active purchase           | Webhook fires while another creation is in progress → lock ensures serial execution |

---

## 18. Out of Scope

| Feature                                   | Reason                                                       |
|-------------------------------------------|--------------------------------------------------------------|
| Recurring subscriptions                   | Business model is one-time purchase per season, not recurring |
| Shopping cart / multi-item checkout        | Each purchase is a single targeted action                    |
| Partial refunds                           | Not needed for v1; can be added later                        |
| Premium transfer between players          | Adds complexity with minimal business value                  |
| Premium gifting                           | Can be modeled as admin grant if needed                      |
| Dedicated heavy audit log table           | Existing reference fields + Frappe versioning sufficient for v1 |
| Admin override for event purchase when premium covers it | Can be added later if business need arises |
| Bulk voucher generation UI                | Can be done via Data Import or script for v1                 |
| Coupon/discount codes                     | Different from vouchers; out of scope for this feature       |
| Multi-currency per transaction            | Single currency per transaction; currency conversion out of scope |

---

## 19. Open Questions

| #  | Question                                                    | Status   | Decision |
|----|-------------------------------------------------------------|----------|----------|
| 1  | Which payment gateway(s) will be integrated first?          | Open     | —        |
| 2  | Should `get_plan_access_state` be Redis-cached from day 1?  | Deferred | Monitor latency post-launch, cache if > 20ms |
| 3  | Do we need webhook retry / dead-letter handling?             | Open     | Depends on gateway choice |
| 4  | Should voucher codes be human-readable or random UUIDs?      | Open     | —        |
| 5  | Is there a maximum price for a plan premium or event ticket? | Open     | —        |

---

## 20. Appendix — Entity Relationship Summary

```
Study Plan
├── has one Season (start_at, end_at)
├── has many Memora Plan Premium
└── has many Memora Live Challenge Event

Memora Player Profile
├── has one current_plan → Study Plan
├── has many Memora Plan Premium
├── has many Memora Plan Premium Purchase
├── has many Memora Live Event Access
├── has many Memora Live Event Purchase
└── has many Memora Voucher Redemption

Memora Plan Premium
├── belongs to one Player
├── belongs to one Plan
├── may come from one Memora Plan Premium Purchase (source_type = purchase)
├── may come from one Memora Voucher Redemption (source_type = voucher)
└── may be granted by one User (source_type = admin)

Memora Plan Premium Purchase
├── belongs to one Player
├── belongs to one Plan
├── references one ERPNext Item
└── references one ERPNext Sales Invoice

Memora Live Challenge Event
├── belongs to one Plan
├── may be free (is_paid = 0) or paid (is_paid = 1)
├── has many Memora Live Event Access
├── has many Memora Live Event Purchase
└── has many Memora Live Challenge Participation

Memora Live Event Access
├── belongs to one Player
├── belongs to one Event
├── may come from one Memora Live Event Purchase (access_type = purchase)
├── may come from one Memora Voucher Redemption (access_type = voucher)
└── may be granted by one User (access_type = admin)

Memora Voucher Card
├── defines voucher_type (plan_premium | live_event_access)
├── targets one Plan or one Event
└── has many Memora Voucher Redemption

Memora Voucher Redemption
├── belongs to one Voucher Card
├── belongs to one Player
├── may create one Memora Plan Premium
└── may create one Memora Live Event Access
```