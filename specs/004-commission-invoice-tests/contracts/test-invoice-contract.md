# Test Contract: test_invoice.py

**Feature**: 004-commission-invoice-tests
**Date**: 2026-02-15

All tests in this file use `VoucherTestCase` (extends `FrappeTestCase`).

---

## Module: TestCreateInvoice (VoucherTestCase)

Tests for `create_voucher_invoice()` and invoice field correctness.

### Test 1: test_invoice_is_submitted

**Requirement**: FR-009
**Setup**:
1. `make_product_grant(season="SEAS-00027")` → grant
2. `make_batch(grants=[grant.name])` → batch
3. `generate_batch_sync(batch.name)`
4. `make_customer()` → customer
5. `fill_and_complete_allocation(batch.name, customer.name, quantity=5)` → alloc

**Assert**:
- `alloc.sales_invoice` is not None
- Load Sales Invoice: `frappe.get_doc("Sales Invoice", alloc.sales_invoice)`
- `si.docstatus == 1`

### Test 2: test_invoice_customer_matches_allocation

**Requirement**: FR-010
**Setup**: Same as Test 1
**Assert**: `si.customer == customer.name`

### Test 3: test_invoice_uses_voucher_item_code

**Requirement**: FR-011
**Setup**: Same as Test 1
**Assert**: `si.items[0].item_code == "MEMORA-VOUCHER-CARD"`

### Test 4: test_invoice_rate_and_quantity

**Requirement**: FR-012
**Setup**:
1. Create batch with `face_value=10`
2. Create customer with `commission_type="Percentage"`, `commission_value="10"`
3. Generate + allocate 5 cards

**Assert**:
- `si.items[0].qty == 5` (card count)
- `si.items[0].rate == 9.0` (net_per_card = 10 - 10% = 9.00, stored as float)

---

## Module: TestCreateCreditNote (VoucherTestCase)

Tests for `create_credit_note()` and `create_prepaid_return_credit_note()`.

### Test 5: test_credit_note_is_return_with_reference

**Requirement**: FR-013
**Setup**:
1. Create batch, generate, allocate (Prepaid) → original invoice
2. Create return allocation, fill with same cards, complete

**Assert**:
- Load Credit Note from `return_alloc.sales_invoice`
- `cn.is_return == 1`
- `cn.return_against == original_si_name`

### Test 6: test_credit_note_has_negative_quantity

**Requirement**: FR-014
**Setup**: Same as Test 5
**Assert**: `cn.items[0].qty < 0`

### Test 7: test_credit_note_is_submitted

**Requirement**: FR-013
**Setup**: Same as Test 5
**Assert**: `cn.docstatus == 1`

---

## Module: TestPrepaidInvoiceFlow (VoucherTestCase)

End-to-end flow test.

### Test 8: test_full_prepaid_flow_creates_linked_invoice

**Requirement**: FR-015, SC-006
**Setup**:
1. `make_product_grant(season="SEAS-00027")` → grant
2. `make_batch(face_value=10, grants=[grant.name])` → batch
3. `generate_batch_sync(batch.name)`
4. `make_customer(commission_type="Percentage", commission_value="20")` → customer
5. `fill_and_complete_allocation(batch.name, customer.name, quantity=5)` → alloc

**Assert**:
- `alloc.sales_invoice` is not None (FR-015: invoice linked to allocation)
- Load SI: `si.docstatus == 1` (submitted)
- `si.customer == customer.name`
- `si.items[0].item_code == "MEMORA-VOUCHER-CARD"`
- `si.items[0].qty == 5`
- `si.items[0].rate == 8.0` (10.00 - 20% = 8.00)
- `si.items[0].amount == 40.0` (8.00 * 5)
