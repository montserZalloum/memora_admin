# Quickstart: Voucher Crypto & Generator Unit Tests

**Phase 1 Output** | **Date**: 2026-02-15

## Prerequisites

1. **Frappe bench** installed and configured at `/home/corex/aurevia-bench`
2. **Test site**: `x.conanacademy.com` with `allow_tests: true` in site config
3. **HMAC secret**: `voucher_hmac_secret` configured in site config
4. **Cryptography package**: `cryptography >= 3.4` installed (`pip show cryptography`)
5. **Phase 2 test infrastructure**: `voucher_test_base.py`, `voucher_fixtures.py`, `voucher_helpers.py` present in `memora_admin/memora_admin/tests/`

## Running the Tests

### Run all Phase 3 tests (generator + crypto)

```bash
cd /home/corex/aurevia-bench

# Generator tests (PIN, HMAC, Serial, CSV)
bench --site x.conanacademy.com run-tests \
  --app memora_admin \
  --module memora_admin.memora_admin.tests.test_generator

# Crypto tests (encrypt/decrypt)
bench --site x.conanacademy.com run-tests \
  --app memora_admin \
  --module memora_admin.memora_admin.tests.test_crypto
```

### Run a specific test class

```bash
# PIN generation tests only
bench --site x.conanacademy.com run-tests \
  --app memora_admin \
  --module memora_admin.memora_admin.tests.test_generator \
  --case TestGeneratePin

# Serial reservation tests only (requires DB)
bench --site x.conanacademy.com run-tests \
  --app memora_admin \
  --module memora_admin.memora_admin.tests.test_generator \
  --case TestReserveSerialBlock
```

### Run a single test

```bash
bench --site x.conanacademy.com run-tests \
  --app memora_admin \
  --module memora_admin.memora_admin.tests.test_generator \
  --case TestGeneratePin \
  --test test_default_pin_length_is_12
```

## Test Files

| File | Tests | DB? | Description |
|------|-------|-----|-------------|
| `test_generator.py` | ~15 | Partial | PIN, HMAC (no DB); Serial reservation (DB); CSV export (no DB) |
| `test_crypto.py` | ~3 | No | Fernet encrypt/decrypt roundtrip |

## Expected Output

```
----------------------------------------------------------------------
Ran 18 tests in X.XXXs

OK
```

All 18 tests should pass in under 30 seconds (SC-003).

## Troubleshooting

| Problem | Solution |
|---------|----------|
| `SkipTest: voucher_hmac_secret not configured` | Run: `bench --site x.conanacademy.com set-config voucher_hmac_secret <secret>` |
| `ImportError: cryptography` | Run: `pip install cryptography` |
| `ModuleNotFoundError: memora_admin` | Ensure app is installed: `bench --site x.conanacademy.com install-app memora_admin` |
| Serial tests fail with lock timeout | Another process holds `tabSeries` lock; retry or check for hanging transactions |
