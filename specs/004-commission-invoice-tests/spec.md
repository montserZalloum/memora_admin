# Feature Specification: Commission & Invoice Unit Tests

**Feature Branch**: `004-commission-invoice-tests`
**Created**: 2026-02-15
**Status**: Draft
**Input**: Phase 4 of Voucher Test Suite Plan — Unit Tests for Commission & Invoice (~18 tests)

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Commission Calculation Correctness (Priority: P1)

A developer modifying the voucher commission logic needs confidence that percentage-based, fixed-amount, and zero-commission scenarios all produce correct financial results with no rounding errors, so that libraries are billed accurately.

**Why this priority**: Commission calculation directly affects revenue. Incorrect math means over- or under-billing libraries, which is a financial and trust risk.

**Independent Test**: Can be fully tested by running the commission test suite in isolation (`test_commission.py`) — no database access needed. Delivers confidence that all commission math is correct.

**Acceptance Scenarios**:

1. **Given** no commission configured (None/empty), **When** commission is calculated for a 5.00 face value, **Then** net per card equals full face value and commission is zero.
2. **Given** a 10% percentage commission, **When** commission is calculated for a 5.00 face value, **Then** commission is 0.50 and net per card is 4.50.
3. **Given** a fixed 1.00 commission, **When** commission is calculated for a 5.00 face value, **Then** commission is 1.00 and net per card is 4.00.
4. **Given** a 33.33% commission on a 10.00 face value, **When** commission is calculated, **Then** values use correct decimal precision with no floating-point rounding errors.
5. **Given** net per card of 4.50 and quantity 10, **When** totals are calculated, **Then** net total is exactly 45.00.
6. **Given** a face value of 0, **When** commission is calculated, **Then** all output values are zero.
7. **Given** an unrecognized commission type string, **When** commission is calculated, **Then** commission defaults to zero (full face value invoiced).

---

### User Story 2 - Commission Resolution Priority (Priority: P1)

A developer needs to verify that the three-tier commission resolution chain (product grant override > library default > zero) correctly determines which commission applies to a given allocation, so that per-product pricing agreements are honored.

**Why this priority**: The priority chain determines which rate applies. Getting it wrong means the wrong party absorbs the cost.

**Independent Test**: Can be tested by setting up batch grants with/without commission overrides and customers with/without default commission, then verifying which values are resolved.

**Acceptance Scenarios**:

1. **Given** a batch grant with commission type and value set, **When** commission is resolved for that grant's batch and library, **Then** the grant-level values take precedence over customer defaults.
2. **Given** a batch grant with no commission override and a customer with commission defaults, **When** commission is resolved, **Then** the customer-level values are used.
3. **Given** no commission configured at grant level or customer level, **When** commission is resolved, **Then** the result is (None, None), meaning zero commission.

---

### User Story 3 - Invoice Creation and Submission (Priority: P1)

A developer needs to verify that prepaid allocations produce correctly structured, submitted Sales Invoices with the right customer, item code, quantities, and rates, so that financial records are accurate and auditable.

**Why this priority**: Invoices are the primary financial output of the voucher system. Incorrect invoices cause accounting reconciliation failures.

**Independent Test**: Can be tested by creating a prepaid allocation, completing it, and verifying the resulting Sales Invoice fields.

**Acceptance Scenarios**:

1. **Given** a completed prepaid allocation, **When** the invoice is created, **Then** the Sales Invoice is submitted (docstatus=1).
2. **Given** a completed prepaid allocation for a specific library, **When** the invoice is created, **Then** the invoice customer matches the allocation library.
3. **Given** a completed prepaid allocation, **When** the invoice is created, **Then** the line item uses the standard voucher item code.
4. **Given** a completed prepaid allocation with known commission and card count, **When** the invoice is created, **Then** the rate equals net per card and quantity equals the allocated card count.

---

### User Story 4 - Credit Note Creation for Returns (Priority: P2)

A developer needs to verify that prepaid return allocations produce correctly structured Credit Notes that reference the original invoice, use negative quantities, and are submitted, so that returns are properly reflected in accounting.

**Why this priority**: Credit Notes reverse financial transactions. Errors here create accounting imbalances that are difficult to trace.

**Independent Test**: Can be tested by creating an allocation, invoicing it, then processing a return and verifying the Credit Note fields.

**Acceptance Scenarios**:

1. **Given** a completed prepaid return allocation, **When** the credit note is created, **Then** it is marked as a return and references the original invoice.
2. **Given** a completed prepaid return allocation, **When** the credit note is created, **Then** the item quantity is negative.
3. **Given** a completed prepaid return allocation, **When** the credit note is created, **Then** the Credit Note is submitted (docstatus=1).

---

### User Story 5 - Prepaid Invoice Full Flow (Priority: P2)

A developer needs to verify the end-to-end flow from allocation through commission calculation to invoice creation and linkage, confirming all pieces work together correctly.

**Why this priority**: Integration between commission and invoicing must produce consistent results. This catches mismatches between unit-level correctness and orchestration-level wiring.

**Independent Test**: Can be tested by running a full prepaid allocation workflow and verifying the resulting invoice is linked to the allocation with correct amounts.

**Acceptance Scenarios**:

1. **Given** a batch with known face value and a library with known commission, **When** a prepaid allocation is completed, **Then** a Sales Invoice is created with the correct net amount and is linked to the allocation.

---

### Edge Cases

- What happens when face value is zero? All financial outputs should be zero with no errors.
- What happens with an unknown/unrecognized commission type string? System defaults to zero commission.
- What happens when percentage commission produces a repeating decimal (e.g., 33.33% of 10.00)? System uses correct decimal rounding (2 decimal places, round half up).
- What happens when both grant-level and customer-level commission exist? Grant-level takes precedence.
- What happens when a credit note is created for a return? Quantity is negated and the original invoice is referenced.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: System MUST calculate zero commission when no commission type/value is configured, returning full face value as net.
- **FR-002**: System MUST calculate percentage commission as `face_value * commission_value / 100`, rounded to 2 decimal places.
- **FR-003**: System MUST calculate fixed commission by using the commission value directly, rounded to 2 decimal places.
- **FR-004**: System MUST use precise decimal arithmetic (not floating-point) for all commission and invoice amount calculations.
- **FR-005**: System MUST multiply net per card by quantity to derive net total, maintaining correct precision.
- **FR-006**: System MUST return all-zero results when face value is zero.
- **FR-007**: System MUST treat unrecognized commission types as zero commission.
- **FR-008**: System MUST resolve commission using a three-tier priority: (1) batch grant override, (2) customer default, (3) zero.
- **FR-009**: System MUST create a submitted Sales Invoice for completed prepaid allocations.
- **FR-010**: System MUST set the invoice customer to match the allocation library.
- **FR-011**: System MUST use the standard voucher item code on all invoice line items.
- **FR-012**: System MUST set invoice rate to net per card and quantity to the allocated card count.
- **FR-013**: System MUST create a submitted Credit Note for completed prepaid return allocations, referencing the original invoice.
- **FR-014**: System MUST negate the quantity on Credit Note line items.
- **FR-015**: System MUST link the created invoice back to the allocation record.

### Key Entities

- **Commission Calculation**: Accepts face value, quantity, commission type, and commission value; outputs per-card commission, total commission, net per card, and net total — all as precise decimal values.
- **Commission Resolution**: Determines applicable commission by checking batch grant overrides first, then customer defaults, then falling back to zero.
- **Sales Invoice**: Financial document representing a billing event for prepaid voucher allocations.
- **Credit Note**: Return invoice reversing a previous Sales Invoice for returned prepaid allocations.
- **Allocation**: Links a voucher batch to a library customer, triggering financial documents on completion.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: All 18 commission and invoice unit tests pass consistently with zero failures across repeated runs.
- **SC-002**: Commission calculations produce identical results to manual decimal arithmetic for all test cases (no floating-point drift).
- **SC-003**: Every commission type (percentage, fixed, none, unknown) is covered by at least one dedicated test.
- **SC-004**: The three-tier commission resolution priority is verified by tests that exercise each tier independently.
- **SC-005**: Invoice and Credit Note tests verify document status, customer, item, amounts, and linkage fields.
- **SC-006**: Tests for the full prepaid flow confirm end-to-end correctness from allocation to invoice linkage.
- **SC-007**: Test suite executes in under 30 seconds on the development environment.

## Assumptions

- The existing test infrastructure (Phase 2: `voucher_fixtures.py`, `voucher_helpers.py`, `voucher_test_base.py`) and Phase 3 tests (`test_generator.py`, `test_crypto.py`) are complete and available.
- Commission calculation tests (`test_commission.py`) are pure unit tests — no database access needed.
- Commission resolution tests require database access (to query batch grants and customer records).
- Invoice tests require database access to create and verify Sales Invoice / Credit Note documents.
- The existing `make_customer()` fixture already supports `commission_type` and `commission_value` parameters.
- The existing `make_batch()` fixture supports `grants` parameter for batch grant child rows.
- Test season `SEAS-00027` is used for any tests requiring season context.
- The standard voucher item code exists in the test environment (validated by test base class).
