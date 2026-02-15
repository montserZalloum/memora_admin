# Tasks: Commission & Invoice Unit Tests

**Input**: Design documents from `/specs/004-commission-invoice-tests/`
**Prerequisites**: plan.md, spec.md, data-model.md, contracts/, research.md, quickstart.md

**Organization**: Tasks are grouped by user story. This feature IS tests — all tasks produce test code, no production code changes.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependencies)
- **[Story]**: Which user story this task belongs to (e.g., US1, US2)
- Include exact file paths in descriptions

## Path Conventions

- **Test files**: `memora_admin/memora_admin/tests/`
- **Source under test**: `memora_admin/memora_admin/services/voucher/commission.py`, `memora_admin/memora_admin/services/voucher/invoice.py`
- **Fixtures**: `memora_admin/memora_admin/tests/voucher_fixtures.py`
- **Helpers**: `memora_admin/memora_admin/tests/voucher_helpers.py`
- **Base class**: `memora_admin/memora_admin/tests/voucher_test_base.py`

---

## Phase 1: Setup

**Purpose**: Create test file skeletons with correct imports and class hierarchy

- [X] T001 Create `test_commission.py` with imports (`decimal.Decimal`, `unittest.TestCase`, `VoucherTestCase` from `voucher_test_base`, `calculate_commission` and `resolve_commission` from `memora_admin.memora_admin.services.voucher.commission`, plus fixtures `make_product_grant`, `make_batch`, `make_customer`) and empty class stubs for `TestCalculateCommission(unittest.TestCase)` and `TestResolveCommission(VoucherTestCase)` in `memora_admin/memora_admin/tests/test_commission.py`
- [X] T002 [P] Create `test_invoice.py` with imports (`frappe`, `Decimal`, `VoucherTestCase`, fixtures `make_product_grant`, `make_batch`, `make_customer`, helpers `generate_batch_sync`, `fill_and_complete_allocation`) and empty class stubs for `TestCreateInvoice(VoucherTestCase)`, `TestCreateCreditNote(VoucherTestCase)`, and `TestPrepaidInvoiceFlow(VoucherTestCase)` in `memora_admin/memora_admin/tests/test_invoice.py`

**Checkpoint**: Both test files exist with valid Python syntax and correct imports. Running them produces 0 tests (no test methods yet).

---

## Phase 2: User Story 1 — Commission Calculation Correctness (Priority: P1) — MVP

**Goal**: Verify all commission math (percentage, fixed, zero, unknown) produces correct `Decimal` results with no floating-point rounding errors.

**Independent Test**: `bench run-tests --app memora_admin --module memora_admin.memora_admin.tests.test_commission` — `TestCalculateCommission` class only (pure unit, no DB).

**Contract**: `specs/004-commission-invoice-tests/contracts/test-commission-contract.md` — Tests 1–8

### Implementation

- [X] T003 [US1] Implement `test_no_commission_none_type` and `test_no_commission_empty_string` (FR-001) — verify `None`/empty inputs yield zero commission and full face value (`face_value="5.00"`, `quantity=10`) — per contract Tests 1–2 in `memora_admin/memora_admin/tests/test_commission.py::TestCalculateCommission`
- [X] T004 [US1] Implement `test_percentage_commission` (FR-002, 10% of 5.00 → 0.50 commission), `test_fixed_amount_commission` (FR-003, fixed 1.00 → exact deduction), and `test_unknown_commission_type_defaults_to_zero` (FR-007, unknown type → zero commission) — per contract Tests 3, 4, 8 in `memora_admin/memora_admin/tests/test_commission.py::TestCalculateCommission`
- [X] T005 [US1] Implement `test_repeating_decimal_precision` (FR-004, 33.33% of 10.00 → correct rounding), `test_quantity_multiplication` (FR-005, net_per_card * qty = net_total), and `test_zero_face_value` (FR-006, all zeros) — per contract Tests 5, 6, 7 in `memora_admin/memora_admin/tests/test_commission.py::TestCalculateCommission`
- [X] T006 [US1] Run `TestCalculateCommission` — verify all 8 pure unit tests pass with `bench run-tests --app memora_admin --module memora_admin.memora_admin.tests.test_commission`

**Checkpoint**: 8 commission math tests passing. All assertions use `Decimal` comparisons (Constitution Principle III). No DB access needed.

---

## Phase 3: User Story 2 — Commission Resolution Priority (Priority: P1)

**Goal**: Verify the three-tier commission resolution chain (grant override > customer default > zero) correctly determines which commission applies.

**Independent Test**: `bench run-tests --app memora_admin --module memora_admin.memora_admin.tests.test_commission` — `TestResolveCommission` class (requires DB).

**Contract**: `specs/004-commission-invoice-tests/contracts/test-commission-contract.md` — Tests 9–11

### Implementation

- [X] T007 [US2] Implement `test_grant_level_takes_precedence` (FR-008) — create batch with grant-level commission (`commission_type="Percentage"`, `commission_value="15"` set via `frappe.db.set_value()` per research.md R6) + customer with different defaults (`Fixed Amount`/`2.00`), verify grant wins → `("Percentage", "15")` — per contract Test 9 in `memora_admin/memora_admin/tests/test_commission.py::TestResolveCommission`
- [X] T008 [US2] Implement `test_customer_default_when_no_grant_override` (FR-008) — batch with no grant commission + customer with defaults, verify customer values used → `("Fixed Amount", "2.00")` — and `test_no_commission_returns_none_none` (FR-008) — neither set → `(None, None)` — per contract Tests 10–11 in `memora_admin/memora_admin/tests/test_commission.py::TestResolveCommission`
- [X] T009 [US2] Run full `test_commission.py` — verify all 11 tests pass (8 pure + 3 DB) with `bench run-tests --app memora_admin --module memora_admin.memora_admin.tests.test_commission`

**Checkpoint**: All 11 commission tests passing. Commission calculation and resolution fully covered.

---

## Phase 4: User Story 3 — Invoice Creation and Submission (Priority: P1)

**Goal**: Verify prepaid allocations produce correctly structured, submitted Sales Invoices with right customer, item code, quantities, and rates.

**Independent Test**: `bench run-tests --app memora_admin --module memora_admin.memora_admin.tests.test_invoice` — `TestCreateInvoice` class.

**Contract**: `specs/004-commission-invoice-tests/contracts/test-invoice-contract.md` — Tests 1–4

### Implementation

- [X] T010 [US3] Implement shared `setUpClass` in `TestCreateInvoice` — create `make_product_grant(season="SEAS-00027")`, `make_batch(face_value=10, grants=[grant.name])`, `generate_batch_sync(batch.name)`, `make_customer(commission_type="Percentage", commission_value="10")`, `fill_and_complete_allocation(batch.name, customer.name, quantity=5)`, reload allocation, load Sales Invoice — in `memora_admin/memora_admin/tests/test_invoice.py::TestCreateInvoice`
- [X] T011 [US3] Implement `test_invoice_is_submitted` (FR-009, `si.docstatus == 1`) and `test_invoice_customer_matches_allocation` (FR-010, `si.customer == customer.name`) — per contract Tests 1–2 in `memora_admin/memora_admin/tests/test_invoice.py::TestCreateInvoice`
- [X] T012 [US3] Implement `test_invoice_uses_voucher_item_code` (FR-011, `si.items[0].item_code == "MEMORA-VOUCHER-CARD"`) and `test_invoice_rate_and_quantity` (FR-012, `rate == 9.0`, `qty == 5`) — per contract Tests 3–4 in `memora_admin/memora_admin/tests/test_invoice.py::TestCreateInvoice`
- [X] T013 [US3] Run `TestCreateInvoice` — verify all 4 tests pass with `bench run-tests --app memora_admin --module memora_admin.memora_admin.tests.test_invoice`

**Checkpoint**: 4 invoice tests passing. Sales Invoice creation fully validated (docstatus, customer, item code, rate, quantity).

---

## Phase 5: User Story 4 — Credit Note Creation for Returns (Priority: P2)

**Goal**: Verify prepaid return allocations produce correctly structured Credit Notes that reference the original invoice, use negative quantities, and are submitted.

**Independent Test**: `bench run-tests --app memora_admin --module memora_admin.memora_admin.tests.test_invoice` — `TestCreateCreditNote` class.

**Contract**: `specs/004-commission-invoice-tests/contracts/test-invoice-contract.md` — Tests 5–7

### Implementation

- [X] T014 [US4] Implement shared `setUpClass` in `TestCreateCreditNote` — create grant/batch/generate/customer/allocation (same pattern as US3), then create return allocation referencing original, complete it, reload, load Credit Note — in `memora_admin/memora_admin/tests/test_invoice.py::TestCreateCreditNote`
- [X] T015 [US4] Implement `test_credit_note_is_return_with_reference` (FR-013, `cn.is_return == 1`, `cn.return_against == original_si_name`), `test_credit_note_has_negative_quantity` (FR-014, `cn.items[0].qty < 0`), and `test_credit_note_is_submitted` (FR-013, `cn.docstatus == 1`) — per contract Tests 5–7 in `memora_admin/memora_admin/tests/test_invoice.py::TestCreateCreditNote`
- [X] T016 [US4] Run `TestCreateCreditNote` — verify all 3 tests pass with `bench run-tests --app memora_admin --module memora_admin.memora_admin.tests.test_invoice`

**Checkpoint**: 3 credit note tests passing. Credit Note creation fully validated (is_return, return_against, negative qty, docstatus).

---

## Phase 6: User Story 5 — Prepaid Invoice Full Flow (Priority: P2)

**Goal**: Verify end-to-end flow from allocation through commission calculation to invoice creation and linkage.

**Independent Test**: `bench run-tests --app memora_admin --module memora_admin.memora_admin.tests.test_invoice` — `TestPrepaidInvoiceFlow` class.

**Contract**: `specs/004-commission-invoice-tests/contracts/test-invoice-contract.md` — Test 8

### Implementation

- [X] T017 [US5] Implement `test_full_prepaid_flow_creates_linked_invoice` (FR-015, SC-006) — create batch (`face_value=10`), customer (`commission_type="Percentage"`, `commission_value="20"`), allocate 5 cards, verify: `alloc.sales_invoice` is not None, `si.docstatus == 1`, `si.customer == customer.name`, `si.items[0].item_code == "MEMORA-VOUCHER-CARD"`, `si.items[0].qty == 5`, `si.items[0].rate == 8.0`, `si.items[0].amount == 40.0` — per contract Test 8 in `memora_admin/memora_admin/tests/test_invoice.py::TestPrepaidInvoiceFlow`
- [X] T018 [US5] Run full `test_invoice.py` — verify all 8 tests pass (4 invoice + 3 credit note + 1 flow) with `bench run-tests --app memora_admin --module memora_admin.memora_admin.tests.test_invoice`

**Checkpoint**: Full flow test passing. End-to-end commission → invoice pipeline validated.

---

## Phase 7: Polish & Cross-Cutting Concerns

**Purpose**: Final validation across all tests, performance verification, cleanup

- [X] T019 Run full voucher test suite — verify all 19 tests pass (11 commission + 8 invoice) with `bench run-tests --app memora_admin`
- [X] T020 Verify test execution time is under 30 seconds (SC-007) and all success criteria (SC-001 through SC-006) are met
- [X] T021 Run quickstart.md validation commands from `specs/004-commission-invoice-tests/quickstart.md`

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: No dependencies — can start immediately
- **US1 (Phase 2)**: Depends on T001 (`test_commission.py` skeleton)
- **US2 (Phase 3)**: Depends on Phase 2 completion (same file, sequential)
- **US3 (Phase 4)**: Depends on T002 (`test_invoice.py` skeleton) — **can run in parallel with Phases 2–3**
- **US4 (Phase 5)**: Depends on Phase 4 completion (same file, credit note needs invoice setup pattern)
- **US5 (Phase 6)**: Depends on Phase 4 completion (same file)
- **Polish (Phase 7)**: Depends on all user stories complete

### User Story Dependencies

- **US1 (P1)**: Can start after T001 — no dependencies on other stories
- **US2 (P1)**: Depends on T001 — independent of US3/US4/US5 (different file)
- **US3 (P1)**: Can start after T002 — independent of US1/US2 (different file)
- **US4 (P2)**: Logically follows US3 (same file, credit note needs prior invoice) — independent of US1/US2
- **US5 (P2)**: Logically follows US3 (same file) — independent of US1/US2

### Parallel Opportunities

Two independent tracks can run simultaneously after Setup:

```
Track A (test_commission.py):  T001 → US1 (T003–T006) → US2 (T007–T009)
Track B (test_invoice.py):     T002 → US3 (T010–T013) → US4 (T014–T016) → US5 (T017–T018)
```

### Within Each User Story

1. Implement test methods per contract specification
2. All assertions use `Decimal` for financial values (Constitution Principle III)
3. Use `frappe.db.set_value()` for batch grant commission fields (research.md R6)
4. Use season `SEAS-00027` for all DB fixtures
5. Run and verify tests pass before moving to next story
6. Commit after each story's tests pass

---

## Parallel Example

```bash
# After Phase 1 (Setup) completes, launch both tracks simultaneously:

# Track A: Commission tests (test_commission.py)
Task: "Implement TestCalculateCommission (8 tests) in test_commission.py"  # US1
Task: "Implement TestResolveCommission (3 tests) in test_commission.py"   # US2

# Track B: Invoice tests (test_invoice.py) — can run at same time as Track A
Task: "Implement TestCreateInvoice (4 tests) in test_invoice.py"          # US3
Task: "Implement TestCreateCreditNote (3 tests) in test_invoice.py"       # US4
Task: "Implement TestPrepaidInvoiceFlow (1 test) in test_invoice.py"      # US5
```

---

## Implementation Strategy

### MVP First (User Story 1 Only)

1. Complete Phase 1: Setup (create both file skeletons)
2. Complete Phase 2: US1 — Commission Calculation (8 pure tests)
3. **STOP and VALIDATE**: Run `TestCalculateCommission` — all 8 tests pass, no DB needed
4. This alone covers 42% of tests (8/19) and validates all commission math

### Incremental Delivery

1. Setup → File skeletons created
2. US1 (Commission Math) → 8 tests → Pure unit, fastest feedback loop
3. US2 (Resolution Chain) → +3 tests (11 total) → Commission fully covered
4. US3 (Invoice Creation) → +4 tests (15 total) → Invoice workflow validated
5. US4 (Credit Notes) → +3 tests (18 total) → Returns covered
6. US5 (Full Flow) → +1 test (19 total) → E2E integration confirmed
7. Polish → All 19 tests pass together under 30 seconds

### Key Patterns (from research.md)

- **Decimal assertions**: Always `assertEqual(result, Decimal("x.xx"))` — never float comparisons
- **Batch grant commission**: Set via `frappe.db.set_value()` after `make_batch()` (R6)
- **Season**: Use `SEAS-00027` for all DB fixtures
- **Reload before assert**: `doc.reload()` before checking fields set by background logic
- **Base classes**: `unittest.TestCase` for pure math, `VoucherTestCase` for DB tests

---

## Notes

- [P] tasks = different files, no dependencies
- [Story] label maps task to specific user story for traceability
- Each user story is independently completable and testable
- Commit after each user story's tests pass
- No production code changes — all tasks produce test code only
- Reference contracts for exact inputs, expected outputs, and assertion values
- Total: 21 tasks, 19 test methods across 2 files and 5 test classes
