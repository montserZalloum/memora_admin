# Implementation Plan: Voucher Test Infrastructure

**Branch**: `002-voucher-test-infra` | **Date**: 2026-02-15 | **Spec**: [spec.md](spec.md)
**Input**: Feature specification from `/specs/002-voucher-test-infra/spec.md`

## Summary

Create shared fixture factories and test helpers for the voucher system test suite. This provides 6 factory functions (`make_batch`, `make_product_grant`, `make_season`, `make_customer`, `make_player`, `make_allocation`) and 5 helper functions (`generate_batch_sync`, `get_card_statuses`, `fill_and_complete_allocation`, `redeem_card_by_pin`, `assert_batch_counters`) plus prerequisite validation. All code is pure Python using Frappe's `FrappeTestCase` testing conventions with `frappe.get_doc({...}).insert()` patterns.

## Technical Context

**Language/Version**: Python 3.11+ (Frappe v15)
**Primary Dependencies**: Frappe Framework (ORM, `frappe.tests.utils.FrappeTestCase`, background jobs)
**Storage**: MariaDB via Frappe ORM; `tabSeries` for atomic serial reservation
**Testing**: `bench run-tests` with `FrappeTestCase` (unittest-based); no pytest
**Target Platform**: Linux server (x.conanacademy.com test site)
**Project Type**: Single Frappe app (no separate frontend/backend split for tests)
**Performance Goals**: N/A (test infrastructure, not runtime code)
**Constraints**: Factories must produce valid, saved documents. Each call must produce unique names. Helpers must work synchronously (no background queue).
**Scale/Scope**: 6 factory functions, 5 helper functions, 1 prerequisite base class. Supports ~145 tests in subsequent phases (P3-P10).

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

| Principle | Relevance | Status |
|-----------|-----------|--------|
| **I. Cryptographic Security** | Helpers compute HMAC for `redeem_card_by_pin`. Must use `compute_hmac()` from generator.py with `voucher_hmac_secret` from site config. Never store plaintext PINs in test assertions beyond the immediate test scope. | PASS — uses existing `compute_hmac()` |
| **II. Auditable Lifecycle** | Factories must create documents in valid states per state machines. `make_batch()` defaults to Draft. Helpers like `generate_batch_sync()` must follow Draft→Generated transition. `fill_and_complete_allocation()` must follow full allocation workflow. | PASS — follows documented state machines |
| **III. Financial Precision** | Not directly involved. Factories set `face_value` as Currency field (Frappe handles storage). No commission calculations in test infra. | PASS — N/A |
| **IV. Self-Healing Architecture** | Not directly involved. Test infrastructure is Frappe ORM only, no Redis operations. | PASS — N/A |
| **V. Test-First Coverage** | This IS the test infrastructure. Must enable isolated, minimal fixtures. Each test must clean up after itself (Frappe's test runner handles rollback). | PASS — core deliverable |

**Gate result**: PASS — No violations. Proceed to Phase 0.

## Project Structure

### Documentation (this feature)

```text
specs/002-voucher-test-infra/
├── plan.md              # This file
├── research.md          # Phase 0 output
├── data-model.md        # Phase 1 output
├── quickstart.md        # Phase 1 output
├── contracts/           # Phase 1 output
│   └── factory-api.md   # Factory & helper function signatures
└── tasks.md             # Phase 2 output (/speckit.tasks)
```

### Source Code (repository root)

```text
memora_admin/memora_admin/
├── tests/                              # NEW: shared test infrastructure
│   ├── __init__.py
│   ├── voucher_fixtures.py             # 6 factory functions
│   ├── voucher_helpers.py              # 5 helper functions
│   └── voucher_test_base.py            # Prerequisite-checking base class
├── doctype/
│   ├── memora_voucher_batch/
│   │   └── test_memora_voucher_batch.py    # Existing stub (consumers of infra)
│   ├── memora_voucher_card/
│   │   └── test_memora_voucher_card.py
│   ├── memora_voucher_allocation/
│   │   └── test_memora_voucher_allocation.py
│   └── ...
├── services/voucher/
│   ├── generator.py                    # Existing: generate_pin, compute_hmac, reserve_serial_block
│   └── batch_utils.py                  # Existing: recount_and_maybe_close
└── api/
    ├── voucher.py                      # Existing: generate_cards_job (called directly by helper)
    └── allocation.py                   # Existing: fill_cards, submit_allocation
```

**Structure Decision**: Test infrastructure lives in `memora_admin/memora_admin/tests/` as a shared module importable by all DocType test files. This follows Frappe's convention of placing shared test utilities in a `tests/` directory within the app module. Individual DocType test files remain in their DocType directories and import from `memora_admin.memora_admin.tests.*`.

## Complexity Tracking

> No constitution violations to justify.

N/A
