# Research: Commission & Invoice Unit Tests

**Feature**: 004-commission-invoice-tests
**Date**: 2026-02-15

## R1: Commission Calculation Function Signature & Behavior

**Decision**: Test `calculate_commission()` as pure unit tests using `unittest.TestCase` (no DB needed).

**Rationale**: The function accepts string/int/None inputs and returns a dict of Decimal values. It has no Frappe/DB dependencies — only `decimal.Decimal` and `ROUND_HALF_UP`.

**Alternatives considered**:
- Using `FrappeTestCase` for all commission tests — rejected because calculate_commission is a pure function with no DB interaction, and pure unittest.TestCase tests run faster.

**Key findings**:
- Function signature: `calculate_commission(face_value: str, quantity: int, commission_type: str | None, commission_value: str | None) -> dict`
- Returns: `per_card_commission`, `total_commission`, `net_per_card`, `net_total` — all `Decimal` objects
- Three branches: `"Percentage"`, `"Fixed Amount"`, else (zero commission)
- No commission when `commission_type` or `commission_value` is falsy (None, empty string)
- Unknown commission_type falls through to `else` → zero commission
- All values quantized to `TWO_PLACES = Decimal("0.01")` with `ROUND_HALF_UP`

**Source**: `memora_admin/memora_admin/services/voucher/commission.py:18-67`

---

## R2: Commission Resolution Priority Chain

**Decision**: Test `resolve_commission()` using `FrappeTestCase` (requires DB for Frappe queries).

**Rationale**: The function queries `Memora Voucher Batch Grant` child table and `Customer` custom fields via `frappe.get_all()` and `frappe.db.get_value()`.

**Key findings**:
- Priority: (1) Batch Grant override → (2) Customer default → (3) (None, None)
- Batch Grant query: `filters={"parent": batch_name, "commission_type": ["is", "set"]}`, `limit=1`
- Customer query: reads `voucher_commission_type` and `voucher_commission_value` custom fields
- Returns `tuple[str | None, str | None]`

**Gap identified**: `make_batch()` fixture does NOT support `commission_type`/`commission_value` on batch grant child rows — it only passes `product_grant`. Tests will need to set these fields manually via `frappe.db.set_value()` on the child row after batch creation.

**Source**: `memora_admin/memora_admin/services/voucher/commission.py:70-108`

---

## R3: Invoice Creation Patterns

**Decision**: Test `create_voucher_invoice()` and `create_credit_note()` using `VoucherTestCase` (requires DB + MEMORA-VOUCHER-CARD item).

**Rationale**: These functions create Sales Invoice documents via Frappe ORM (`frappe.new_doc`, `insert`, `submit`). They need a valid Customer and Item in the database.

**Key findings**:
- `create_voucher_invoice()`: Creates submitted Sales Invoice with `MEMORA-VOUCHER-CARD` item, converts `rate` to `float()` at ORM boundary
- `create_credit_note()`: Creates submitted Sales Invoice with `is_return=1`, `return_against`, negated qty (`-abs(qty)`)
- `create_credit_note()` throws `frappe.ValidationError` if `return_against` is falsy
- Both use `ignore_permissions=True` for insert

**Source**: `memora_admin/memora_admin/services/voucher/invoice.py:23-117`

---

## R4: Prepaid Allocation Invoice Orchestration

**Decision**: Test `create_prepaid_allocation_invoice()` as integration test using `VoucherTestCase` + `fill_and_complete_allocation()` helper.

**Rationale**: This function orchestrates loading allocation/batch docs, resolving commission, calculating amounts, creating invoice, and linking back. It exercises the full commission→invoice pipeline.

**Key findings**:
- Loads allocation + batch documents
- Gets `card_count` from `len(allocation.allocation_cards)`
- Calls `resolve_commission()` + `calculate_commission()`
- Creates invoice with `rate=result["net_per_card"]` and `qty=card_count`
- Links invoice to allocation via `frappe.db.set_value()`
- Links invoice to cards via bulk SQL UPDATE

**Source**: `memora_admin/memora_admin/services/voucher/invoice.py:120-178`

---

## R5: Credit Note for Returns

**Decision**: Test `create_prepaid_return_credit_note()` as integration test.

**Rationale**: Groups returned cards by original `sales_invoice`, creates one Credit Note per group. Needs a prior allocation+invoice to exist.

**Key findings**:
- Queries cards' `sales_invoice` field to find original invoices
- Groups by original invoice → one Credit Note per group
- Returns single name, comma-separated names, or None
- Links to allocation via `frappe.db.set_value()`

**Source**: `memora_admin/memora_admin/services/voucher/invoice.py:181-267`

---

## R6: Existing Fixture Support for Commission Tests

**Decision**: Enhance `make_batch()` by adding commission fields to batch grant rows post-creation, rather than modifying the fixture.

**Rationale**: The `make_batch()` fixture builds minimal batch grant rows (only `product_grant`). For commission resolution tests, we need `commission_type` and `commission_value` on grant child rows. Setting these via `frappe.db.set_value()` after creation is simpler and avoids modifying shared fixture code.

**Pattern**:
```python
# After make_batch():
grant_row = frappe.get_all(
    "Memora Voucher Batch Grant",
    filters={"parent": batch.name},
    fields=["name"],
    limit=1,
)
frappe.db.set_value(
    "Memora Voucher Batch Grant",
    grant_row[0].name,
    {"commission_type": "Percentage", "commission_value": "10"},
)
```

**Source**: `memora_admin/memora_admin/tests/voucher_fixtures.py:245-289`

---

## R7: Test Class Hierarchy

**Decision**: Use two base classes based on DB dependency:
- `unittest.TestCase` for `TestCalculateCommission` (pure math, 7 tests)
- `VoucherTestCase` (extends `FrappeTestCase`) for `TestResolveCommission`, `TestCreateInvoice`, `TestCreateCreditNote`, `TestPrepaidInvoiceFlow` (DB-dependent, ~11 tests)

**Rationale**: Follows existing pattern from `test_generator.py` where `TestGeneratePin` uses `unittest.TestCase` and `TestReserveSerialBlock` uses `FrappeTestCase`.

**Source**: `memora_admin/memora_admin/tests/test_generator.py:24` (unittest.TestCase pattern)

---

## R8: Assertion Strategy for Decimal Values

**Decision**: Use `assertEqual` with explicit `Decimal` expected values (not float comparisons).

**Rationale**: Constitution Principle III (Financial Precision) mandates Decimal arithmetic. Float comparison would mask precision errors.

**Pattern**:
```python
from decimal import Decimal
result = calculate_commission("5.00", 10, "Percentage", "10")
self.assertEqual(result["per_card_commission"], Decimal("0.50"))
self.assertEqual(result["net_per_card"], Decimal("4.50"))
```

**Source**: Constitution Principle III (Financial Precision)
