# Implementation Plan: Voucher System Audit & Comprehensive Tests

**Branch**: `008-voucher-audit-tests` | **Date**: 2026-02-16 | **Spec**: [spec.md](spec.md)
**Input**: Feature specification from `/specs/008-voucher-audit-tests/spec.md`

## Summary

Audit the voucher system for edge cases, abuse scenarios, logical flaws, and missing validations. Write comprehensive tests covering redemption edge cases, voiding/expiry flows, fraud/security gaps, financial accuracy, and counter integrity — targeting **only gaps not already covered** by the 65 existing tests (phases 003-007). Tests document current behavior (including known flaws) with grep-able `# TODO: SECURITY-FIX` / `# TODO: FIX` markers for a future fix branch.

## Technical Context

**Language/Version**: Python 3.11+ (Frappe v15)
**Primary Dependencies**: Frappe Framework (`frappe.tests.utils.FrappeTestCase`), `hmac`, `decimal.Decimal`, `csv`/`io`, ERPNext Sales Invoice
**Storage**: MariaDB via Frappe ORM (card records, batch state, redemption logs, subscription transactions)
**Testing**: `bench --site x.conanacademy.com run-tests --module memora_admin`
**Target Platform**: Linux server (Frappe bench)
**Project Type**: Single (Frappe app — test-only feature, no production code changes)
**Performance Goals**: All new tests execute within 30 seconds total
**Constraints**: No real threading (simulated concurrency); no production code fixes (document-only); no test pollution (each test cleans up)
**Scale/Scope**: ~40-50 new test methods across 4 new test files

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

| Principle | Status | Notes |
|-----------|--------|-------|
| I. Cryptographic Security | PASS | Tests verify `hmac.compare_digest` usage, HMAC secret absence handling, PIN security properties. No production crypto code is modified. |
| II. Auditable Lifecycle | PASS | Tests cover all state machine transitions (valid + invalid), counter integrity, redemption log immutability. Terminal state irreversibility is tested. |
| III. Financial Precision | PASS | Tests use `Decimal` assertions (not float). Commission edge cases (repeating decimals) already covered by phase 004; new tests target only credit note on return and invoice failure gaps. |
| IV. Self-Healing Architecture | N/A | No Redis-cached data is modified. Tests operate entirely within Frappe ORM. |
| V. Test-First Coverage | PASS | This IS the test coverage feature. Every new test follows the constitution's requirements: positive+negative transitions, error code coverage, Decimal precision, minimal fixtures with cleanup. |

**Gate result**: PASS — no violations. Proceeding to Phase 0.

## Project Structure

### Documentation (this feature)

```text
specs/008-voucher-audit-tests/
├── plan.md              # This file
├── research.md          # Phase 0: coverage gap analysis & test approach decisions
├── data-model.md        # Phase 1: test file organization & test method inventory
├── quickstart.md        # Phase 1: how to run and extend the new tests
└── contracts/
    └── test-matrix.md   # Phase 1: requirement-to-test traceability matrix
```

### Source Code (repository root)

```text
memora_admin/memora_admin/
├── api/
│   ├── voucher.py                    # Source under test (redemption, voiding)
│   └── allocation.py                 # Source under test (allocation workflow)
├── services/voucher/
│   ├── batch_utils.py                # Source under test (recount_and_maybe_close)
│   ├── commission.py                 # Source under test (already covered by 004)
│   └── invoice.py                    # Source under test (already covered by 004)
├── doctype/
│   └── memora_voucher_allocation/
│       └── memora_voucher_allocation.py  # Source under test (_apply_allocation, state machine)
└── tests/
    ├── voucher_test_base.py          # Existing: VoucherTestCase base class
    ├── voucher_fixtures.py           # Existing: Factory functions (make_batch, etc.)
    ├── voucher_helpers.py            # Existing: Helper functions (generate_batch_sync, etc.)
    ├── test_redemption_edge.py       # NEW: Redemption edge cases & error codes (US1)
    ├── test_voiding.py               # NEW: Batch/card voiding & expiry flows (US3)
    ├── test_security_audit.py        # NEW: Fraud & security gap documentation (US4)
    └── test_counter_integrity.py     # NEW: Counter accuracy & recount idempotency (US6)
```

**Structure Decision**: All new test files go in the existing `memora_admin/memora_admin/tests/` directory, following the established pattern from phases 003-007. No new directories needed.

### Existing Coverage Summary (DO NOT DUPLICATE)

| File | Tests | Covers |
|------|-------|--------|
| `test_generator.py` | 19 | PIN generation, HMAC, serial reservation, CSV export |
| `test_crypto.py` | 3 | Fernet encrypt/decrypt roundtrip |
| `test_commission.py` | 11 | Commission math (percentage, fixed, zero, Decimal precision) |
| `test_invoice.py` | 8 | Sales Invoice creation, credit note, prepaid flow |
| `test_allocation_flow.py` | 23 | Full allocation lifecycle (fill, submit, approve, reject, return, invoice, state machine) |
| `test_voucher_quickstart.py` | 2 | Basic batch generation + lifecycle |
| `test_voucher_helpers.py` | 5 | Helper function validation |
| **Total** | **71** | |

### New Test Files & Coverage Targets

| File | Target Tests | User Stories | FRs Covered |
|------|-------------|--------------|-------------|
| `test_redemption_edge.py` | 12-15 | US1 | FR-001 to FR-005, FR-017 |
| `test_voiding.py` | 8-10 | US3 | FR-009, FR-010, FR-018, FR-019 |
| `test_security_audit.py` | 6-8 | US4 | FR-016, FR-017 |
| `test_counter_integrity.py` | 4-6 | US6 | FR-012, FR-013 |
| **Total** | **30-39** | | |

## Complexity Tracking

> No constitution violations — this section is intentionally empty.
