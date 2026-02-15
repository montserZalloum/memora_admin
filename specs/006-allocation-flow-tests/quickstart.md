# Quickstart: Integration Tests — Allocation Flow

**Feature**: 006-allocation-flow-tests | **Date**: 2026-02-15

## Prerequisites

1. **Test site**: `x.conanacademy.com` with Frappe v15 + ERPNext
2. **HMAC secret**: `voucher_hmac_secret` configured in `site_config.json`
3. **Item**: `MEMORA-VOUCHER-CARD` Item exists in the database
4. **Season**: `SEAS-00027` exists (used to avoid MySQL partitioning constraints)
5. **Previous phases passing**: Phases 2-5 tests green (infrastructure, crypto, commission, batch lifecycle)

## Running Tests

### Run all allocation flow tests

```bash
cd /home/corex/aurevia-bench
bench --site x.conanacademy.com run-tests \
  --app memora_admin \
  --module memora_admin.memora_admin.tests.test_allocation_flow
```

### Run a specific test class

```bash
bench --site x.conanacademy.com run-tests \
  --app memora_admin \
  --module memora_admin.memora_admin.tests.test_allocation_flow \
  --test TestFillCards
```

### Run a single test method

```bash
bench --site x.conanacademy.com run-tests \
  --app memora_admin \
  --module memora_admin.memora_admin.tests.test_allocation_flow \
  --test TestFillCards.test_fill_allocate_gets_all_available_cards
```

## Test File Structure

```python
# memora_admin/memora_admin/tests/test_allocation_flow.py

from memora_admin.memora_admin.tests.voucher_test_base import VoucherTestCase
from memora_admin.memora_admin.tests.voucher_fixtures import (
    make_batch, make_customer, make_product_grant, make_allocation,
)
from memora_admin.memora_admin.tests.voucher_helpers import (
    generate_batch_sync, get_card_statuses, fill_and_complete_allocation,
    assert_batch_counters,
)
from memora_admin.memora_admin.api.allocation import (
    fill_cards, submit_allocation, approve_allocation, reject_allocation,
)

class TestFillCards(VoucherTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.grant = make_product_grant(season="SEAS-00027")
        cls.batch = make_batch(grants=[cls.grant.name])
        generate_batch_sync(cls.batch.name)

    def test_fill_allocate_gets_all_available_cards(self):
        # ...
```

## Adding New Tests

1. Add test method to the appropriate class (group by user story)
2. Use existing fixtures — don't create new factory functions
3. Follow naming: `test_{what_is_tested}` in snake_case
4. Use `self.assertRaises(frappe.ValidationError)` for error paths
5. Reload documents after API calls: `alloc.reload()` / `frappe.get_doc()`
6. Use `assert_batch_counters(self, batch.name, ...)` for counter checks

## Common Patterns

### Creating a generated batch
```python
grant = make_product_grant(season="SEAS-00027")
batch = make_batch(face_value=10, grants=[grant.name])
generate_batch_sync(batch.name)
```

### Creating libraries with different approval settings
```python
no_approval_lib = make_customer(requires_approval=False)
approval_lib = make_customer(requires_approval=True)
commission_lib = make_customer(commission_type="Percentage", commission_value="10")
```

### Testing error paths
```python
with self.assertRaises(frappe.ValidationError) as ctx:
    submit_allocation(alloc.name)
self.assertIn("No cards", str(ctx.exception))
```

### Verifying card fields after allocation
```python
card = frappe.get_doc("Memora Voucher Card", card_name)
self.assertEqual(card.status, "Allocated")
self.assertEqual(card.library, customer.name)
```
