# Research: Monetized Access

**Feature Branch**: `051-monetized-access`
**Date**: 2026-03-18

## R-001: MariaDB Partial Unique Index Alternative

**Context**: PRD specifies `CREATE UNIQUE INDEX ... WHERE status = 'active'` for one-active-premium-per-(player, plan) constraint. MariaDB does not support partial unique indexes (PostgreSQL-only feature).

**Decision**: Virtual/generated column with conditional UNIQUE index.

**Implementation**:
```sql
ALTER TABLE `tabMemora Plan Premium`
  ADD COLUMN `_unique_active_plan` VARCHAR(140) AS (
    IF(status = 'active', plan, NULL)
  ) VIRTUAL;

CREATE UNIQUE INDEX idx_one_active_premium
  ON `tabMemora Plan Premium` (player, `_unique_active_plan`);
```

MariaDB treats NULL values as non-duplicate in unique indexes, so only `active` rows participate in the uniqueness check. Revoked/expired rows get NULL and are ignored.

Same pattern for `tabMemora Live Event Access`:
```sql
ALTER TABLE `tabMemora Live Event Access`
  ADD COLUMN `_unique_active_event` VARCHAR(140) AS (
    IF(status = 'active', event, NULL)
  ) VIRTUAL;

CREATE UNIQUE INDEX idx_one_active_event_access
  ON `tabMemora Live Event Access` (player, `_unique_active_event`);
```

**Rationale**: Provides a DB-level safety net alongside Redis locks. Virtual columns are computed on read (zero storage overhead) and work with MariaDB 10.2+.

**Alternatives Considered**:
- Application-only enforcement (rejected: no DB safety net, single point of failure)
- BEFORE INSERT/UPDATE triggers (rejected: harder to maintain, less transparent)
- Separate lookup table (rejected: over-engineering for a constraint)

---

## R-002: Voucher Card Name Collision

**Context**: PRD defines a "Memora Voucher Card" for promotional entitlement codes. An existing `Memora Voucher Card` DocType already exists for the B2B batch distribution system (serial numbers, HMAC PINs, batch allocations, libraries).

**Decision**: Rename to `Memora Access Voucher` and `Memora Access Voucher Redemption`.

**Rationale**: The two systems serve fundamentally different purposes:
- **Existing** `Memora Voucher Card`: B2B batch distribution — generated in batches of up to 1,000, allocated to libraries, HMAC-hashed PINs, serial numbers, commission tracking, consignment/prepaid models.
- **New** `Memora Access Voucher`: Promotional entitlement codes — individually created by admins, grant Plan Premium or Live Event Access directly, simpler lifecycle (active/inactive + usage limits).

Keeping separate DocTypes avoids field bloat and preserves the existing voucher system's integrity.

**Alternatives Considered**:
- Extend existing `Memora Voucher Card` with new fields (rejected: fundamentally different lifecycle and purpose, would pollute existing B2B workflow)
- Keep same name (rejected: would break existing DocType)
- `Memora Promo Code` (rejected: less descriptive about what it grants)

---

## R-003: Payment Gateway Integration Pattern

**Context**: Spec states "payment gateway integration details will be determined during planning — the feature design is gateway-agnostic." Existing codebase has a webhook pattern in `fastapi_app/api/v1/endpoints/webhooks.py`.

**Decision**: Follow existing webhook pattern — gateway-agnostic payload model, Redis idempotency key, immediate 200 response with synchronous processing.

**Existing Pattern** (from `webhooks.py`):
1. Receive webhook payload with idempotency key
2. Check Redis `SET NX` for duplicate (24h TTL)
3. Process: create entitlement + update purchase status
4. Return 200 OK

**New Webhook Flow**:
- Single new endpoint: `POST /api/v1/webhooks/monetized-payment`
- Payload includes `purchase_type` field (`plan_premium` | `live_event`) to route processing
- Same idempotency pattern with Redis `SET NX`
- On success: mark purchase as `paid`, create entitlement, create invoice

**Rationale**: Reuses proven production pattern. Gateway-agnostic design means any payment provider can call this endpoint as long as payload conforms to the schema.

**Alternatives Considered**:
- Separate endpoints per purchase type (rejected: unnecessary duplication, same idempotency pattern)
- Direct gateway SDK integration (rejected: creates vendor lock-in, spec explicitly says gateway-agnostic)

---

## R-004: Premium Access Check Architecture

**Context**: FR-003 requires a centralized premium usability function. FR-002 defines computed validity. SC-002 requires <50ms p95 access resolution.

**Decision**: Two-tier cached check with `PremiumService` on FastAPI side.

**Architecture**:
```
PremiumService.is_plan_premium_usable(player_id, plan_id)
  ├─ Process-local cache (60s TTL) → hit? return
  ├─ Redis hash memora:premium:{player}:{plan} → hit? return
  └─ Frappe API call → compute, cache in Redis (no TTL, event-invalidated), return
```

**Redis Key**: `memora:premium:{player}:{plan}` — Hash with fields:
- `usable` (0|1)
- `reason` (none|plan_mismatch|season_ended|revoked)
- `season_end` (ISO date)
- `source_type` (purchase|voucher|admin)
- `premium_id` (DocType name)

**Invalidation triggers** (two-pronged: direct delete + pubsub):
- Plan Premium created/revoked
- Player changes plan (existing plan_change hook)
- Season updated (end_date changed)

**Rationale**: Follows Principle I (self-healing cache) and Principle II (sub-20ms). Redis hash gives <2ms lookup. Process-local cache eliminates Redis RTT on repeated checks within the same request lifecycle.

**Alternatives Considered**:
- Extend access set with special key like `PREMIUM-{plan}` (rejected: mixes entitlement types, doesn't carry metadata needed for FR-014)
- DB-only checks (rejected: violates Principle II performance target)
- Single Redis string instead of hash (rejected: need multiple fields for access state endpoint)

---

## R-005: Integration with Existing Access Control

**Context**: Existing `AccessService.check_access_with_plan()` checks subject-level grants. Plan Premium grants plan-wide access that supersedes individual grants.

**Decision**: Extend `AccessService` to call `PremiumService` as first-pass bypass, before subject-level grant check.

**Flow Change**:
```python
# BEFORE (existing):
async def check_access_with_plan(player_id, content_key, plan_id):
    # Gate 1: season check
    # Gate 2: explicit grant OR plan membership
    return result

# AFTER (extended):
async def check_access_with_plan(player_id, content_key, plan_id):
    # Gate 1: season check
    # Gate 1.5 (NEW): premium bypass — if usable premium, skip Gate 2
    premium_state = await premium_service.is_plan_premium_usable(player_id, plan_id)
    if premium_state.usable:
        return AccessResult(allowed=True, via="premium")
    # Gate 2: explicit grant OR plan membership (unchanged)
    return result
```

**Rationale**: Preserves existing access control flow. Premium check is additive — never removes existing access, only adds a bypass path. Centralized in PremiumService per FR-003.

**Alternatives Considered**:
- Separate premium-aware endpoint (rejected: violates FR-003 centralization requirement, duplicates logic)
- Modify Gate 2 internals (rejected: unnecessary complexity, premium is conceptually a different gate)

---

## R-006: Concurrency Control Strategy

**Context**: FR-004 and FR-008 require duplicate prevention under concurrent requests. Existing codebase uses Redis `SET NX EX` pattern (see `plan_change.py`).

**Decision**: Two-layer concurrency control matching existing patterns.

**Layer 1 — Redis Lock (primary)**:
- Lock key: `memora:lock:premium:{player}:{plan}` (10s TTL)
- Lock key: `memora:lock:event_access:{player}:{event}` (10s TTL)
- Acquired via `SET NX EX 10`, released via `DEL`
- Covers: purchase creation, voucher redemption, admin grant, webhook callback

**Layer 2 — DB Virtual Column Unique Index (safety net)**:
- See R-001 for implementation
- Catches any race condition that slips past Redis lock (Redis restart, lock expiry)

**Lock Scope**: Lock acquired before existence check, held through entitlement creation. Any failure within the lock releases it and returns error to caller.

**Rationale**: Follows proven `freeze_key()` pattern from `plan_change.py`. Two layers provide defense-in-depth without over-engineering.

**Alternatives Considered**:
- `SELECT ... FOR UPDATE` (rejected: requires long-held transactions, doesn't fit Frappe's request-per-transaction model)
- Advisory locks (rejected: MariaDB advisory locks are connection-scoped, poor fit for web requests)
- Optimistic locking with retry (rejected: unnecessary complexity when Redis lock provides deterministic ordering)

---

## R-007: Plan Change Hook Integration

**Context**: FR-013 requires preserving Plan Premium records when a player changes plans. Existing `plan_change.py` performs comprehensive cleanup (deletes subscriptions, progress, memory states).

**Decision**: Do NOT modify plan_change to revoke or delete premiums. Premium stays `active` — computed validity returns `unusable` with reason `plan_mismatch`.

**Required Changes to Plan Change Flow**:
1. **Invalidate premium cache**: After plan change completes, delete Redis key `memora:premium:{player}:{old_plan}` + pubsub notification
2. **No deletion**: Plan Premium records are NOT deleted during plan change (unlike subscriptions which are deleted)
3. **Re-activation**: If player changes back to old plan within the season, computed validity automatically returns `usable` again (no action needed)

**Rationale**: FR-013 explicitly states "status is only modified by explicit business actions (admin revoke, refund cascade), never by time or plan changes." Computed validity handles this naturally.

**Alternatives Considered**:
- Revoke on plan change (rejected: spec explicitly forbids)
- Mark as "suspended" status (rejected: introduces unnecessary status, computed validity already handles it)

---

## R-008: Invoice Creation Pattern

**Context**: FR-017 requires an accounting invoice for every purchase. Constitution Principle VI forbids direct SQL INSERT into accounting tables.

**Decision**: Follow existing `voucher/invoice.py` pattern using Frappe ORM.

**Implementation**:
```python
def create_purchase_invoice(purchase_doc):
    invoice = frappe.get_doc({
        "doctype": "Sales Invoice",
        "customer": get_player_customer(purchase_doc.player),
        "items": [{
            "item_code": purchase_doc.erpnext_item_code,
            "qty": 1,
            "rate": purchase_doc.amount,
        }],
        "currency": purchase_doc.currency,
    })
    invoice.insert()
    invoice.submit()
    return invoice.name
```

**Rationale**: Frappe ORM ensures GL entries and any e-invoicing hooks (JoFotara) fire correctly. Direct SQL INSERT is FORBIDDEN by Constitution Principle VI.

**Alternatives Considered**:
- Direct SQL (FORBIDDEN by constitution)
- Deferred invoice creation (rejected: FR-017 requires invoice at purchase time)

---

## R-009: Access Voucher Security

**Context**: Constitution Principle V (NON-NEGOTIABLE) requires cryptographic security for all voucher PINs/codes. Access Vouchers carry monetary value (they bypass payment).

**Decision**: Apply full Constitution Principle V requirements to Access Voucher codes.

**Implementation**:
- **Generation**: `secrets.choice()` from 30-character unambiguous alphabet (same as existing voucher system)
- **Storage**: HMAC-SHA256 hash only. Plaintext NEVER persisted.
- **Verification**: `hmac.compare_digest()` (timing-safe comparison)
- **HMAC secret**: Reuse existing `voucher_hmac_secret` from `site_config.json`
- **Redemption locking**: Redis lock per voucher (see R-006), NOT `SELECT FOR UPDATE` (different from B2B vouchers which use FOR UPDATE on the card row)

**Difference from B2B Vouchers**: Access Vouchers don't need batch generation, serial numbers, or Fernet-encrypted exports. The security model is simpler but the core cryptographic requirements are identical.

**Rationale**: Access Voucher codes are bearer credentials with monetary value. Any weakness creates direct financial exposure. Constitution Principle V is NON-NEGOTIABLE.

**Alternatives Considered**:
- Plain text codes (FORBIDDEN by constitution)
- UUID-based codes without hashing (rejected: UUIDs are predictable, violates Principle V)

---

## R-010: Event Join Integration

**Context**: FR-015 requires the event join operation to perform its own full access check. Existing `LiveChallengeService` handles event lifecycle.

**Decision**: Extend `LiveChallengeService.join()` to check paid-event access before allowing join.

**Extended Join Flow**:
```
join_live_event(player_id, event_id):
  1. Existing checks (event status, capacity, eligibility)
  2. NEW: if event.is_paid:
     a. Check PremiumService.is_plan_premium_usable(player_id, event.plan_id)
        → usable? ALLOW (premium bypass)
     b. Check EventAccessService.has_active_access(player_id, event_id)
        → has access? ALLOW
     c. DENY (no access to paid event)
  3. Proceed with existing join logic (capacity, participation creation)
```

**Rationale**: Backend is the source of truth for access (FR-015). Frontend may show "Join" button based on access state, but the join endpoint performs its own independent check.

**Alternatives Considered**:
- Trust frontend access state (rejected: violates FR-015 source-of-truth requirement)
- Separate paid-join endpoint (rejected: unnecessary duplication, same flow with added check)
