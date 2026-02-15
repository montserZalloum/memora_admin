# Tasks: Integration Tests — Allocation Flow

**Input**: Design documents from `/specs/006-allocation-flow-tests/`
**Prerequisites**: plan.md, spec.md, data-model.md, contracts/test-contracts.md, research.md, quickstart.md

**Tests**: This feature IS the test implementation. All tasks produce test code.

**Organization**: Tasks are grouped by user story (7 test classes across 6 user stories, 23 tests total in a single file).

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependencies)
- **[Story]**: Which user story this task belongs to (e.g., US1, US2, US3)
- Include exact file paths in descriptions

## Path Conventions

- **Test file**: `memora_admin/memora_admin/tests/test_allocation_flow.py` (single new file)
- **Code under test**: `memora_admin/memora_admin/api/allocation.py` (fill_cards, submit_allocation, approve_allocation, reject_allocation)
- **DocType hooks**: `memora_admin/memora_admin/doctype/memora_voucher_allocation/memora_voucher_allocation.py`
- **Existing fixtures**: `memora_admin/memora_admin/tests/voucher_fixtures.py`
- **Existing helpers**: `memora_admin/memora_admin/tests/voucher_helpers.py`
- **Base class**: `memora_admin/memora_admin/tests/voucher_test_base.py`

---

## Phase 1: Setup

**Purpose**: Create test file skeleton with all imports, constants, and base structure

- [X] T001 Create test file skeleton with imports, constants, and docstring in `memora_admin/memora_admin/tests/test_allocation_flow.py`
  - Import `VoucherTestCase` from `voucher_test_base`
  - Import `make_batch`, `make_customer`, `make_product_grant`, `make_allocation` from `voucher_fixtures`
  - Import `generate_batch_sync`, `get_card_statuses`, `fill_and_complete_allocation`, `assert_batch_counters` from `voucher_helpers`
  - Import `fill_cards`, `submit_allocation`, `approve_allocation`, `reject_allocation` from `memora_admin.api.allocation`
  - Import `frappe` and `decimal.Decimal`
  - Add module docstring mapping test classes to user stories and FR requirements
  - Define `SEASON = "SEAS-00027"` constant

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Verify existing infrastructure works and establish shared patterns before writing test logic

**CRITICAL**: No user story tests can begin until imports and base patterns are validated

- [X] T002 Add smoke test `TestAllocationFlowSmoke.test_imports_and_fixtures_available` that verifies all imports succeed and `make_product_grant(season=SEASON)` creates a grant in `memora_admin/memora_admin/tests/test_allocation_flow.py`
- [X] T003 Run smoke test via `bench run-tests --module memora_admin.memora_admin.tests.test_allocation_flow` and verify it passes

**Checkpoint**: Fixture infrastructure confirmed working; test class implementation can begin

---

## Phase 3: User Story 1 — Fill Cards into Allocation (Priority: P1)

**Goal**: Verify fill_cards() correctly selects Available/Allocated cards by type, respects quantity limits, rejects non-Draft allocations, and handles idempotent re-fill.

**Independent Test**: Run `bench run-tests --test TestFillCards` — 5 tests covering TC-01 through TC-05.

### Implementation

- [X] T004 [US1] Implement `TestFillCards.setUpClass` with shared generated batch (10 cards) and no-approval library in `memora_admin/memora_admin/tests/test_allocation_flow.py`
  - `cls.grant = make_product_grant(season=SEASON)`
  - `cls.batch = make_batch(grants=[cls.grant.name])`
  - `generate_batch_sync(cls.batch.name)`
  - `cls.library = make_customer(requires_approval=False)`

- [X] T005 [US1] Implement TC-01 `test_fill_allocate_gets_all_available_cards` — fill Allocate-type with quantity=0 expects filled_count=10 and 10 child rows in `memora_admin/memora_admin/tests/test_allocation_flow.py`
  - FR-001, FR-003: Verify all Available cards filled when quantity=0

- [X] T006 [US1] Implement TC-03 `test_fill_respects_quantity_limit` — fill with quantity=5 expects exactly 5 child rows in `memora_admin/memora_admin/tests/test_allocation_flow.py`
  - FR-003: Verify quantity parameter limits card selection

- [X] T007 [US1] Implement TC-04 `test_fill_rejects_non_draft_allocation` — fill on Completed allocation raises ValidationError in `memora_admin/memora_admin/tests/test_allocation_flow.py`
  - FR-004: Create a completed allocation via `fill_and_complete_allocation()`, then attempt `fill_cards()` and assert `frappe.ValidationError`

- [X] T008 [US1] Implement TC-05 `test_fill_replaces_existing_cards` — re-fill Draft allocation replaces previous child rows in `memora_admin/memora_admin/tests/test_allocation_flow.py`
  - FR-001 edge case: Fill with quantity=0 (10 cards), then re-fill with quantity=5, assert 5 child rows (not 15)

- [X] T009 [US1] Implement TC-02 `test_fill_return_gets_allocated_cards_for_library` — fill Return-type gets Allocated cards belonging to specific library in `memora_admin/memora_admin/tests/test_allocation_flow.py`
  - FR-002: First allocate 5 cards to Library A via `fill_and_complete_allocation()`, then create Return-type allocation, fill, and verify 5 cards filled

- [X] T010 [US1] Run TestFillCards class and verify all 5 tests pass via `bench run-tests --test TestFillCards`

**Checkpoint**: Fill logic validated — cards are correctly selected by type, quantity, and status

---

## Phase 4: User Story 2 — Submit and Approval Workflow (Priority: P1)

**Goal**: Verify submit routes allocations correctly (auto-complete vs pending approval), validates preconditions, and approve/reject work from Pending Approval state.

**Independent Test**: Run `bench run-tests --test TestSubmitAndApproval` — 7 tests covering TC-06 through TC-12.

### Implementation

- [X] T011 [US2] Implement `TestSubmitAndApproval.setUpClass` with shared generated batch, no-approval library, and approval-required library in `memora_admin/memora_admin/tests/test_allocation_flow.py`
  - `cls.no_approval_lib = make_customer(requires_approval=False)`
  - `cls.approval_lib = make_customer(requires_approval=True)`

- [X] T012 [US2] Implement TC-06 `test_submit_auto_completes_no_approval_library` — submit filled allocation for no-approval library returns Completed in `memora_admin/memora_admin/tests/test_allocation_flow.py`
  - FR-005: Create allocation for `no_approval_lib`, fill, submit, assert status="Completed"

- [X] T013 [US2] Implement TC-07 `test_submit_routes_to_pending_approval` — submit for approval-required library returns Pending Approval in `memora_admin/memora_admin/tests/test_allocation_flow.py`
  - FR-006: Create allocation for `approval_lib`, fill, submit, assert status="Pending Approval"

- [X] T014 [US2] Implement TC-08 `test_submit_rejects_empty_allocation` — submit with no cards raises ValidationError("No cards") in `memora_admin/memora_admin/tests/test_allocation_flow.py`
  - FR-007: Create allocation, do NOT fill, attempt submit, assert error

- [X] T015 [US2] Implement TC-09 `test_submit_rejects_mismatched_batch_cards` — submit with cards from wrong batch raises ValidationError in `memora_admin/memora_admin/tests/test_allocation_flow.py`
  - FR-008: Create allocation for batch A, manually append card from batch B, attempt submit

- [X] T016 [US2] Implement TC-10 `test_approve_completes_pending_allocation` — approve Pending Approval allocation transitions to Completed in `memora_admin/memora_admin/tests/test_allocation_flow.py`
  - FR-009: Submit to Pending Approval, then approve, assert status="Completed"

- [X] T017 [US2] Implement TC-11 `test_reject_sets_rejected_with_reason` — reject Pending Approval allocation sets Rejected status and stores reason in `memora_admin/memora_admin/tests/test_allocation_flow.py`
  - FR-010: Submit to Pending Approval, reject with reason="Quality issue", assert status="Rejected" and notes="Quality issue"

- [X] T018 [US2] Implement TC-12 `test_approve_rejects_non_pending_allocation` — approve Draft allocation raises ValidationError in `memora_admin/memora_admin/tests/test_allocation_flow.py`
  - FR-011: Create filled Draft allocation (not submitted), attempt approve, assert error containing "Pending Approval"

- [X] T019 [US2] Run TestSubmitAndApproval class and verify all 7 tests pass via `bench run-tests --test TestSubmitAndApproval`

**Checkpoint**: Approval workflow validated — auto-complete, approval routing, approve, reject, and precondition checks all working

---

## Phase 5: User Story 3 — Card State Updates on Completion (Priority: P1)

**Goal**: Verify that allocation completion correctly mutates card fields (status, library, allocation, sale_model) for both Allocate and Return types.

**Independent Test**: Run `bench run-tests --test TestCardStateOnAllocate` and `bench run-tests --test TestCardStateOnReturn` — 4 tests covering TC-13 through TC-16.

### Implementation — TestCardStateOnAllocate

- [X] T020 [US3] Implement `TestCardStateOnAllocate.setUpClass` — create batch, allocate 5 of 10 cards to library via `fill_and_complete_allocation()` in `memora_admin/memora_admin/tests/test_allocation_flow.py`
  - Setup: grant → batch (10 cards) → generate → library → allocate 5 cards (Prepaid)

- [X] T021 [US3] Implement TC-13 `test_allocated_cards_have_correct_fields` — verify each allocated card has status=Allocated, library, allocation, sale_model in `memora_admin/memora_admin/tests/test_allocation_flow.py`
  - FR-012: Query each card from allocation child table, assert all 4 fields set correctly

- [X] T022 [US3] Implement TC-14 `test_remaining_cards_stay_available` — verify non-allocated cards retain Available status with null fields in `memora_admin/memora_admin/tests/test_allocation_flow.py`
  - FR-012 boundary: Query remaining 5 cards, assert status="Available", library=None, allocation=None

### Implementation — TestCardStateOnReturn

- [X] T023 [US3] Implement `TestCardStateOnReturn.setUpClass` — allocate cards then complete a Return-type allocation in `memora_admin/memora_admin/tests/test_allocation_flow.py`
  - Setup: grant → batch → generate → library → allocate 5 cards → return those 5 cards

- [X] T024 [US3] Implement TC-15 `test_returned_cards_cleared_with_return_allocation` — verify returned cards have status=Available, cleared fields, return_allocation set in `memora_admin/memora_admin/tests/test_allocation_flow.py`
  - FR-013, FR-020: Each returned card: status="Available", library=None, allocation=None, sale_model=None, return_allocation=return_alloc.name

- [X] T025 [US3] Implement TC-16 `test_return_with_zero_eligible_cards` — Return-type fill for library with no allocated cards fills nothing in `memora_admin/memora_admin/tests/test_allocation_flow.py`
  - FR-002 edge case: Library B with no allocated cards in batch, fill returns filled_count=0

- [X] T026 [US3] Run TestCardStateOnAllocate and TestCardStateOnReturn classes and verify all 4 tests pass

**Checkpoint**: Card state integrity validated — Allocate sets fields, Return clears fields, boundary cases handled

---

## Phase 6: User Story 4 — Batch Counter and Status Updates (Priority: P2)

**Goal**: Verify batch allocated_count is recounted after allocation and batch transitions from Generated to Active on first allocation.

**Independent Test**: Run `bench run-tests --test TestBatchCountersAndStatus` — 2 tests covering TC-17 and TC-18.

### Implementation

- [ ] T027 [US4] Implement `TestBatchCountersAndStatus.setUpClass` — create batch with 10 cards, allocate 5, store batch name for counter verification in `memora_admin/memora_admin/tests/test_allocation_flow.py`

- [ ] T028 [US4] Implement TC-17 `test_allocated_count_updated` — verify batch.allocated_count=5 after allocating 5 of 10 cards using `assert_batch_counters()` in `memora_admin/memora_admin/tests/test_allocation_flow.py`
  - FR-014: Use `assert_batch_counters(self, batch.name, allocated_count=5, generated_count=10)`

- [ ] T029 [US4] Implement TC-18 `test_batch_transitions_generated_to_active` — verify batch.status changes from Generated to Active on first allocation in `memora_admin/memora_admin/tests/test_allocation_flow.py`
  - FR-015: Reload batch after allocation completion, assert status="Active"

- [ ] T030 [US4] Run TestBatchCountersAndStatus class and verify both tests pass

**Checkpoint**: Batch metadata validated — counters accurate, status lifecycle correct

---

## Phase 7: User Story 5 — Prepaid Invoice Creation (Priority: P2)

**Goal**: Verify Prepaid allocations create linked Sales Invoices with correct amounts after commission, and Consignment allocations create no invoice.

**Independent Test**: Run `bench run-tests --test TestPrepaidInvoiceOnAllocation` — 3 tests covering TC-19 through TC-21.

### Implementation

- [X] T031 [US5] Implement `TestPrepaidInvoiceOnAllocation.setUpClass` — create batch with face_value, library with 10% commission, complete Prepaid allocation in `memora_admin/memora_admin/tests/test_allocation_flow.py`
  - Setup: grant → batch (face_value=10) → generate → library (commission_type="Percentage", commission_value="10") → allocate 5 cards (Prepaid)

- [X] T032 [US5] Implement TC-19 `test_prepaid_creates_linked_sales_invoice` — verify allocation.sales_invoice is set and linked Sales Invoice has docstatus=1 in `memora_admin/memora_admin/tests/test_allocation_flow.py`
  - FR-016: Reload allocation, assert sales_invoice is not None, load invoice and assert docstatus=1

- [X] T033 [US5] Implement TC-20 `test_invoice_amount_reflects_commission` — verify invoice rate=9.0 (10 - 10%), qty=5, correct customer and item_code in `memora_admin/memora_admin/tests/test_allocation_flow.py`
  - FR-017: Load Sales Invoice, check items[0].rate, items[0].qty, customer, items[0].item_code="MEMORA-VOUCHER-CARD"

- [X] T034 [US5] Implement TC-21 `test_consignment_creates_no_invoice` — complete Consignment allocation and verify no Sales Invoice created in `memora_admin/memora_admin/tests/test_allocation_flow.py`
  - FR-016 negative: Create separate Consignment allocation, complete it, assert sales_invoice is None/empty

- [X] T035 [US5] Run TestPrepaidInvoiceOnAllocation class and verify all 3 tests pass

**Checkpoint**: Invoice creation validated — Prepaid creates correct invoices, Consignment creates none

---

## Phase 8: User Story 6 — State Machine Enforcement (Priority: P2)

**Goal**: Verify invalid state transitions are rejected and terminal states cannot be escaped.

**Independent Test**: Run `bench run-tests --test TestStateMachineEnforcement` — 2 tests covering TC-22 and TC-23.

### Implementation

- [X] T036 [US6] Implement `TestStateMachineEnforcement.setUpClass` — create batch and approval-required library for state machine testing in `memora_admin/memora_admin/tests/test_allocation_flow.py`

- [X] T037 [US6] Implement TC-22 `test_invalid_skip_transition_rejected` — set Draft allocation status directly to Completed and save raises ValidationError in `memora_admin/memora_admin/tests/test_allocation_flow.py`
  - FR-018: Create Draft allocation, set alloc.status="Completed", call alloc.save(), assert error containing "Invalid allocation status transition"

- [X] T038 [US6] Implement TC-23 `test_terminal_state_blocks_transitions` — set Completed allocation status to Draft and save raises ValidationError in `memora_admin/memora_admin/tests/test_allocation_flow.py`
  - FR-019: Complete an allocation, set alloc.status="Draft", call alloc.save(), assert error containing "terminal state"

- [X] T039 [US6] Run TestStateMachineEnforcement class and verify both tests pass

**Checkpoint**: State machine enforcement validated — invalid transitions blocked, terminal states locked

---

## Phase 9: Polish & Cross-Cutting Concerns

**Purpose**: Full suite validation, cleanup, and performance verification

- [X] T040 Remove smoke test class (`TestAllocationFlowSmoke`) from `memora_admin/memora_admin/tests/test_allocation_flow.py` — no longer needed after all tests implemented
- [X] T041 Run full allocation test suite via `bench run-tests --module memora_admin.memora_admin.tests.test_allocation_flow` and verify all 23 tests pass (SC-001)
- [X] T042 Verify test execution completes within 60 seconds (SC-005) — check timing output
- [X] T043 Run quickstart.md validation: execute all three run modes (full module, single class, single method) from quickstart.md and confirm each works

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: No dependencies — create the file
- **Foundational (Phase 2)**: Depends on Phase 1 — validates imports work
- **US1 Fill Cards (Phase 3)**: Depends on Phase 2 — first test class
- **US2 Submit/Approval (Phase 4)**: Depends on Phase 2 — independent of US1
- **US3 Card State (Phase 5)**: Depends on Phase 2 — uses `fill_and_complete_allocation()` from helpers
- **US4 Batch Counters (Phase 6)**: Depends on Phase 2 — independent of US1-3
- **US5 Invoice (Phase 7)**: Depends on Phase 2 — needs commission library setup
- **US6 State Machine (Phase 8)**: Depends on Phase 2 — independent of US1-5
- **Polish (Phase 9)**: Depends on Phases 3-8 — validates entire suite

### User Story Dependencies

- **US1 (P1)**: Independent — tests fill_cards() in isolation
- **US2 (P1)**: Independent — tests submit/approve/reject in isolation
- **US3 (P1)**: Independent — tests card field mutations post-completion
- **US4 (P2)**: Independent — tests batch counter/status updates
- **US5 (P2)**: Independent — tests invoice creation
- **US6 (P2)**: Independent — tests state machine enforcement

All user stories can theoretically be implemented in parallel since they write to separate test classes within the same file. In practice, sequential execution by priority (P1 first) is recommended.

### Within Each User Story

1. Implement `setUpClass` (shared fixtures)
2. Implement each test method (TC contracts)
3. Run and verify the test class passes
4. Move to next story

### Parallel Opportunities

Since all tasks write to the same file (`test_allocation_flow.py`), true parallel execution is limited. However:

- **Phase 3 (US1)**: T005, T006, T007, T008 can be written in any order after T004 (setUpClass)
- **Phase 4 (US2)**: T012-T018 can be written in any order after T011 (setUpClass)
- **Phase 5 (US3)**: T021-T022 are parallel after T020; T024-T025 are parallel after T023
- **Phase 6 (US4)**: T028-T029 are parallel after T027
- **Phase 7 (US5)**: T032-T034 are parallel after T031
- **Phase 8 (US6)**: T037-T038 are parallel after T036

---

## Parallel Example: User Story 1 (Phase 3)

```bash
# Sequential: setUpClass must come first
Task: T004 — Implement TestFillCards.setUpClass

# Then tests can be written in any order:
Task: T005 — TC-01: fill_allocate_gets_all_available_cards
Task: T006 — TC-03: fill_respects_quantity_limit
Task: T007 — TC-04: fill_rejects_non_draft_allocation
Task: T008 — TC-05: fill_replaces_existing_cards
Task: T009 — TC-02: fill_return_gets_allocated_cards_for_library

# Validation after all tests written:
Task: T010 — Run and verify TestFillCards passes
```

---

## Implementation Strategy

### MVP First (User Story 1 Only)

1. Complete Phase 1: Setup (T001)
2. Complete Phase 2: Foundational (T002-T003)
3. Complete Phase 3: US1 — Fill Cards (T004-T010)
4. **STOP and VALIDATE**: 5 tests passing, fill logic confirmed
5. Proceed to US2 if stable

### Incremental Delivery

1. Setup + Foundational (T001-T003) → File ready
2. Add US1: Fill Cards (5 tests) → Validate → 5/23 passing
3. Add US2: Submit/Approval (7 tests) → Validate → 12/23 passing
4. Add US3: Card State (4 tests) → Validate → 16/23 passing
5. Add US4: Batch Counters (2 tests) → Validate → 18/23 passing
6. Add US5: Invoice (3 tests) → Validate → 21/23 passing
7. Add US6: State Machine (2 tests) → Validate → 23/23 passing
8. Polish: Full suite + performance check

### Suggested MVP Scope

**Phase 3 (US1 — Fill Cards)** delivers the first 5 tests with immediate value: confirms the entry point of the allocation workflow works correctly.

---

## Summary

| Phase | User Story | Tests | Task Range | FRs Covered |
|-------|-----------|-------|------------|-------------|
| 1 | Setup | — | T001 | — |
| 2 | Foundational | 1 (smoke) | T002-T003 | — |
| 3 | US1: Fill Cards | 5 | T004-T010 | FR-001–FR-004 |
| 4 | US2: Submit/Approval | 7 | T011-T019 | FR-005–FR-011 |
| 5 | US3: Card State | 4 | T020-T026 | FR-012, FR-013, FR-020 |
| 6 | US4: Batch Counters | 2 | T027-T030 | FR-014, FR-015 |
| 7 | US5: Invoice | 3 | T031-T035 | FR-016, FR-017 |
| 8 | US6: State Machine | 2 | T036-T039 | FR-018, FR-019 |
| 9 | Polish | — | T040-T043 | SC-001, SC-005 |
| **Total** | **6 stories** | **23 tests** | **43 tasks** | **FR-001–FR-020** |

## Notes

- All 23 test contracts (TC-01 through TC-23) are mapped to specific tasks
- All 20 functional requirements (FR-001 through FR-020) are covered
- All 6 success criteria (SC-001 through SC-006) have corresponding validation tasks
- Season `SEAS-00027` is used throughout to avoid MySQL partitioning constraints
- No new fixture factories or helpers are needed — all existing infrastructure reused (SC-004)
