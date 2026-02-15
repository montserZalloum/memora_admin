# Quickstart: Redemption Flow Tests

**Feature**: 007-redemption-flow-tests | **Date**: 2026-02-15

## Prerequisites

1. **HMAC Secret**: `voucher_hmac_secret` must be configured in site config:
   ```bash
   bench --site x.conanacademy.com set-config voucher_hmac_secret "your-secret-here"
   ```

2. **MEMORA-VOUCHER-CARD Item**: Must exist in the database (created by `setup.py`)

3. **Season SEAS-00027**: Must exist and be published with a future end_date

## Running the Tests

### All Redemption Tests
```bash
cd /home/corex/aurevia-bench
bench run-tests --app memora_admin --module memora_admin.memora_admin.doctype.memora_voucher_card.test_memora_voucher_card -v
```

### Single Test
```bash
bench run-tests --app memora_admin --module memora_admin.memora_admin.doctype.memora_voucher_card.test_memora_voucher_card --test test_redeem_success_card_status_and_transaction -v
```

### All Voucher Tests (full suite)
```bash
bench run-tests --app memora_admin --module memora_admin.memora_admin.doctype.memora_voucher_batch.test_memora_voucher_batch -v
bench run-tests --app memora_admin --module memora_admin.memora_admin.doctype.memora_voucher_allocation.test_memora_voucher_allocation -v
bench run-tests --app memora_admin --module memora_admin.memora_admin.doctype.memora_voucher_card.test_memora_voucher_card -v
```

## Expected Output

```
test_redeem_success_card_status_and_transaction ... ok
test_redeem_success_card_fields_populated ... ok
test_redeem_success_batch_counter_incremented ... ok
test_redeem_success_log_entry_created ... ok
test_error_invalid_pin ... ok
test_error_not_allocated ... ok
test_error_already_redeemed ... ok
test_error_expired ... ok
test_error_void ... ok
test_error_batch_inactive ... ok
test_error_season_inactive ... ok
test_error_grant_not_in_batch ... ok
test_error_already_owned ... ok
test_preview_returns_grants_and_face_value ... ok
test_preview_filters_owned_grants ... ok
test_preview_all_grants_owned_error ... ok
test_log_success_entry ... ok
test_log_failure_entries_all_codes ... ok
test_log_pin_masked ... ok
test_log_ip_address_captured ... ok
test_hmac_uses_compare_digest ... ok
test_batch_auto_close_on_last_redemption ... ok

----------------------------------------------------------------------
Ran 22 tests in <60s
```

## Test Data Created

Each test run creates:
- 1 Memora Subject (for grant components)
- 1-2 Product Grants (with grant components)
- 1-2 Voucher Batches (~30 cards total)
- 1 Customer (library)
- 1-2 Player Profiles
- Various Redemption Log entries
- Various Subscription Transaction records

All test data uses unique random identifiers and does not conflict with production data.

## Files Modified

| File | Change Type |
|------|------------|
| `doctype/memora_voucher_card/test_memora_voucher_card.py` | Replaced stub with 22 tests |
| `tests/voucher_fixtures.py` | Added `grant_components` param to `make_product_grant()` |
| `tests/voucher_helpers.py` | Added `get_pins_from_export()`, `preview_card_by_pin()` |
