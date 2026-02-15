# Implementation Plan: Batch Lifecycle Integration Tests

**Branch**: `005-batch-lifecycle-tests` | **Date**: 2026-02-15 | **Spec**: [spec.md](spec.md)
**Input**: Feature specification from `/specs/005-batch-lifecycle-tests/spec.md`

## Summary

Populate the existing empty test stub `test_memora_voucher_batch.py` with 14 integration tests covering the full batch generation lifecycle: happy path card creation (6 tests), validation guard rails (5 tests), export/audit trail (2 tests), and failure rollback (1 test). Tests use existing Phase 2 infrastructure (fixtures, helpers, base class) and target two API layers — `generate_batch()` for validation tests and `generate_cards_job()` for generation tests.

## Technical Context

**Language/Version**: Python 3.11+ (Frappe v15)
**Primary Dependencies**: Frappe Framework (`frappe.tests.utils.FrappeTestCase`), `cryptography 3.4.8` (Fernet/HKDF for export verification)
**Storage**: MariaDB via Frappe ORM (card records, batch state, export audit log)
**Testing**: `FrappeTestCase` with `bench run-tests` runner
**Target Platform**: Linux server (x.conanacademy.com test environment)
**Project Type**: Single (Frappe app — test file within DocType directory)
**Performance Goals**: Full test suite completes in under 30 seconds
**Constraints**: Must use existing season `SEAS-00027`; max batch quantity 1000
**Scale/Scope**: 14 tests in 1 file (~300 lines)

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

| Principle | Status | Notes |
|-----------|--------|-------|
| I. Cryptographic Security | PASS | Tests verify HMAC storage (FR-006), no plaintext persistence, HMAC secret requirement (FR-010), and encrypted export (FR-004). No new crypto code introduced. |
| II. Auditable Lifecycle | PASS | Tests verify Draft→Generated state transition (FR-002), guard rails prevent invalid transitions (FR-007, FR-013), export audit log (FR-012). |
| III. Financial Precision | N/A | No financial calculations in batch generation. Commission/invoice tested in Phase 4/6. |
| IV. Self-Healing Architecture | N/A | No Redis interaction in batch generation tests. |
| V. Test-First Coverage | PASS | This phase IS the test coverage. 14 tests covering all generation paths (positive + negative). |

**Post-design re-check**: All gates still pass. No violations introduced.

## Project Structure

### Documentation (this feature)

```text
specs/005-batch-lifecycle-tests/
├── plan.md              # This file
├── spec.md              # Feature specification
├── research.md          # Research findings
├── data-model.md        # Entities under test
├── quickstart.md        # How to run the tests
├── contracts/
│   └── test-matrix.md   # Test-to-requirement traceability
└── checklists/
    └── requirements.md  # Spec quality checklist
```

### Source Code (repository root)

```text
memora_admin/memora_admin/
├── doctype/memora_voucher_batch/
│   └── test_memora_voucher_batch.py   # ← ONLY file modified (populate existing stub)
├── api/
│   └── voucher.py                     # Functions under test (NOT modified)
├── services/voucher/
│   ├── generator.py                   # Functions under test (NOT modified)
│   └── crypto.py                      # Functions under test (NOT modified)
└── tests/
    ├── voucher_test_base.py           # Base class (from Phase 2, NOT modified)
    ├── voucher_fixtures.py            # Fixtures (from Phase 2, NOT modified)
    └── voucher_helpers.py             # Helpers (from Phase 2, NOT modified)
```

**Structure Decision**: Single file modification — populate the existing `test_memora_voucher_batch.py` stub. No new files or directories needed. All test infrastructure already exists from Phase 2.

## Implementation Details

### Test Class Design

Single test class `TestMemoraVoucherBatch(VoucherTestCase)` with a shared `setUp()` method that creates the common fixtures (grant + batch) needed by most tests.

### Test Method Mapping

#### Group 1: Happy Path (6 tests)

| # | Method | What It Tests | Key Assertions |
|---|--------|---------------|----------------|
| 1 | `test_generate_creates_cards` | Card creation count + status | `get_card_statuses()` returns `{"Available": N}` |
| 2 | `test_generate_status_transition` | Batch Draft→Generated | `batch.status == "Generated"` |
| 3 | `test_generate_counters` | Counter fields | `assert_batch_counters(generated=N, allocated=0, redeemed=0, voided=0, expired=0)` |
| 4 | `test_generate_encrypted_file` | Encrypted export exists | `batch.encrypted_file_url` is truthy + `os.path.exists()` |
| 5 | `test_generate_serial_format` | VCH-NNNNNN format | Regex `r'^VCH-\d{6}$'` on all card serial_no values |
| 6 | `test_generate_hmac_stored` | HMAC presence, no plaintext | `card.pin_hmac` is non-empty; no `pin` column in schema |

#### Group 2: Guard Rails (5 tests)

| # | Method | What It Tests | Key Assertions |
|---|--------|---------------|----------------|
| 7 | `test_generate_non_draft_fails` | Non-Draft rejection | `assertRaises(ValidationError)` on Generated batch |
| 8 | `test_generate_zero_quantity_fails` | Zero quantity rejection | `assertRaises(ValidationError)` |
| 9 | `test_generate_exceeds_max_fails` | Over-limit rejection | `assertRaises(ValidationError)` on qty=1001 |
| 10 | `test_generate_no_hmac_secret_fails` | Missing config rejection | Temp remove secret, `assertRaises(ValidationError)` |
| 11 | `test_generate_already_generated_fails` | Re-generation rejection | Generate once, then `assertRaises(ValidationError)` on second call |

#### Group 3: Export & Audit (2 tests)

| # | Method | What It Tests | Key Assertions |
|---|--------|---------------|----------------|
| 12 | `test_export_decrypts_correctly` | CSV content integrity | Parse CSV from `frappe.local.response.filecontent`, verify serial_no and pin columns match generated cards |
| 13 | `test_export_audit_logged` | Audit trail entry | `len(batch.export_log)` increases by 1 after export call |

#### Group 4: Atomicity (1 test)

| # | Method | What It Tests | Key Assertions |
|---|--------|---------------|----------------|
| 14 | `test_generate_rollback_on_failure` | No partial cards on failure | Monkeypatch `bulk_insert` to raise, verify 0 cards + Draft status |

### Technical Approaches for Complex Tests

**test_generate_no_hmac_secret_fails**: Store `frappe.conf.voucher_hmac_secret` value, set to `""`, call `generate_batch()`, restore original in `finally` block.

**test_export_decrypts_correctly**: Generate batch, then call `export_for_print()` with System Manager role. Read CSV from `frappe.local.response.filecontent`. Parse and verify serial numbers exist in the DB and PINs decrypt to valid HMAC matches.

**test_generate_rollback_on_failure**: Use `unittest.mock.patch` on `frappe.db.bulk_insert` to raise `Exception`. Call `generate_cards_job()` in a try/except (it re-raises). Verify zero cards for the batch and batch status is still "Draft".

## Complexity Tracking

No constitution violations. No complexity justifications needed.
