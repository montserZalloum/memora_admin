# Tasks: Scholarship & Gift Voucher System

**Input**: Design documents from `/specs/034-scholarship-gift-vouchers/`
**Prerequisites**: plan.md, spec.md, research.md, data-model.md, contracts/api.md, quickstart.md

**Tests**: Included — plan.md specifies Test-First Coverage (Constitution VIII) and project structure defines `tests/test_scholarship_vouchers.py`.

**Organization**: Tasks grouped by user story. US3 (Grant Access Without Voucher) is fully delivered by Phase 1 schema change (no additional code). All source paths relative to `memora_admin/memora_admin/`.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependencies on incomplete tasks)
- **[Story]**: Which user story this task belongs to (US1–US5)
- Exact file paths included in every task description

---

## Phase 1: Setup — Schema Changes

**Purpose**: Add new fields to existing DocTypes. All user stories depend on these schema changes.

- [x] T001 Add `batch_purpose` Select field (options: Sale\nScholarship\nGift\nPromotion, default Sale, reqd) after `batch_name` in `doctype/memora_voucher_batch/memora_voucher_batch.json`
- [x] T002 [P] Add `batch_purpose` Select field (read-only, same options as batch) and `recipient_note` Small Text field (with `depends_on: eval:doc.batch_purpose && doc.batch_purpose !== 'Sale'`) to `doctype/memora_voucher_card/memora_voucher_card.json`
- [x] T003 [P] Add "Scholarship" and "Gift" options to `payment_method` Select field in `doctype/memora_subscription_transaction/memora_subscription_transaction.json` — new options string: `Payment Gateway\nManual-Admin\nVoucher\nScholarship\nGift`
- [x] T004 Run `bench --site x.conanacademy.com migrate` to apply all schema changes to the database

---

## Phase 2: Foundational — Validation & Card Generation

**Purpose**: Core validation rules and card generation logic that multiple user stories depend on.

**CRITICAL**: No user story work can begin until this phase is complete.

- [x] T005 Add batch_purpose validation rules to `doctype/memora_voucher_batch/memora_voucher_batch.py`: (1) if `batch_purpose != 'Sale'` and `face_value > 0`, throw "Non-sale batches must have zero face value." (2) if `status != 'Draft'` and `batch_purpose` changed from DB value, throw "Batch purpose cannot be changed after Draft status."
- [x] T006 [P] Propagate `batch_purpose` from batch to each card during bulk insert in `generate_cards_job()` at `api/voucher.py` — add `batch_purpose` to the fields list and include `batch.batch_purpose` value in each card row

**Checkpoint**: Schema live, validation enforced, card generation propagates purpose.

---

## Phase 3: User Story 1 — Create and Distribute Scholarship Vouchers (P1) MVP

**Goal**: Admins can create a non-Sale batch, generate cards, directly activate all cards (bypassing library allocation), export PINs, and students can redeem them.

**Independent Test**: Create a Scholarship batch (face_value=0) → generate cards → export PINs → Direct Activate → verify all cards are `Allocated` with `library='Admin-Direct'` → redeem one PIN as a student → confirm subscription created.

### Implementation

- [x] T007 [US1] Implement `direct_activate(batch_name)` whitelisted endpoint in `api/voucher.py` — guard: batch must exist, status must be `Generated`, purpose must be non-Sale; bulk SQL UPDATE setting `status='Allocated'`, `library='Admin-Direct'`, `modified=NOW()`, `modified_by=current_user` on all Available cards; transition batch to `Active`; update `allocated_count`; `frappe.db.commit()`; return `{"status": "activated", "activated_count": N}`; idempotent (second call returns count 0)
- [x] T008 [US1] Update `doctype/memora_voucher_batch/memora_voucher_batch.js` — (1) add `batch_purpose` change handler: auto-set `face_value=0` and make `face_value` read-only when non-Sale, restore editability when Sale; (2) add "Direct Activate" custom button in `refresh`: visible only when `batch_purpose != 'Sale'` AND `status == 'Generated'`, calls `memora_admin.memora_admin.api.voucher.direct_activate` and reloads form on success; (3) make `batch_purpose` field read-only when `status != 'Draft'`

**Checkpoint**: Full scholarship voucher lifecycle works end-to-end. Existing sale flows unchanged.

---

## Phase 4: User Story 2 — Prevent Cross-Purpose Misuse (P1)

**Goal**: Non-sale batches cannot be allocated to libraries. Sale batches cannot be directly activated. Batch purpose is immutable after Draft.

**Independent Test**: Attempt to allocate a Scholarship batch to a library (should fail with validation error). Verify Direct Activate button is hidden for Sale batches. Attempt server-side `direct_activate()` on a Sale batch (should fail). Attempt to change `batch_purpose` on a Generated batch (should fail).

### Implementation

- [x] T009 [US2] Add non-Sale batch guard to both `fill_cards()` and `submit_allocation()` in `api/allocation.py` — before existing logic, fetch `batch_purpose` via `frappe.db.get_value("Memora Voucher Batch", alloc.batch, "batch_purpose")`; if not `Sale`, `frappe.throw("Cannot allocate cards from a non-sale batch. Use Direct Activate instead.", frappe.ValidationError)`

**Checkpoint**: Cross-purpose misuse fully blocked at server level. US1 Direct Activate already guards Sale batches (T007).

---

## Phase 5: User Story 3 — Grant Access Without Voucher (P2)

**Goal**: Admins can manually create subscription transactions with "Scholarship" or "Gift" payment method to grant individual students access without issuing a voucher.

**Independent Test**: Create a subscription transaction with payment_method "Scholarship", amount_paid 0, for a student → verify student gains access → verify no sales invoice is associated.

**No additional tasks** — fully delivered by T003 (schema change adding "Scholarship" and "Gift" to `payment_method` options). The existing `_handle_approval()` pipeline in `memora_subscription_transaction.py` handles all payment methods generically. No code changes needed.

---

## Phase 6: User Story 4 — Track Non-Sale Grants via Report (P2)

**Goal**: Dedicated Script Report showing all non-sale voucher batches with card distribution statistics (total, activated, redeemed, voided, remaining) and filtering by purpose, date range, and product grant.

**Independent Test**: Create batches with different non-sale purposes, activate and redeem some cards → open report → verify correct counts → apply filters → verify filtered results match.

### Implementation

- [x] T010 [US4] Create Script Report directory and JSON definition: create `report/scholarship_gift_grants/__init__.py` (empty) and `report/scholarship_gift_grants/scholarship_gift_grants.json` with `doctype: "Memora Voucher Batch"`, `report_type: "Script Report"`, `is_standard: "Yes"`, `roles: [{"role": "System Manager"}]`
- [x] T011 [P] [US4] Create report Python backend at `report/scholarship_gift_grants/scholarship_gift_grants.py` — implement `execute(filters)` returning columns and data; columns: Batch (Link), Batch Name (Data), Purpose (Data), Product Grant (Data), Total Cards (Int), Activated (Int), Redeemed (Int), Voided (Int), Remaining (Int), Created (Date); query joins `tabMemora Voucher Batch` with `tabMemora Voucher Card` aggregating status counts; WHERE `batch_purpose != 'Sale'`; apply optional filters for `batch_purpose`, `from_date`/`to_date` on `creation`, `product_grant` via EXISTS on `tabMemora Voucher Batch Grant`; GROUP BY `b.name` ORDER BY `b.creation DESC`; include `get_report_summary()` with total cards, total redeemed, avg redemption rate
- [x] T012 [P] [US4] Create report JS frontend at `report/scholarship_gift_grants/scholarship_gift_grants.js` — define filters: `batch_purpose` (Select, options: Scholarship\nGift\nPromotion), `from_date` (Date), `to_date` (Date), `product_grant` (Link to Memora Product Grant)

**Checkpoint**: Non-sale grant tracking report fully functional with filters and summary.

---

## Phase 7: User Story 5 — Add Recipient Notes to Non-Sale Cards (P3)

**Goal**: Non-sale cards have an optional "recipient note" field for tracking who the card is intended for. Hidden on sale cards.

**Independent Test**: Open a Scholarship card form → recipient_note field is visible and editable. Open a Sale card form → recipient_note field is hidden.

### Implementation

- [x] T013 [US5] Add conditional visibility logic for `recipient_note` in `doctype/memora_voucher_card/memora_voucher_card.js` — in `refresh` handler, toggle visibility of `recipient_note` based on `batch_purpose !== 'Sale'`; ensure field is hidden by default and only shows for non-Sale cards

**Checkpoint**: Recipient notes visible only on non-sale cards.

---

## Phase 8: Polish & Cross-Cutting Concerns

**Purpose**: Tests and end-to-end validation across all user stories.

- [x] T014 Write test suite in `tests/test_scholarship_vouchers.py` covering: (1) validation — face_value=0 enforced for non-Sale, batch_purpose immutable after Draft; (2) Direct Activate flow — cards transition to Allocated with library='Admin-Direct', batch transitions to Active, idempotent on second call; (3) cross-purpose guards — fill_cards and submit_allocation reject non-Sale batches, direct_activate rejects Sale batches; (4) report — correct counts for activated/redeemed/voided/remaining, filters work correctly. Use existing test infrastructure: `VoucherTestBase`, `voucher_fixtures` (season `SEAS-00027`), `voucher_helpers`.
- [x] T015 Run quickstart.md validation scenarios end-to-end: create Scholarship batch → generate → export → Direct Activate → redeem as student → verify report counts

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: No dependencies — start immediately
- **Foundational (Phase 2)**: Depends on Phase 1 (T004 migrate must complete) — BLOCKS all user stories
- **US1 (Phase 3)**: Depends on Phase 2 — delivers core MVP
- **US2 (Phase 4)**: Depends on Phase 2 — can run in parallel with US1 (different files)
- **US3 (Phase 5)**: Delivered by Phase 1 — no additional work
- **US4 (Phase 6)**: Depends on Phase 2 — can run in parallel with US1/US2 (all new files)
- **US5 (Phase 7)**: Depends on Phase 1 — can run in parallel with US1/US2/US4 (different file)
- **Polish (Phase 8)**: Depends on all user story phases complete

### User Story Dependencies

- **US1 (P1)**: Independent — core MVP, no cross-story dependencies
- **US2 (P1)**: Independent — guards are in separate files from US1 implementation
- **US3 (P2)**: Independent — delivered by schema change only
- **US4 (P2)**: Independent — entirely new files, no overlap with US1/US2
- **US5 (P3)**: Independent — different file (card.js) from US1 (batch.js)

### Within Each User Story

- Schema (Phase 1) before validation (Phase 2)
- Validation before endpoints (Phase 3+)
- Server-side before client-side (Python before JS)
- Implementation before tests

### Parallel Opportunities

Within Phase 1:
- T002 and T003 can run in parallel (different DocType JSONs)

Within Phase 2:
- T005 and T006 can run in parallel (batch.py vs voucher.py)

Across Phases 3–7 (after Phase 2 completes):
- US1 (T007–T008), US2 (T009), US4 (T010–T012), US5 (T013) can ALL run in parallel
- US4 tasks T011 and T012 can run in parallel within the story (report .py vs .js)

---

## Parallel Example: After Phase 2 Completes

```
# All four user stories can launch simultaneously:
Agent A: T007 [US1] direct_activate() endpoint in api/voucher.py
Agent B: T009 [US2] allocation guards in api/allocation.py
Agent C: T010 [US4] report JSON + __init__.py
Agent D: T013 [US5] card.js recipient_note visibility

# After T007 completes:
Agent A: T008 [US1] batch.js Direct Activate button

# After T010 completes:
Agent C: T011 [US4] report Python backend
Agent E: T012 [US4] report JS frontend (parallel with T011)
```

---

## Implementation Strategy

### MVP First (US1 Only)

1. Complete Phase 1: Schema Changes (T001–T004)
2. Complete Phase 2: Validation & Generation (T005–T006)
3. Complete Phase 3: US1 — Direct Activate (T007–T008)
4. **STOP and VALIDATE**: Create a Scholarship batch, generate, export, activate, redeem
5. Deploy if ready — scholarship distribution is immediately usable

### Incremental Delivery

1. Phase 1 + 2 → Foundation ready
2. Add US1 (Phase 3) → Test → Deploy (**MVP: scholarship vouchers work**)
3. Add US2 (Phase 4) → Test → Deploy (cross-purpose guards active)
4. Add US3 (Phase 5) → Already delivered (manual grants)
5. Add US4 (Phase 6) → Test → Deploy (reporting available)
6. Add US5 (Phase 7) → Test → Deploy (recipient notes)
7. Phase 8 → Full test suite + validation

### Files Modified/Created Summary

| File | Action | Tasks |
|---|---|---|
| `doctype/memora_voucher_batch/memora_voucher_batch.json` | MODIFY | T001 |
| `doctype/memora_voucher_batch/memora_voucher_batch.py` | MODIFY | T005 |
| `doctype/memora_voucher_batch/memora_voucher_batch.js` | MODIFY | T008 |
| `doctype/memora_voucher_card/memora_voucher_card.json` | MODIFY | T002 |
| `doctype/memora_voucher_card/memora_voucher_card.js` | MODIFY | T013 |
| `doctype/memora_subscription_transaction/memora_subscription_transaction.json` | MODIFY | T003 |
| `api/voucher.py` | MODIFY | T006, T007 |
| `api/allocation.py` | MODIFY | T009 |
| `report/scholarship_gift_grants/__init__.py` | CREATE | T010 |
| `report/scholarship_gift_grants/scholarship_gift_grants.json` | CREATE | T010 |
| `report/scholarship_gift_grants/scholarship_gift_grants.py` | CREATE | T011 |
| `report/scholarship_gift_grants/scholarship_gift_grants.js` | CREATE | T012 |
| `tests/test_scholarship_vouchers.py` | CREATE | T014 |

---

## Notes

- All paths relative to `memora_admin/memora_admin/` (the Frappe module root)
- This is a **Frappe-only feature** — no FastAPI, no Redis, no game API changes
- Existing sale flows (Generate → Allocate → Redeem) must remain completely unchanged (FR-016)
- `library='Admin-Direct'` is set via direct SQL to bypass Frappe Link validation (R2 decision)
- Export workflow: Generate → Export for Print → Direct Activate → Distribute PINs (export filters `Available` cards)
- Test fixtures use existing season `SEAS-00027` (avoids MySQL partitioning constraints)
