# Implementation Plan: Voucher Crypto & Generator Unit Tests

**Branch**: `003-crypto-generator-tests` | **Date**: 2026-02-15 | **Spec**: [spec.md](spec.md)
**Input**: Feature specification from `/specs/003-crypto-generator-tests/spec.md`

## Summary

Implement ~18 unit tests covering the voucher system's cryptographic and generation layer: PIN generation (`generate_pin`), HMAC computation (`compute_hmac`), serial number reservation (`reserve_serial_block`), CSV export construction (`build_export_csv`), and Fernet encryption/decryption roundtrip (`encrypt_data`/`decrypt_data`). Tests use the existing Phase 2 infrastructure (`VoucherTestCase`, fixtures, helpers) and split into two test files: pure-function tests (no DB) and database-dependent tests (serial reservation).

## Technical Context

**Language/Version**: Python 3.11+ (Frappe v15)
**Primary Dependencies**: Frappe Framework (`frappe.tests.utils.FrappeTestCase`), `cryptography 3.4.8` (Fernet, HKDF)
**Storage**: MariaDB via Frappe ORM (serial reservation only; all other tests are DB-free)
**Testing**: Frappe test runner (`bench run-tests --app memora_admin --module ...`)
**Target Platform**: Linux server (x.conanacademy.com test site)
**Project Type**: Single (Frappe app with service layer)
**Performance Goals**: Full test suite completes in <30 seconds (SC-003)
**Constraints**: Serial reservation tests require `tabSeries` table; all other tests are pure-function with no DB dependency
**Scale/Scope**: ~18 tests across 2 test files, targeting 6 public functions in 2 modules

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

| Principle | Gate | Status |
|-----------|------|--------|
| I. Cryptographic Security | Tests MUST verify `secrets.choice()` usage (not `random`), 30-char alphabet, HMAC-SHA256 format, timing-safe comparison is not bypassed | PASS — FR-003 (alphabet check), FR-008 (HMAC format), FR-005 (determinism) all specified |
| II. Auditable Lifecycle | N/A — Phase 3 tests do not cover state transitions (those belong in later phases) | PASS — out of scope |
| III. Financial Precision | N/A — No monetary calculations in crypto/generator layer | PASS — out of scope |
| IV. Self-Healing Architecture | N/A — Tests target service functions, not Redis cache layer | PASS — out of scope |
| V. Test-First Coverage | Tests MUST cover all public functions in generator and crypto modules (SC-005); each test validates exactly one behavior (SC-004) | PASS — 18 tests mapped to 6 functions with single-assertion focus |

**Pre-design verdict**: All gates PASS. No violations to justify.

## Project Structure

### Documentation (this feature)

```text
specs/003-crypto-generator-tests/
├── plan.md              # This file
├── research.md          # Phase 0 output
├── data-model.md        # Phase 1 output (test data model)
├── quickstart.md        # Phase 1 output (how to run tests)
├── contracts/           # Phase 1 output (test contracts)
│   └── test-matrix.md   # Test-to-requirement traceability matrix
└── tasks.md             # Phase 2 output (/speckit.tasks command)
```

### Source Code (repository root)

```text
memora_admin/memora_admin/
├── services/voucher/
│   ├── generator.py          # SUT: generate_pin, compute_hmac, reserve_serial_block, build_export_csv
│   └── crypto.py             # SUT: encrypt_data, decrypt_data (+ get_fernet_key, get_fernet)
└── tests/
    ├── voucher_test_base.py  # Existing: VoucherTestCase base class
    ├── voucher_fixtures.py   # Existing: Factory functions
    ├── voucher_helpers.py    # Existing: Test helpers
    ├── test_voucher_quickstart.py  # Existing: Example tests
    ├── test_generator.py     # NEW: PIN, HMAC, CSV, encrypted export tests (~12 tests)
    └── test_crypto.py        # NEW: Fernet encrypt/decrypt roundtrip tests (~3 tests)
```

**Structure Decision**: Tests go in `memora_admin/memora_admin/tests/` alongside existing test infrastructure. Two new files: `test_generator.py` for generator module tests (PIN, HMAC, serial, CSV) and `test_crypto.py` for crypto module tests (encrypt/decrypt). Serial reservation tests use `FrappeTestCase` (needs DB); pure-function tests use `unittest.TestCase` for speed.

## Post-Design Constitution Re-Check

| Principle | Gate | Post-Design Status |
|-----------|------|-------------------|
| I. Cryptographic Security | PIN alphabet validated (FR-003), HMAC format validated (FR-008), determinism validated (FR-005) | PASS — `test_pin_contains_only_safe_characters` checks all chars against `PIN_ALPHABET`; `test_hmac_output_format` validates 64-char hex; no `random` module used in tests |
| II. Auditable Lifecycle | N/A for Phase 3 | PASS — no state transition tests in scope |
| III. Financial Precision | N/A for Phase 3 | PASS — no Decimal calculations in scope |
| IV. Self-Healing Architecture | N/A for Phase 3 | PASS — no Redis operations in scope |
| V. Test-First Coverage | All 6 public functions covered by 18 tests; each test has single assertion focus; DB vs pure split respects SC-002 | PASS — traceability matrix confirms 100% FR coverage (FR-001 through FR-018) |

**Post-design verdict**: All gates PASS. Design is constitution-compliant.

## Complexity Tracking

No constitution violations. No complexity justifications needed.
