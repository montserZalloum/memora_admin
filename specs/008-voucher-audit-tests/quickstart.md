# Quickstart: Voucher System Audit Tests

**Phase 1 output** | **Date**: 2026-02-16

## Prerequisites

1. Frappe bench running with site `x.conanacademy.com`
2. `voucher_hmac_secret` configured in `site_config.json`
3. `MEMORA-VOUCHER-CARD` Item exists in the database
4. Season `SEAS-00027` exists (pre-configured in test environment)

## Running the Tests

### Run all audit tests

```bash
cd /home/corex/aurevia-bench
bench --site x.conanacademy.com run-tests \
  --app memora_admin \
  --module memora_admin.memora_admin.tests.test_redemption_edge \
  --module memora_admin.memora_admin.tests.test_voiding \
  --module memora_admin.memora_admin.tests.test_security_audit \
  --module memora_admin.memora_admin.tests.test_counter_integrity
```

### Run a single test file

```bash
bench --site x.conanacademy.com run-tests \
  --app memora_admin \
  --module memora_admin.memora_admin.tests.test_redemption_edge
```

### Run a single test class

```bash
bench --site x.conanacademy.com run-tests \
  --app memora_admin \
  --module memora_admin.memora_admin.tests.test_redemption_edge \
  -k TestRedemptionErrorCodes
```

### Run a single test method

```bash
bench --site x.conanacademy.com run-tests \
  --app memora_admin \
  --module memora_admin.memora_admin.tests.test_redemption_edge \
  -k test_already_redeemed_returns_error
```

## Finding Security Gap Markers

```bash
# All security-fix TODOs
grep -rn "TODO: SECURITY-FIX" memora_admin/memora_admin/tests/

# All fix TODOs (includes security + logic bugs)
grep -rn "TODO: FIX\|TODO: SECURITY-FIX" memora_admin/memora_admin/tests/
```

## Adding New Tests

### Pattern: Error code test

```python
def test_some_error_returns_correct_code(self):
    """Scenario: [description of what triggers the error]."""
    # Setup: create conditions for the error
    result = redeem_card_by_pin(
        pin=self.pins[0],
        player_id=self.player.name,
        grant_id=self.grant.name,
    )
    self.assertEqual(result.get("error"), "EXPECTED_ERROR_CODE")

    # Verify redemption log entry
    log = frappe.get_last_doc("Memora Voucher Redemption Log",
        filters={"player": self.player.name})
    self.assertEqual(log.status, "Expected Status")
```

### Pattern: Security gap documentation

```python
def test_gap_description(self):
    """Document: [what the gap is].
    # TODO: SECURITY-FIX - [what correct behavior should be]
    """
    # This test PASSES asserting current (insecure) behavior
    result = some_operation()
    self.assertEqual(result, current_insecure_value)
```

### Pattern: Counter integrity

```python
def test_counters_after_operation(self):
    """Verify counters are accurate after [operation]."""
    perform_operation()
    assert_batch_counters(
        self, self.batch.name,
        generated_count=10,
        allocated_count=5,
        redeemed_count=3,
        voided_count=2,
        expired_count=0,
    )
```

## Test Infrastructure Files

| File | Purpose |
|------|---------|
| `voucher_test_base.py` | `VoucherTestCase` base class — validates prerequisites in `setUpClass()` |
| `voucher_fixtures.py` | Factory functions: `make_product_grant()`, `make_batch()`, `make_customer()`, `make_player()`, `make_allocation()` |
| `voucher_helpers.py` | Test helpers: `generate_batch_sync()`, `fill_and_complete_allocation()`, `get_pins_from_export()`, `redeem_card_by_pin()`, `assert_batch_counters()` |
