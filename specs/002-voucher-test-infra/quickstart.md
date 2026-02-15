# Quickstart: Voucher Test Infrastructure

**Feature**: 002-voucher-test-infra | **Date**: 2026-02-15

## Prerequisites

Before using the test infrastructure, ensure:

1. **HMAC Secret**: `voucher_hmac_secret` is set in site config:
   ```bash
   bench --site x.conanacademy.com set-config voucher_hmac_secret "your-secret-here"
   ```

2. **Voucher Item**: The `MEMORA-VOUCHER-CARD` Item record exists in the database.

The `VoucherTestCase` base class will skip tests with clear messages if either is missing.

## Writing a Voucher Test

### 1. Import the infrastructure

```python
from memora_admin.memora_admin.tests.voucher_test_base import VoucherTestCase
from memora_admin.memora_admin.tests.voucher_fixtures import (
    make_batch,
    make_product_grant,
    make_season,
    make_customer,
    make_player,
    make_allocation,
)
from memora_admin.memora_admin.tests.voucher_helpers import (
    generate_batch_sync,
    get_card_statuses,
    fill_and_complete_allocation,
    redeem_card_by_pin,
    assert_batch_counters,
)
```

### 2. Use `VoucherTestCase` as your base class

```python
class TestMyVoucherFeature(VoucherTestCase):
    def test_something(self):
        pass
```

### 3. Create test data with factories

```python
def test_batch_generation(self):
    # Create a product grant (auto-creates plan, season, grade)
    grant = make_product_grant()

    # Create a batch with that grant
    batch = make_batch(grants=[grant.name])

    # Generate cards synchronously
    generate_batch_sync(batch.name)

    # Verify
    batch.reload()
    self.assertEqual(batch.status, "Generated")
    assert_batch_counters(self, batch.name, generated_count=10)
```

### 4. Test the full lifecycle

```python
def test_full_voucher_lifecycle(self):
    # Setup
    grant = make_product_grant()
    batch = make_batch(grants=[grant.name])
    library = make_customer()
    player = make_player()

    # Generate
    generate_batch_sync(batch.name)

    # Allocate
    alloc = fill_and_complete_allocation(batch.name, library.name, quantity=5)
    self.assertEqual(alloc.status, "Completed")

    # Check statuses
    statuses = get_card_statuses(batch.name)
    self.assertEqual(statuses.get("Allocated", 0), 5)
    self.assertEqual(statuses.get("Available", 0), 5)

    # Redeem (need plaintext PIN from decrypted export)
    # ... see redeem_card_by_pin() helper
```

## Running Tests

```bash
# Run all voucher tests
bench --site x.conanacademy.com run-tests --app memora_admin --module memora_admin.memora_admin.doctype.memora_voucher_batch

# Run a specific test class
bench --site x.conanacademy.com run-tests --app memora_admin --doctype "Memora Voucher Batch"

# Run with verbose output
bench --site x.conanacademy.com run-tests --app memora_admin --doctype "Memora Voucher Batch" -v
```

## Factory Quick Reference

| Factory | Required Args | Auto-creates | Returns |
|---------|---------------|--------------|---------|
| `make_season()` | None | — | Memora Season |
| `make_product_grant()` | None | Plan, Season, Grade | Memora Product Grant |
| `make_customer()` | None | — | Customer |
| `make_batch()` | None | — | Memora Voucher Batch |
| `make_player()` | None | Plan, Season, Grade, Major | Memora Player Profile |
| `make_allocation(batch, customer)` | batch, customer | — | Memora Voucher Allocation |

## Helper Quick Reference

| Helper | Purpose | Key Precondition |
|--------|---------|------------------|
| `generate_batch_sync(batch)` | Generate cards synchronously | Batch in Draft |
| `get_card_statuses(batch)` | Count cards by status | Batch has cards |
| `fill_and_complete_allocation(batch, customer)` | Full allocation workflow | Batch in Generated/Active |
| `redeem_card_by_pin(pin, player, grant)` | Redeem with plaintext PIN | Card is Allocated, Batch is Active |
| `assert_batch_counters(tc, batch, **kw)` | Assert counter values | Batch exists |
