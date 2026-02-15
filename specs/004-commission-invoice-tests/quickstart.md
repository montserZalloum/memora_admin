# Quickstart: Commission & Invoice Unit Tests

**Feature**: 004-commission-invoice-tests
**Date**: 2026-02-15

## Prerequisites

1. Frappe bench installed with `memora_admin` app
2. `voucher_hmac_secret` configured in `site_config.json`
3. `MEMORA-VOUCHER-CARD` Item exists in database
4. Test site: `x.conanacademy.com`

## Running the Tests

```bash
# Run commission tests only (pure unit — no DB)
bench run-tests --app memora_admin --module memora_admin.memora_admin.tests.test_commission

# Run invoice tests only (requires DB)
bench run-tests --app memora_admin --module memora_admin.memora_admin.tests.test_invoice

# Run all voucher tests
bench run-tests --app memora_admin
```

## Test File Structure

```
memora_admin/memora_admin/tests/
├── test_commission.py    # NEW — ~10 tests (7 pure + 3 DB)
├── test_invoice.py       # NEW — ~8 tests (all DB)
├── test_generator.py     # Existing — 19 tests
├── test_crypto.py        # Existing — 3 tests
├── voucher_test_base.py  # Existing — VoucherTestCase base class
├── voucher_fixtures.py   # Existing — Factory functions
└── voucher_helpers.py    # Existing — Helper functions
```

## Test Categories

### Pure Unit Tests (no DB) — `test_commission.py::TestCalculateCommission`

These test the `calculate_commission()` function with known inputs and verify exact Decimal outputs:

```python
from decimal import Decimal
from memora_admin.memora_admin.services.voucher.commission import calculate_commission

# Example: percentage commission
result = calculate_commission("5.00", 10, "Percentage", "10")
assert result["per_card_commission"] == Decimal("0.50")
assert result["net_per_card"] == Decimal("4.50")
assert result["net_total"] == Decimal("45.00")
```

### DB-Dependent Tests — `test_commission.py::TestResolveCommission`

These test the three-tier priority chain using real Customer and Batch Grant records:

```python
# Uses VoucherTestCase (FrappeTestCase) for DB access
# Fixtures: make_customer(), make_batch(), make_product_grant()
customer = make_customer(commission_type="Percentage", commission_value="10")
# ... set up batch grant with override ...
result = resolve_commission(batch.name, customer.name)
```

### Invoice Integration Tests — `test_invoice.py`

These create real Sales Invoices and Credit Notes:

```python
# Uses fill_and_complete_allocation() helper for full workflow
grant = make_product_grant(season="SEAS-00027")
batch = make_batch(grants=[grant.name])
generate_batch_sync(batch.name)
customer = make_customer(commission_type="Percentage", commission_value="10")
alloc = fill_and_complete_allocation(batch.name, customer.name, quantity=5)
# Verify invoice was created and linked
```

## Key Patterns

1. **Decimal assertions** — always compare with `Decimal("x.xx")`, never `float`
2. **Season reuse** — use `SEAS-00027` for fixtures needing season context
3. **Batch grant commission** — set via `frappe.db.set_value()` on child row after `make_batch()`
4. **Reload before assert** — always `doc.reload()` before checking fields set by background logic
