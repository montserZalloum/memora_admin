# Tasks: Monetized Access

**Input**: Design documents from `/specs/051-monetized-access/`
**Prerequisites**: plan.md, spec.md, research.md, data-model.md, contracts/, quickstart.md

**Tests**: Not included (not explicitly requested). Add test phases per story if TDD is desired.

**Organization**: Tasks grouped by user story for independent implementation and testing.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependencies)
- **[Story]**: Which user story this task belongs to (e.g., US1, US2)
- Exact file paths included in descriptions

## Path Conventions

- **Frappe app**: `memora_admin/memora_admin/` (DocTypes, services, APIs, events)
- **FastAPI sidecar**: `fastapi_app/` (player-facing endpoints, cache services)
- All paths relative to project root `/home/corex/aurevia-bench/apps/memora_admin`

---

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: Create directory structure and shared utility additions

- [x] T001 Create directory structure for all new modules: 6 DocType directories under `memora_admin/memora_admin/doctype/`, service module `memora_admin/memora_admin/services/premium/` with `__init__.py`, API files `memora_admin/memora_admin/api/`, event files `memora_admin/memora_admin/events/`, FastAPI service files `fastapi_app/services/`, and FastAPI endpoint files `fastapi_app/api/v1/endpoints/`
- [x] T002 [P] Add 5 new Redis key builders (`premium_key`, `event_access_key`, `premium_lock_key`, `event_access_lock_key`, `monetized_webhook_idempotency_key`) to `fastapi_app/core/redis_keys.py`

---

## Phase 2: Foundational (DocTypes, DB Migrations, Core Services)

**Purpose**: All DocTypes, database-level constraints, hook wiring, and the centralized access check that every user story depends on

**CRITICAL**: No user story work can begin until this phase is complete

- [x] T003 [P] Create Memora Plan Premium DocType JSON schema (autoname `PP-.#####.`, fields per data-model.md Entity 1) and controller with source-type field validation in `memora_admin/memora_admin/doctype/memora_plan_premium/`
- [x] T004 [P] Create Memora Plan Premium Purchase DocType JSON schema (autoname `PPP-.#####.`, fields per data-model.md Entity 2) and controller with duplicate-pending validation in `memora_admin/memora_admin/doctype/memora_plan_premium_purchase/`
- [x] T005 [P] Create Memora Live Event Access DocType JSON schema (autoname `LEA-.#####.`, fields per data-model.md Entity 3) and controller with access-type field validation in `memora_admin/memora_admin/doctype/memora_live_event_access/`
- [x] T006 [P] Create Memora Live Event Purchase DocType JSON schema (autoname `LEP-.#####.`, fields per data-model.md Entity 4) and controller with premium-overlap and duplicate-pending validation in `memora_admin/memora_admin/doctype/memora_live_event_purchase/`
- [x] T007 [P] Create Memora Access Voucher DocType JSON schema (autoname `AV-.#####.`, fields per data-model.md Entity 5) and controller with voucher_type-target field conditional validation in `memora_admin/memora_admin/doctype/memora_access_voucher/`
- [x] T008 [P] Create Memora Access Voucher Redemption DocType JSON schema (autoname `AVR-.#####.`, fields per data-model.md Entity 6) and immutable controller in `memora_admin/memora_admin/doctype/memora_access_voucher_redemption/`
- [x] T009 [P] Extend Memora Live Challenge Event DocType JSON with `price` (Currency), `currency` (Link to Currency, default JOD), and `erpnext_item_code` (Link to Item) fields; add validation requiring price > 0 and item_code when is_paid=1 in `memora_admin/memora_admin/doctype/memora_live_challenge_event/`
- [x] T010 Add virtual column migrations and unique indexes to `setup.py` `after_migrate` hook: `_unique_active_plan` + `idx_one_active_premium` on Plan Premium, `_unique_active_event` + `idx_one_active_event_access` on Live Event Access, `idx_voucher_code_hash` on Access Voucher, `_unique_success` + `idx_redemption_unique` on Voucher Redemption (per R-001, quickstart.md migrations section)
- [x] T011 Register `doc_events` hooks in `hooks.py` for Memora Plan Premium (`after_insert`, `on_update` to `premium_sync`) and Memora Live Event Access (`after_insert`, `on_update` to `event_access_sync`)
- [x] T012 Implement centralized premium usability check function (FR-003) in `memora_admin/memora_admin/services/premium/access_check.py` — returns structured result with `usable`, `reason` (none/plan_mismatch/season_ended/revoked), `premium_id`, `season_end`, `source_type` per computed validity logic in data-model.md

**Checkpoint**: All DocTypes exist, DB constraints active, hooks wired, core access check available. User story implementation can begin.

---

## Phase 3: User Story 1 — Player Purchases Plan Premium (Priority: P1) MVP

**Goal**: Player can purchase plan premium via payment gateway, receive entitlement on webhook confirmation, and have premium state cached in Redis for fast access checks.

**Independent Test**: Initiate purchase, simulate webhook, verify premium entitlement is created and Redis cache populated. All gated content within the plan becomes accessible.

### Implementation for User Story 1

- [x] T013 [P] [US1] Implement purchase creation service (validate no existing usable premium or pending purchase, create pending purchase record, return payment session info) and invoice creation helper using Frappe ORM per R-008 in `memora_admin/memora_admin/services/premium/purchase.py`
- [x] T014 [P] [US1] Implement premium Redis cache sync event handlers (`on_premium_created`: compute and cache usability state in Redis hash; `on_premium_updated`: invalidate cache on revoke) in `memora_admin/memora_admin/events/premium_sync.py`
- [x] T015 [US1] Implement FastAPI PremiumService with 3-tier cached premium check (process-local 60s TTL, Redis hash, Frappe API hydration) per R-004 architecture in `fastapi_app/services/premium.py`
- [x] T016 [US1] Create FastAPI premium purchase endpoint `POST /api/v1/premium/purchase` (validate player, check for existing premium/pending purchase via PremiumService, call Frappe purchase creation, return purchase_id + payment_url + amount + currency) in `fastapi_app/api/v1/endpoints/premium.py`
- [x] T017 [US1] Implement monetized payment webhook `POST /api/v1/webhooks/monetized-payment` with Redis SET NX idempotency (24h TTL), plan_premium handler (mark purchase paid, create Plan Premium entitlement, create Sales Invoice, cache in Redis), and duplicate webhook handling (FR-016) in `fastapi_app/api/v1/endpoints/monetized_webhooks.py`
- [x] T018 [US1] Wire premium purchase endpoint and webhook into FastAPI router, add route registration in `fastapi_app/api/v1/router.py`

**Checkpoint**: Player can initiate a plan premium purchase and receive the entitlement when the payment webhook confirms. Premium state is cached in Redis. Core monetization path is functional.

---

## Phase 4: User Story 2 — Player Accesses a Paid Live Event (Priority: P1)

**Goal**: Paid events are gated. Players with usable plan premium bypass payment. Others must purchase a ticket. Event join performs its own access check.

**Independent Test**: Configure a paid event, have a premium player join directly (bypass), have a non-premium player purchase a ticket and join. Free events unaffected.

### Implementation for User Story 2

- [x] T019 [P] [US2] Implement event access Redis cache sync event handlers (`on_event_access_created`: cache access state; `on_event_access_updated`: invalidate cache on revoke/refund) in `memora_admin/memora_admin/events/event_access_sync.py`
- [x] T020 [P] [US2] Implement FastAPI EventAccessService (Redis-cached event access check, hydration from Frappe, has_active_access method) in `fastapi_app/services/event_access.py`
- [x] T021 [US2] Create FastAPI event ticket purchase endpoint `POST /api/v1/events/{event_id}/purchase` (reject if player has usable premium per FR-007, reject if active access or pending purchase exists, create pending purchase via Frappe) in `fastapi_app/api/v1/endpoints/event_access.py`
- [x] T022 [US2] Extend monetized payment webhook with `live_event` purchase_type handler (mark purchase paid, create Live Event Access entitlement, create Sales Invoice, cache in Redis) in `fastapi_app/api/v1/endpoints/webhooks.py`
- [x] T023 [US2] Extend existing event join logic with paid-event access gate per R-010: if `event.is_paid`, check PremiumService for premium bypass, then EventAccessService for ticket access, deny if neither — in the existing LiveChallengeService join method (FR-015 source-of-truth gate)
- [x] T024 [US2] Wire event access endpoints (`purchase`, `join`) into FastAPI router in `fastapi_app/api/v1/router.py`

**Checkpoint**: Paid events work with premium bypass and ticket purchases. Event join performs independent access checks. Both monetization paths (plan premium + event tickets) are functional.

---

## Phase 5: User Story 3 — Player Redeems a Voucher (Priority: P2)

**Goal**: Promotional voucher codes can be created by admins and redeemed by players to receive Plan Premium or Live Event Access without payment.

**Independent Test**: Admin creates a voucher, player redeems the code, entitlement is created. Expired/exhausted/duplicate redemptions are rejected.

### Implementation for User Story 3

- [x] T025 [US3] Implement voucher code generation (`secrets.choice()` 30-char unambiguous alphabet), HMAC-SHA256 hash storage, and `hmac.compare_digest()` timing-safe verification per R-009 and Constitution Principle V in `memora_admin/memora_admin/services/premium/voucher.py`
- [x] T026 [US3] Implement atomic voucher redemption in `memora_admin/memora_admin/services/premium/voucher.py` — verify code via HMAC, check voucher active + not expired + not exhausted, check player hasn't already redeemed, check no existing entitlement, acquire Redis lock, create Redemption + entitlement atomically (FR-011), increment total_redemptions
- [x] T027 [US3] Create admin voucher management whitelisted API methods (`create_access_voucher`: generate code + store hash + return plaintext once; `deactivate_access_voucher`: set is_active=0) in `memora_admin/memora_admin/api/access_voucher.py`
- [x] T028 [US3] Create FastAPI plan premium voucher redemption endpoint `POST /api/v1/premium/voucher/redeem` (accept code, call Frappe voucher redemption service, return premium_id + plan_id + season_end) in `fastapi_app/api/v1/endpoints/premium.py`
- [x] T029 [US3] Create FastAPI event voucher redemption endpoint `POST /api/v1/events/{event_id}/voucher/redeem` (accept code, call Frappe voucher redemption service, return access_id + event_id) in `fastapi_app/api/v1/endpoints/event_access.py`

**Checkpoint**: Voucher lifecycle complete — admin creation, player redemption, entitlement granting. All validation rules enforced (expiry, exhaustion, duplicate). Three entitlement sources now functional: purchase, voucher, admin (next phase).

---

## Phase 5b: B2B Voucher Batch — Plan Premium Grant Type

**Purpose**: Extend the existing B2B voucher batch system (which already supports `product_grant` and `live_event_access`) with a `plan_premium` grant type, enabling admins to create voucher batches that grant premium access to academic plans.

- [x] T025b [US3] Create `Memora Voucher Batch Eligible Plan` child DocType (istable=1, single `plan` Link field → `Memora Academic Plan`) in `memora_admin/memora_admin/doctype/memora_voucher_batch_eligible_plan/`
- [x] T025c [US3] Add `plan_premium` to `Memora Voucher Batch` grant_type options, add `section_eligible_plans` + `eligible_plans` Table field, add validation requiring non-empty eligible_plans and valid plan references
- [x] T025d [US3] Add `voucher` to `Memora Plan Premium` source_type options, add `voucher_ref` Link field → `Memora Voucher Card`, add validation requiring voucher_ref when source_type=voucher
- [x] T025e [US3] Add `plan_premium` Link field → `Memora Plan Premium` to `Memora Voucher Card`
- [x] T025f [US3] Add `requested_plan` Link field and `Plan Not Eligible`/`Already Has Premium` status options to `Memora Voucher Redemption Log`
- [x] T025g [US3] Implement `_preview_plan_premium` and `_redeem_plan_premium` functions in `memora_admin/memora_admin/api/voucher.py` — checks player's plan against eligible list, creates `Memora Plan Premium` with `source_type=voucher`
- [x] T025h [US3] Add `PLAN_NOT_ELIGIBLE` and `ALREADY_HAS_PREMIUM` error codes to FastAPI voucher endpoint error map and failure error set

---

## Phase 6: User Story 4 — Admin Grants or Revokes Entitlements (Priority: P2)

**Goal**: Administrators can manually grant Plan Premium or Live Event Access to a player, and revoke existing entitlements. Supports customer service and promotional use cases.

**Independent Test**: Admin grants premium to a player, verify access. Admin revokes it, verify access denied. Same for event access.

### Implementation for User Story 4

- [x] T030 [US4] Implement admin `grant_plan_premium` (create Plan Premium with source_type=admin, granted_by=current user, reject if usable premium exists) and `revoke_plan_premium` (set status=revoked, record revoked_at/revoked_by, invalidate Redis cache) whitelisted methods in `memora_admin/memora_admin/api/premium.py`
- [x] T031 [US4] Implement admin `grant_event_access` (create Live Event Access with access_type=admin, reject if active access exists) and `revoke_event_access` (set status=revoked, record revoked_at/revoked_by, invalidate Redis cache) whitelisted methods in `memora_admin/memora_admin/api/premium.py`

**Checkpoint**: All three entitlement sources (purchase, voucher, admin) are fully functional for both Plan Premium and Live Event Access.

---

## Phase 7: User Story 5 — Admin Processes a Refund (Priority: P2)

**Goal**: Admin can process refunds that atomically mark the purchase as refunded and revoke the linked entitlement within a single transaction (FR-012).

**Independent Test**: Complete a purchase, process a refund, verify both purchase status and entitlement status are updated atomically. Previously accessible content is no longer available.

### Implementation for User Story 5

- [x] T032 [US5] Implement atomic refund processing service (single transaction: mark purchase refunded + set refunded_at, mark linked entitlement revoked/refunded + set revoked_at, invalidate Redis cache) for both plan premium and event purchases in `memora_admin/memora_admin/services/premium/refund.py`
- [x] T033 [US5] Implement admin `refund_plan_premium_purchase` whitelisted method (validate purchase is in `paid` state, call refund service, FR-012 atomicity) in `memora_admin/memora_admin/api/premium.py`
- [x] T034 [US5] Implement admin `refund_event_purchase` whitelisted method (validate purchase is in `paid` state, call refund service, FR-012 atomicity) in `memora_admin/memora_admin/api/premium.py`

**Checkpoint**: Full financial lifecycle complete — purchase, payment confirmation, refund. Entitlement revocation is always atomic with purchase refund.

---

## Phase 8: User Story 6 — Plan Change Impacts Premium Usability (Priority: P3)

**Goal**: When a player changes plans, their premium automatically becomes unusable via computed validity (no status change). Switching back restores usability. Cache is invalidated on plan change.

**Independent Test**: Grant premium on Plan A, switch to Plan B (verify access denied with reason `plan_mismatch`), switch back to Plan A (verify access restored).

### Implementation for User Story 6

- [x] T035 [US6] Extend plan change hooks to invalidate premium Redis cache (delete `memora:premium:{player}:{old_plan}` key after plan change completes, per R-007) in `memora_admin/memora_admin/events/premium_sync.py` and register the plan change hook in `hooks.py`

**Checkpoint**: Computed validity model validated — no stored expiry, usability derived from current state. Plan changes and season endings handled gracefully.

---

## Phase 9: User Story 7 — Player Checks Access State (Priority: P3)

**Goal**: Player can query complete access state for a plan or event in a single call. Frontend has all data needed for UI rendering without assembling from multiple requests (FR-014).

**Independent Test**: Query access state for various player states (premium, no premium, pending purchase, covered by premium for events) and verify response structure matches PlanAccessState/EventAccessState schemas.

### Implementation for User Story 7

- [x] T036 [P] [US7] Create plan access state endpoint `GET /api/v1/premium/access-state/{plan_id}` returning `PlanAccessState` (has_usable_premium, reason, season_end, source_type, premium_id, has_pending_purchase) using PremiumService in `fastapi_app/api/v1/endpoints/premium.py`
- [x] T037 [P] [US7] Create event access state endpoint `GET /api/v1/events/{event_id}/access-state` returning `EventAccessState` (has_access, access_type, is_covered_by_premium, is_paid, price, currency, has_pending_purchase) using PremiumService + EventAccessService in `fastapi_app/api/v1/endpoints/event_access.py`

**Checkpoint**: Frontend integration ready — all UI-driving access state available via single-call endpoints.

---

## Phase 10: Polish & Cross-Cutting Concerns

**Purpose**: Security hardening, concurrency validation, and end-to-end flow validation

- [x] T038 [P] Add player-only-own-records permission filtering to all player-facing endpoints and hide raw financial documents (purchases, invoices) from player reads (FR-020) across `fastapi_app/api/v1/endpoints/premium.py` and `fastapi_app/api/v1/endpoints/event_access.py`
- [x] T039 [P] Validate concurrency control with threading tests: simultaneous purchase attempts, simultaneous voucher redemptions (last-use race), and simultaneous admin grants — verify Redis lock + DB unique index prevent all duplicates (R-006)
- [x] T040 Run end-to-end flow validation against quickstart.md scenarios: purchase → webhook → access check → event join, voucher redeem → access check, admin grant → access check → revoke → access denied, refund → entitlement revoked

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: No dependencies — start immediately
- **Foundational (Phase 2)**: Depends on Phase 1 — BLOCKS all user stories
- **US1 (Phase 3)**: Depends on Phase 2 only — **MVP target**
- **US2 (Phase 4)**: Depends on Phase 2. Soft dependency on US1 (uses PremiumService from T015 for premium bypass check)
- **US3 (Phase 5)**: Depends on Phase 2. Extends files from US1 (T016 premium.py) and US2 (T021 event_access.py) for voucher redeem endpoints
- **US4 (Phase 6)**: Depends on Phase 2 only — can start after Phase 2 independently
- **US5 (Phase 7)**: Depends on Phase 2. Extends api/premium.py from US4 (T030/T031). Uses entitlements created by US1/US2
- **US6 (Phase 8)**: Depends on Phase 2 and US1 (premium_sync.py from T014)
- **US7 (Phase 9)**: Depends on Phase 2. Extends endpoint files from US1 (T016) and US2 (T021). Uses services from US1 (T015) and US2 (T020)
- **Polish (Phase 10)**: Depends on all user stories being complete

### Recommended Sequential Order

```
Phase 1 → Phase 2 → US1 → US2 → US3 → US4 → US5 → US6 → US7 → Polish
```

### Within Each User Story

- Frappe services before FastAPI services
- Services before endpoints
- Endpoints before router wiring
- Tasks marked [P] within a phase can run in parallel

### Parallel Opportunities

**Phase 2 DocTypes** (T003–T009 all [P]):
```
All 7 DocType tasks can run in parallel — each is a separate directory
```

**US1** (T013 + T014 are [P]):
```
T013 (purchase service) + T014 (premium_sync) can run in parallel
Then: T015 → T016 → T017 → T018
```

**US2** (T019 + T020 are [P]):
```
T019 (event_access_sync) + T020 (EventAccessService) can run in parallel
Then: T021 → T022 → T023 → T024
```

**US7** (T036 + T037 are [P]):
```
T036 (plan access state) + T037 (event access state) can run in parallel
```

---

## Implementation Strategy

### MVP First (User Story 1 Only)

1. Complete Phase 1: Setup
2. Complete Phase 2: Foundational (CRITICAL — blocks all stories)
3. Complete Phase 3: User Story 1 (plan premium purchase)
4. **STOP and VALIDATE**: Test purchase → webhook → premium check flow
5. Deploy/demo if ready — delivers core "pay once, unlock all" value

### Incremental Delivery

1. Setup + Foundational → Foundation ready
2. US1 (Plan Premium Purchase) → MVP! Core monetization path
3. US2 (Paid Live Events) → Second monetization path, premium bypass validated
4. US3 (Voucher Redemption) → Promotional campaigns enabled
5. US4 (Admin Grant/Revoke) → Support operations enabled
6. US5 (Admin Refund) → Financial lifecycle complete
7. US6 (Plan Change) → Computed validity edge cases covered
8. US7 (Access State) → Frontend integration ready
9. Polish → Security hardened, concurrency validated

### Key Risk: Concurrency

The two-layer concurrency control (Redis lock + DB virtual column unique index) should be validated early. Consider running T039 concurrency tests after US1 is complete rather than waiting for the Polish phase.
