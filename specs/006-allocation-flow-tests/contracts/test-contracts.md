# Test Contracts: Integration Tests — Allocation Flow

**Feature**: 006-allocation-flow-tests | **Date**: 2026-02-15

## Overview

Each contract defines: test class, test method, preconditions (Given), action (When), and expected outcome (Then). Maps to spec acceptance scenarios and FR requirements.

---

## Class 1: TestFillCards (US1 — 5 tests)

### TC-01: Fill Allocate type gets all Available cards
- **FR**: FR-001, FR-003
- **Given**: Generated batch with 10 Available cards; Draft Allocate-type allocation
- **When**: `fill_cards(alloc.name, quantity=0)`
- **Then**: `filled_count == 10`; allocation has 10 child rows; each child's `voucher_card` exists in batch

### TC-02: Fill Return type gets Allocated cards for library
- **FR**: FR-002
- **Given**: Generated batch → 5 cards allocated to Library A; Draft Return-type allocation targeting Library A
- **When**: `fill_cards(return_alloc.name, quantity=0)`
- **Then**: `filled_count == 5`; child rows reference the 5 Allocated cards belonging to Library A

### TC-03: Fill respects quantity limit
- **FR**: FR-003
- **Given**: Generated batch with 10 Available cards; Draft Allocate-type allocation
- **When**: `fill_cards(alloc.name, quantity=5)`
- **Then**: `filled_count == 5`; allocation has exactly 5 child rows

### TC-04: Fill rejects non-Draft allocation
- **FR**: FR-004
- **Given**: Allocation in "Completed" status
- **When**: `fill_cards(alloc.name)`
- **Then**: Raises `frappe.ValidationError` with message containing "Draft"

### TC-05: Fill replaces existing cards (idempotent re-fill)
- **FR**: FR-001 (edge case)
- **Given**: Draft allocation already filled with 10 cards
- **When**: `fill_cards(alloc.name, quantity=5)` (re-fill with different quantity)
- **Then**: `filled_count == 5`; allocation now has 5 child rows (not 15)

---

## Class 2: TestSubmitAndApproval (US2 — 7 tests)

### TC-06: Submit auto-completes for no-approval library
- **FR**: FR-005
- **Given**: Filled allocation for library with `voucher_requires_approval=0`
- **When**: `submit_allocation(alloc.name)`
- **Then**: Returns `{"status": "Completed"}`; `alloc.status == "Completed"`

### TC-07: Submit routes to Pending Approval for approval library
- **FR**: FR-006
- **Given**: Filled allocation for library with `voucher_requires_approval=1`
- **When**: `submit_allocation(alloc.name)`
- **Then**: Returns `{"status": "Pending Approval"}`; `alloc.status == "Pending Approval"`

### TC-08: Submit rejects empty allocation
- **FR**: FR-007
- **Given**: Draft allocation with no cards (not filled)
- **When**: `submit_allocation(alloc.name)`
- **Then**: Raises `frappe.ValidationError` with message containing "No cards"

### TC-09: Submit rejects mismatched batch cards
- **FR**: FR-008
- **Given**: Allocation with cards manually added from a different batch
- **When**: `submit_allocation(alloc.name)`
- **Then**: Raises `frappe.ValidationError` with message containing "do not belong to batch"

### TC-10: Approve completes Pending Approval allocation
- **FR**: FR-009
- **Given**: Filled allocation in Pending Approval status
- **When**: `approve_allocation(alloc.name)`
- **Then**: Returns `{"status": "Completed"}`; `alloc.status == "Completed"`

### TC-11: Reject sets Rejected status with reason
- **FR**: FR-010
- **Given**: Filled allocation in Pending Approval status
- **When**: `reject_allocation(alloc.name, reject_reason="Quality issue")`
- **Then**: Returns `{"status": "Rejected"}`; `alloc.status == "Rejected"`; `alloc.notes == "Quality issue"`

### TC-12: Approve rejects non-Pending Approval allocation
- **FR**: FR-011
- **Given**: Allocation in Draft status
- **When**: `approve_allocation(alloc.name)`
- **Then**: Raises `frappe.ValidationError` with message containing "Pending Approval"

---

## Class 3: TestCardStateOnAllocate (US3 — 2 tests)

### TC-13: Allocate sets card fields correctly
- **FR**: FR-012
- **Given**: Completed Allocate-type allocation of 5 cards to Library A with sale_model=Prepaid
- **When**: Query each card in the allocation
- **Then**: Each card has `status="Allocated"`, `library=Library A`, `allocation=alloc.name`, `sale_model="Prepaid"`

### TC-14: Remaining cards stay Available
- **FR**: FR-012 (boundary)
- **Given**: Batch with 10 cards, allocation of 5
- **When**: Query non-allocated cards
- **Then**: Remaining 5 cards have `status="Available"`, `library=None`, `allocation=None`

---

## Class 4: TestCardStateOnReturn (US3 — 2 tests)

### TC-15: Return clears card fields and sets return_allocation
- **FR**: FR-013, FR-020
- **Given**: 5 cards allocated to Library A → Return-type allocation completed
- **When**: Query each returned card
- **Then**: Each card has `status="Available"`, `library=None`, `allocation=None`, `sale_model=None`, `return_allocation=return_alloc.name`

### TC-16: Return with zero eligible cards fills nothing
- **FR**: FR-002 (edge case)
- **Given**: Library B with no Allocated cards in the batch; Draft Return-type allocation for Library B
- **When**: `fill_cards(return_alloc.name)`
- **Then**: `filled_count == 0`; allocation has 0 child rows

---

## Class 5: TestBatchCountersAndStatus (US4 — 2 tests)

### TC-17: Allocated count updated after allocation
- **FR**: FR-014
- **Given**: Batch with 10 Generated cards → 5 cards allocated
- **When**: Check batch counters
- **Then**: `allocated_count == 5`; `generated_count == 10`

### TC-18: Batch transitions Generated→Active on first allocation
- **FR**: FR-015
- **Given**: Batch in Generated status
- **When**: First allocation completes
- **Then**: `batch.status == "Active"`

---

## Class 6: TestPrepaidInvoiceOnAllocation (US5 — 3 tests)

### TC-19: Prepaid allocation creates linked Sales Invoice
- **FR**: FR-016
- **Given**: Completed Prepaid allocation of 5 cards (face_value=10)
- **When**: Check allocation's sales_invoice field
- **Then**: `sales_invoice` is not None; linked Sales Invoice exists with `docstatus=1`

### TC-20: Invoice amount reflects commission
- **FR**: FR-017
- **Given**: Library with 10% commission; Completed Prepaid allocation of 5 cards at face_value=10
- **When**: Load Sales Invoice
- **Then**: `items[0].rate == 9.0` (10 - 10%); `items[0].qty == 5`; total amount = 45.0

### TC-21: Consignment allocation creates no invoice
- **FR**: FR-016 (negative)
- **Given**: Completed Consignment allocation
- **When**: Check allocation's sales_invoice field
- **Then**: `sales_invoice` is None or empty

---

## Class 7: TestStateMachineEnforcement (US6 — 2 tests)

### TC-22: Invalid skip transition rejected
- **FR**: FR-018
- **Given**: Draft allocation
- **When**: Set `alloc.status = "Completed"` and call `alloc.save()`
- **Then**: Raises `frappe.ValidationError` with message containing "Invalid allocation status transition"

### TC-23: Terminal state blocks all transitions
- **FR**: FR-019
- **Given**: Completed allocation
- **When**: Set `alloc.status = "Draft"` and call `alloc.save()`
- **Then**: Raises `frappe.ValidationError` with message containing "terminal state"

---

## Summary

| Class | Tests | User Story | FRs Covered |
|-------|-------|-----------|-------------|
| TestFillCards | 5 | US1 | FR-001, FR-002, FR-003, FR-004 |
| TestSubmitAndApproval | 7 | US2 | FR-005–FR-011 |
| TestCardStateOnAllocate | 2 | US3 | FR-012 |
| TestCardStateOnReturn | 2 | US3 | FR-013, FR-020 |
| TestBatchCountersAndStatus | 2 | US4 | FR-014, FR-015 |
| TestPrepaidInvoiceOnAllocation | 3 | US5 | FR-016, FR-017 |
| TestStateMachineEnforcement | 2 | US6 | FR-018, FR-019 |
| **Total** | **23** | **6** | **FR-001–FR-020** |
