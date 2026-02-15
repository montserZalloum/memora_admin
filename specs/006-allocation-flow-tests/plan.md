# Implementation Plan: Integration Tests — Allocation Flow

**Branch**: `006-allocation-flow-tests` | **Date**: 2026-02-15 | **Spec**: [spec.md](spec.md)
**Input**: Feature specification from `/specs/006-allocation-flow-tests/spec.md`

## Summary

Implement ~22 integration tests covering the full voucher allocation workflow: card filling (Allocate/Return types), approval/auto-approve routing, card state mutations on completion, batch counter updates, prepaid invoice creation with commission, and state machine enforcement. Tests use the existing shared fixture factories (`voucher_fixtures.py`) and helpers (`voucher_helpers.py`) from Phase 2 infrastructure, running against the live test site via `FrappeTestCase`.

## Technical Context

**Language/Version**: Python 3.11+ (Frappe v15)
**Primary Dependencies**: Frappe Framework (`frappe.tests.utils.FrappeTestCase`), ERPNext Sales Invoice, `decimal.Decimal`
**Storage**: MariaDB via Frappe ORM (card records, batch state, allocation state, Sales Invoice)
**Testing**: `FrappeTestCase` with `bench run-tests` runner
**Target Platform**: Linux server (x.conanacademy.com test site)
**Project Type**: Single (Frappe app)
**Performance Goals**: Full test suite completes within 60 seconds (SC-005)
**Constraints**: Must use existing season `SEAS-00027`; each test independently runnable (SC-003)
**Scale/Scope**: 22 tests across 7 test classes in a single test file

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

| Principle | Relevant? | Compliance Status | Notes |
|-----------|-----------|-------------------|-------|
| I. Cryptographic Security | No | N/A | Tests consume generated cards but don't test PIN generation (covered in Phase 3) |
| II. Auditable Lifecycle | **Yes** | PASS | Tests validate allocation state machine transitions (VALID_TRANSITIONS), card state lifecycle (Available→Allocated→Available), and batch status transitions (Generated→Active) |
| III. Financial Precision | **Yes** | PASS | Invoice amount tests verify commission calculations using `Decimal` math; assertions check `rate` and `amount` fields |
| IV. Self-Healing Architecture | No | N/A | Tests don't exercise Redis caching; allocation flow is MariaDB-only |
| V. Test-First Coverage | **Yes** | PASS | This IS the test implementation; covers positive + negative tests for every state transition, validates both happy paths and error scenarios (SC-006) |

**Pre-Phase 0 Gate**: PASS — No violations.

## Project Structure

### Documentation (this feature)

```text
specs/006-allocation-flow-tests/
├── plan.md              # This file
├── research.md          # Phase 0: Code analysis findings
├── data-model.md        # Phase 1: Entity relationships under test
├── quickstart.md        # Phase 1: How to run and extend tests
├── contracts/           # Phase 1: Test contracts (input→output)
│   └── test-contracts.md
└── tasks.md             # Phase 2 output (/speckit.tasks command)
```

### Source Code (repository root)

```text
memora_admin/memora_admin/
├── api/
│   └── allocation.py                  # Code under test (fill_cards, submit, approve, reject)
├── doctype/
│   └── memora_voucher_allocation/
│       └── memora_voucher_allocation.py  # on_update hooks (card state, batch counters, invoice)
├── services/voucher/
│   ├── commission.py                  # Commission resolution (tested indirectly)
│   └── invoice.py                     # Invoice creation (tested indirectly)
└── tests/
    ├── voucher_test_base.py           # VoucherTestCase base class (existing)
    ├── voucher_fixtures.py            # Factory functions (existing)
    ├── voucher_helpers.py             # Test helpers (existing)
    └── test_allocation_flow.py        # NEW: 22 integration tests (this feature)
```

**Structure Decision**: Single new test file `test_allocation_flow.py` in the existing `tests/` directory, following the established pattern from `test_invoice.py` and `test_commission.py`.

## Post-Design Constitution Re-Check

*Re-evaluated after Phase 1 design completion.*

| Principle | Compliance Status | Post-Design Notes |
|-----------|-------------------|-------------------|
| II. Auditable Lifecycle | PASS | 23 test contracts cover all VALID_TRANSITIONS paths: happy (Draft→Approved→Completed, Draft→Pending Approval→Approved→Completed), rejection (Pending Approval→Rejected), and invalid (Draft→Completed skip, terminal state escape). Card lifecycle (Available↔Allocated) fully tested. |
| III. Financial Precision | PASS | TC-19/TC-20 verify Prepaid invoice creation with Decimal-based commission math. TC-21 verifies Consignment produces no invoice. Commission priority chain tested indirectly through `resolve_commission()`. |
| V. Test-First Coverage | PASS | 23 tests across 7 classes covering all 6 user stories. Every FR (FR-001 through FR-020) mapped to at least one test contract. Both positive and negative scenarios covered for each workflow step. |

**Post-Design Gate**: PASS — No violations introduced by design.

## Complexity Tracking

> No constitution violations — table not needed.
