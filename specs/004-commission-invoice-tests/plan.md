# Implementation Plan: Commission & Invoice Unit Tests

**Branch**: `004-commission-invoice-tests` | **Date**: 2026-02-15 | **Spec**: [spec.md](spec.md)
**Input**: Feature specification from `/specs/004-commission-invoice-tests/spec.md`

## Summary

Add ~18 unit and integration tests covering commission calculation correctness (percentage, fixed, zero, unknown types), three-tier commission resolution priority chain, Sales Invoice creation for prepaid allocations, Credit Note creation for returns, and end-to-end prepaid flow validation. Commission calculation tests are pure functions tested without DB; resolution and invoice tests use `VoucherTestCase` with existing fixture infrastructure.

## Technical Context

**Language/Version**: Python 3.11+ (Frappe v15)
**Primary Dependencies**: Frappe Framework (`frappe.tests.utils.FrappeTestCase`), `decimal.Decimal`, ERPNext Sales Invoice
**Storage**: MariaDB via Frappe ORM (for resolution and invoice tests); N/A for pure commission math tests
**Testing**: `bench run-tests` (Frappe test runner), `unittest.TestCase` for pure tests, `FrappeTestCase` for DB tests
**Target Platform**: Linux server (x.conanacademy.com)
**Project Type**: Single (Frappe app)
**Performance Goals**: Test suite executes in under 30 seconds (SC-007)
**Constraints**: Must use `Decimal` for all financial assertions (Constitution Principle III); must use existing season `SEAS-00027` for fixture chain
**Scale/Scope**: 2 new test files (~18 tests total), no new production code

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

| Principle | Status | Notes |
|-----------|--------|-------|
| I. Cryptographic Security | N/A | No PIN generation/verification in scope |
| II. Auditable Lifecycle | PASS | Tests verify invoice docstatus and allocation linkage |
| III. Financial Precision | PASS | All commission assertions use `Decimal` with exact comparison; no float assertions |
| IV. Self-Healing Architecture | N/A | No Redis cache operations in scope |
| V. Test-First Coverage | PASS | This IS the test implementation; covers all commission types, resolution tiers, invoice/credit note fields |

**Gate Result**: PASS — no violations.

**Post-Phase 1 Re-check**: PASS — design artifacts (contracts) specify `Decimal` assertions throughout; test classes correctly split into `unittest.TestCase` (pure) and `VoucherTestCase` (DB-dependent).

## Project Structure

### Documentation (this feature)

```text
specs/004-commission-invoice-tests/
├── plan.md                                    # This file
├── research.md                                # Phase 0: Research findings
├── data-model.md                              # Phase 1: Entities under test
├── quickstart.md                              # Phase 1: How to run the tests
├── contracts/
│   ├── test-commission-contract.md            # Phase 1: Commission test specifications
│   └── test-invoice-contract.md               # Phase 1: Invoice test specifications
└── tasks.md                                   # Phase 2: Task breakdown (via /speckit.tasks)
```

### Source Code (repository root)

```text
memora_admin/memora_admin/
├── services/voucher/
│   ├── commission.py          # EXISTING — functions under test
│   └── invoice.py             # EXISTING — functions under test
└── tests/
    ├── test_commission.py     # NEW — ~10 tests (7 pure + 3 DB)
    ├── test_invoice.py        # NEW — ~8 tests (all DB)
    ├── voucher_test_base.py   # EXISTING — VoucherTestCase base class
    ├── voucher_fixtures.py    # EXISTING — Factory functions (make_customer, make_batch, etc.)
    ├── voucher_helpers.py     # EXISTING — Helper functions (fill_and_complete_allocation, etc.)
    ├── test_generator.py      # EXISTING — 19 tests (reference patterns)
    └── test_crypto.py         # EXISTING — 3 tests (reference patterns)
```

**Structure Decision**: Tests go in the existing `memora_admin/memora_admin/tests/` directory alongside existing test files. Two new files: `test_commission.py` and `test_invoice.py`. No new directories needed.

## Test Architecture

### test_commission.py

| Class | Base | DB? | Tests | Covers |
|-------|------|-----|-------|--------|
| `TestCalculateCommission` | `unittest.TestCase` | No | 8 | US1 (FR-001 through FR-007) |
| `TestResolveCommission` | `VoucherTestCase` | Yes | 3 | US2 (FR-008) |

**TestCalculateCommission tests**:
1. `test_no_commission_none_type` — None inputs → full face value (FR-001)
2. `test_no_commission_empty_string` — Empty string inputs → full face value (FR-001)
3. `test_percentage_commission` — 10% of 5.00 → 0.50 commission (FR-002)
4. `test_fixed_amount_commission` — Fixed 1.00 → exact deduction (FR-003)
5. `test_repeating_decimal_precision` — 33.33% of 10.00 → correct rounding (FR-004)
6. `test_quantity_multiplication` — net_per_card * qty = net_total (FR-005)
7. `test_zero_face_value` — All zeros (FR-006)
8. `test_unknown_commission_type_defaults_to_zero` — Unknown type → zero (FR-007)

**TestResolveCommission tests**:
9. `test_grant_level_takes_precedence` — Grant override wins over customer default (FR-008)
10. `test_customer_default_when_no_grant_override` — Falls back to customer (FR-008)
11. `test_no_commission_returns_none_none` — Neither set → (None, None) (FR-008)

### test_invoice.py

| Class | Base | DB? | Tests | Covers |
|-------|------|-----|-------|--------|
| `TestCreateInvoice` | `VoucherTestCase` | Yes | 4 | US3 (FR-009 through FR-012) |
| `TestCreateCreditNote` | `VoucherTestCase` | Yes | 3 | US4 (FR-013, FR-014) |
| `TestPrepaidInvoiceFlow` | `VoucherTestCase` | Yes | 1 | US5 (FR-015) |

**TestCreateInvoice tests**:
1. `test_invoice_is_submitted` — docstatus == 1 (FR-009)
2. `test_invoice_customer_matches_allocation` — customer field correct (FR-010)
3. `test_invoice_uses_voucher_item_code` — MEMORA-VOUCHER-CARD (FR-011)
4. `test_invoice_rate_and_quantity` — rate = net_per_card, qty = card_count (FR-012)

**TestCreateCreditNote tests**:
5. `test_credit_note_is_return_with_reference` — is_return=1, return_against set (FR-013)
6. `test_credit_note_has_negative_quantity` — qty < 0 (FR-014)
7. `test_credit_note_is_submitted` — docstatus == 1 (FR-013)

**TestPrepaidInvoiceFlow tests**:
8. `test_full_prepaid_flow_creates_linked_invoice` — End-to-end allocation→invoice (FR-015)

## Key Design Decisions

1. **Split pure vs DB tests**: `calculate_commission()` has zero Frappe dependencies — tested with `unittest.TestCase` for speed. `resolve_commission()` and invoice functions require DB — tested with `VoucherTestCase`.

2. **Batch grant commission setup**: The `make_batch()` fixture doesn't pass commission fields to grant rows. Tests will set `commission_type`/`commission_value` on the child row via `frappe.db.set_value()` after batch creation (see research.md R6).

3. **Invoice tests share setup**: `TestCreateInvoice` tests can share a `setUpClass` that creates one allocation+invoice, then individual tests assert different fields. This reduces fixture overhead.

4. **Credit note tests need prior invoice**: `TestCreateCreditNote` creates an allocation, then a return allocation. The helper `fill_and_complete_allocation()` handles the full workflow.

5. **Season `SEAS-00027`**: Reused for all DB tests to avoid MySQL partition constraints.

## Complexity Tracking

> No constitution violations — table not needed.
