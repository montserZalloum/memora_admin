# Tasks: Single Live Event Purchase

**Input**: Design documents from `/specs/052-live-event-purchase/`
**Prerequisites**: plan.md, spec.md, research.md, data-model.md, contracts/

**Tests**: Included per Constitution Principle VIII (TDD mandatory). Write tests FIRST, verify they FAIL, then implement.

**Organization**: Tasks grouped by user story. US2 (Join-Time Access Check) and US3 (Access State Inquiry) are **already complete** from 051 (commit 378d022) and have no tasks.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependencies)
- **[Story]**: Which user story this task belongs to (e.g., US1, US4, US5, US6)
- Exact file paths included in all task descriptions

---

## Phase 1: Setup (DocType Schema + Hook Registration)

**Purpose**: Add the `expires_at` field and register all new Frappe hooks before any implementation begins.

- [x] T001 [P] Add `expires_at` Datetime field (read-only, after `status`) to `memora_admin/doctype/memora_live_event_purchase/memora_live_event_purchase.json` — field label "Expires At", no default, not required (only meaningful for pending status)
- [x] T002 [P] Register new entries in `hooks.py`: (1) `scheduler_events` cron `"*/5 * * * *"` pointing to `memora_admin.memora_admin.tasks.purchase_expiry.cancel_expired_purchases`, and (2) `doc_events` for `"Memora Live Challenge Event"` with `before_save` pointing to `memora_admin.memora_admin.events.item_sync.ensure_paid_event_item`

**Checkpoint**: DocType schema updated, hooks registered. Implementation can begin.

---

## Phase 2: User Story 1 — Complete Purchase Expiry Field (Priority: P1) :dart: MVP

**Goal**: Every new purchase gets an `expires_at = now + 30 minutes` timestamp at creation, enabling the auto-cancel job (US4) and completing FR-001.

**Independent Test**: Create a purchase via `create_event_purchase()`, assert `expires_at` is ~30 minutes in the future and field is read-only.

**Existing from 051**: Purchase creation, duplicate-purchase prevention, payment confirmation, invoice creation, access granting — all complete. Only the 30-minute expiry timestamp is missing.

### Tests for User Story 1

> **Write these tests FIRST, ensure they FAIL before implementation**

- [x] T003 [US1] Write unit tests for `expires_at` assignment in `memora_admin/tests/test_purchase_expiry.py`: (1) `test_expires_at_set_on_insert` — mock `now_datetime()`, create purchase doc, assert `expires_at == now + 30 min`; (2) `test_expires_at_readonly` — assert field is read-only after insert; (3) `test_expires_at_only_for_pending` — assert `expires_at` is ignored/not checked for non-pending statuses

### Implementation for User Story 1

- [x] T004 [P] [US1] Add `before_insert` method in `memora_admin/doctype/memora_live_event_purchase/memora_live_event_purchase.py` — if `self.expires_at` is not set, default to `now_datetime() + timedelta(minutes=30)`. This is the defensive fallback for any insert path.
- [x] T005 [P] [US1] Set `expires_at = now_datetime() + timedelta(minutes=30)` explicitly in `create_event_purchase()` in `memora_admin/services/premium/event_purchase.py` — set on the purchase doc before calling `doc.insert()`. This is the primary business logic path.

**Checkpoint**: US1 complete. New purchases have a 30-minute expiry. Existing purchase-to-access flow unchanged.

---

## Phase 3: User Story 4 — Auto-Cancellation of Expired Purchases (Priority: P2)

**Goal**: A scheduled job runs every 5 minutes, batch-cancelling all pending purchases past their `expires_at` deadline (FR-010). Students can then create new purchases.

**Independent Test**: Insert a purchase with `expires_at` in the past, run the job, verify status is `cancelled`. Insert a paid purchase, run the job, verify it's untouched.

**Contract**: `contracts/purchase-expiry.yaml`

### Tests for User Story 4

> **Write these tests FIRST, ensure they FAIL before implementation**

- [x] T006 [US4] Write unit tests for `cancel_expired_purchases()` in `memora_admin/tests/test_purchase_expiry.py`: (1) `test_cancels_expired_pending` — insert pending purchase with `expires_at` in the past, run job, assert `status == 'cancelled'`; (2) `test_ignores_non_pending` — insert paid/failed/refunded purchases with expired `expires_at`, run job, assert statuses unchanged; (3) `test_idempotent` — run job twice, assert no errors and already-cancelled purchases unchanged; (4) `test_batch_update` — insert 3 expired + 2 non-expired pending purchases, run job, assert only 3 cancelled

### Implementation for User Story 4

- [x] T007 [US4] Create `memora_admin/tasks/purchase_expiry.py` with `cancel_expired_purchases()`: execute batch `UPDATE tabMemora Live Event Purchase SET status='cancelled', modified=NOW(), modified_by='Administrator' WHERE status='pending' AND expires_at < NOW()` via `frappe.db.sql()`. Log count of cancelled purchases. No per-doc overhead — single atomic SQL statement per contract.

**Checkpoint**: US4 complete. Expired pending purchases are automatically cleaned up every 5 minutes.

---

## Phase 4: User Story 5 — Refund with Credit Note (Priority: P2)

**Goal**: Extend `refund_event_purchase()` to atomically create an ERPNext Credit Note (Sales Invoice with `is_return=1`) linked to the original invoice, completing FR-011's all-or-nothing cascade.

**Independent Test**: Create a paid purchase with invoice, call refund, verify: purchase is refunded, access is refunded, Credit Note exists and links to original invoice. Then test rollback: mock Credit Note creation failure, verify purchase stays paid and access stays active.

**Contract**: `contracts/refund-credit-note.yaml`

### Tests for User Story 5

> **Write these tests FIRST, ensure they FAIL before implementation**

- [x] T008 [US5] Write unit tests for credit note creation in `memora_admin/tests/test_refund_credit_note.py`: (1) `test_credit_note_created_on_refund` — mock `frappe.new_doc`, call refund on purchase with `erpnext_invoice`, assert Credit Note created with `is_return=1`, `return_against=original_invoice`, `qty=-1`, `rate=purchase.amount`, and submitted; (2) `test_no_credit_note_without_invoice` — call refund on purchase without `erpnext_invoice`, assert refund succeeds with `credit_note_id=None`; (3) `test_rollback_on_credit_note_failure` — mock Credit Note insert to raise, assert exception propagates (Frappe transaction rollback handles the rest); (4) `test_return_value_includes_credit_note_id` — assert return dict contains `credit_note_id` field

### Implementation for User Story 5

- [x] T009 [US5] Extend `refund_event_purchase()` in `memora_admin/services/premium/refund.py`: after marking purchase refunded and access refunded (existing steps 1-2), add step 3 — if `purchase.erpnext_invoice` exists: create `frappe.new_doc("Sales Invoice")` with `customer=_get_player_customer(purchase.player)`, `is_return=1`, `return_against=purchase.erpnext_invoice`, `currency=purchase.currency`, one item row with `item_code=purchase.erpnext_item_code`, `qty=-1`, `rate=float(purchase.amount)`. Insert and submit. Add `credit_note_id` to return dict (None if no invoice). All within existing Frappe transaction for atomicity.

**Checkpoint**: US5 complete. Refunds atomically create Credit Notes for accounting reconciliation.

---

## Phase 5: User Story 6 — Automatic ERPNext Item Creation (Priority: P3)

**Goal**: When a Live Challenge Event is saved with `is_paid=1`, automatically create an ERPNext Item (`LIVE-EVENT-{event.name}`) and set `erpnext_item_code` on the event. Idempotent — never creates duplicates. Never deletes items when `is_paid` toggles to 0 (FR-014).

**Independent Test**: Create a paid event, save, verify Item exists and `erpnext_item_code` is set. Save again, verify no duplicate. Toggle `is_paid=0`, save, verify Item still exists.

**Contract**: `contracts/item-auto-creation.yaml`

### Tests for User Story 6

> **Write these tests FIRST, ensure they FAIL before implementation**

- [x] T010 [US6] Write unit tests for `ensure_paid_event_item()` in `memora_admin/tests/test_item_sync.py`: (1) `test_creates_item_for_paid_event` — mock `frappe.db.exists` returning False, mock `frappe.new_doc`, call handler with `is_paid=1`, assert Item created with `item_code=LIVE-EVENT-{name}`, `item_group=Services`, `stock_uom=Nos`, `is_stock_item=0`, `is_sales_item=1`; (2) `test_idempotent_no_duplicate` — mock `frappe.db.exists` returning True, assert no `frappe.new_doc` called, assert `doc.erpnext_item_code` still set; (3) `test_noop_for_free_event` — call with `is_paid=0`, assert no Item creation and no changes to `erpnext_item_code`; (4) `test_item_code_format` — assert generated code matches `LIVE-EVENT-{doc.name}` pattern; (5) `test_item_name_uses_title` — assert `item_name = "Live Event Ticket: {doc.event_title}"`

### Implementation for User Story 6

- [x] T011 [US6] Create `memora_admin/events/item_sync.py` with `ensure_paid_event_item(doc, method)`: if `doc.is_paid != 1`, return immediately (no-op per FR-014). Compute `item_code = f"LIVE-EVENT-{doc.name}"`. If `frappe.db.exists("Item", item_code)`, set `doc.erpnext_item_code = item_code` and return. Otherwise create `frappe.new_doc("Item")` with `item_code`, `item_name=f"Live Event Ticket: {doc.event_title or doc.name}"`, `item_group="Services"`, `stock_uom="Nos"`, `is_stock_item=0`, `is_sales_item=1`, `include_item_in_manufacturing=0`, `description=f"Ticket for live event {doc.name}"`. Insert with `ignore_permissions=True`. Set `doc.erpnext_item_code = item_code`.

**Checkpoint**: US6 complete. Paid events auto-create ERPNext Items; admins never need to create them manually.

---

## Phase 6: Polish & Cross-Cutting Concerns

**Purpose**: End-to-end validation across all user stories

- [x] T012 Write integration test for full purchase lifecycle in `memora_admin/tests/test_purchase_lifecycle.py`: (1) create paid event → verify Item auto-created (US6); (2) create purchase → verify `expires_at` set (US1); (3) confirm payment → verify invoice + access (051); (4) refund → verify credit note + access revoked (US5); (5) create expired purchase → run cancel job → verify cancelled (US4); (6) create new purchase after cancel → verify success
- [ ] T013 Run quickstart.md manual smoke tests for all three gaps (expiry, credit note, item creation) and verify expected behavior

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: No dependencies — start immediately
- **US1 (Phase 2)**: Depends on T001 (field exists in DocType JSON)
- **US4 (Phase 3)**: Depends on T001 (field exists) + T002 (scheduler registered) + US1 (field gets populated)
- **US5 (Phase 4)**: Depends on Phase 1 only — independent of US1/US4
- **US6 (Phase 5)**: Depends on T002 (doc_events registered) — independent of US1/US4/US5
- **Polish (Phase 6)**: Depends on all user story phases complete

### User Story Independence

- **US1 (P1)**: Foundation for US4 but independently testable
- **US4 (P2)**: Reads `expires_at` set by US1 — depends on US1 being complete
- **US5 (P2)**: Fully independent — touches only `refund.py`
- **US6 (P3)**: Fully independent — creates new file `item_sync.py`
- **US2, US3**: Already complete from 051 — no tasks needed

### Within Each User Story

1. Tests MUST be written and FAIL before implementation (TDD)
2. Implementation tasks marked [P] can run in parallel
3. Verify tests PASS after implementation

### Parallel Opportunities

- **Phase 1**: T001 and T002 are parallel (different files)
- **Phase 2**: T004 and T005 are parallel (different files, after T003)
- **After Phase 2**: US5 and US6 can run in parallel (completely independent files)
- **US4** should follow US1 (depends on `expires_at` field being populated)

---

## Parallel Example: After Setup

```bash
# After Phase 1 (Setup) completes, launch US1 immediately:
Task T003: "Write unit tests for expires_at in memora_admin/tests/test_purchase_expiry.py"

# Then implement US1 in parallel:
Task T004: "Set expires_at on before_insert in memora_live_event_purchase.py"
Task T005: "Set expires_at in create_event_purchase() in event_purchase.py"

# After US1, launch US4 + US5 + US6 in parallel:
# Stream 1 (US4): T006 → T007
# Stream 2 (US5): T008 → T009
# Stream 3 (US6): T010 → T011
```

---

## Implementation Strategy

### MVP First (US1 Only)

1. Complete Phase 1: Setup (T001, T002)
2. Complete Phase 2: US1 (T003 → T004 + T005)
3. **STOP and VALIDATE**: Purchases now have expiry timestamps
4. This delivers the P1 gap — the most critical missing piece

### Incremental Delivery

1. Setup → US1 (expires_at field) → **Validate** (MVP)
2. Add US4 (auto-cancel job) → **Validate** (expired purchases cleaned up)
3. Add US5 (refund credit note) + US6 (item creation) in parallel → **Validate**
4. Polish → Integration test → Smoke test → **Done**

### Optimal Solo Developer Path

1. T001 + T002 (Setup, parallel)
2. T003 → T004 + T005 (US1: test then implement)
3. T006 → T007 (US4: test then implement)
4. T008 + T010 (US5 + US6 tests, parallel — different files)
5. T009 + T011 (US5 + US6 implementation, parallel — different files)
6. T012 → T013 (Polish)

---

## Notes

- All 051 infrastructure (purchase creation, payment confirmation, access granting, join gating, access state query, Redis caching, webhook) is **unchanged** — this feature fills 4 peripheral gaps only
- US2 (Join-Time Access Check) and US3 (Access State Inquiry) require **zero tasks** — fully delivered in 051
- All financial operations use Frappe ORM exclusively (Constitution Principle VI)
- `expires_at` batch UPDATE uses raw SQL per contract — acceptable for non-financial status cleanup
- Credit Note creation follows existing `voucher/invoice.py:create_credit_note()` pattern but with event-specific item codes
- Item auto-creation follows existing `setup.py:_ensure_voucher_service_item()` pattern
