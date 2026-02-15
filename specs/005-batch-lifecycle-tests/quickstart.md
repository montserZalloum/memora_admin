# Quickstart: Batch Lifecycle Integration Tests

**Feature**: 005-batch-lifecycle-tests
**Date**: 2026-02-15

## Prerequisites

1. Test site `x.conanacademy.com` is accessible
2. `voucher_hmac_secret` is set in site config:
   ```bash
   bench --site x.conanacademy.com set-config voucher_hmac_secret "your-secret-here"
   ```
3. `MEMORA-VOUCHER-CARD` Item exists in the database
4. Season `SEAS-00027` exists (standard test season)

## Running the Tests

### Run all batch lifecycle tests
```bash
cd /home/corex/aurevia-bench
bench --site x.conanacademy.com run-tests \
  --app memora_admin \
  --module memora_admin.memora_admin.doctype.memora_voucher_batch.test_memora_voucher_batch
```

### Run a single test
```bash
bench --site x.conanacademy.com run-tests \
  --app memora_admin \
  --module memora_admin.memora_admin.doctype.memora_voucher_batch.test_memora_voucher_batch \
  --test test_generate_creates_cards
```

### Run all voucher tests (includes prior phases)
```bash
bench --site x.conanacademy.com run-tests --app memora_admin
```

## Test File Location

```
memora_admin/memora_admin/doctype/memora_voucher_batch/test_memora_voucher_batch.py
```

This populates the existing empty stub that was created with the DocType.

## Test Organization

The test class `TestMemoraVoucherBatch` extends `VoucherTestCase` and is organized into logical groups:

| Group            | Tests | What They Verify                                  |
|------------------|-------|---------------------------------------------------|
| Happy path       | 6     | Card creation, status, counters, file, serial, HMAC |
| Guard rails      | 5     | Validation errors for invalid inputs               |
| Export & audit   | 2     | CSV decryption, export_log entries                 |
| Atomicity        | 1     | Rollback on failure leaves no partial data         |

## Key Patterns

### Creating a batch and generating cards
```python
grant = make_product_grant(season="SEAS-00027")
batch = make_batch(grants=[grant.name], quantity=10)
generate_batch_sync(batch.name)
batch.reload()
assert batch.status == "Generated"
```

### Testing guard rail validations
```python
batch = make_batch(grants=[grant.name], quantity=0)
with self.assertRaises(frappe.ValidationError):
    generate_batch(batch.name)
```

### Testing export decryption
```python
generate_batch_sync(batch.name)
batch.reload()
# Set System Manager role for export
export_for_print(batch.name)
csv_content = frappe.local.response.filecontent
# Parse and verify CSV matches cards
```

## Dependencies on Prior Phases

| Phase | Dependency | Used For |
|-------|-----------|----------|
| Phase 2 | `voucher_test_base.py` | `VoucherTestCase` base class |
| Phase 2 | `voucher_fixtures.py` | `make_batch()`, `make_product_grant()` |
| Phase 2 | `voucher_helpers.py` | `generate_batch_sync()`, `assert_batch_counters()`, `get_card_statuses()` |
