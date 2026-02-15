# Implementation Plan: Integration Tests — Redemption Flow

**Branch**: `007-redemption-flow-tests` | **Date**: 2026-02-15 | **Spec**: [spec.md](spec.md)
**Input**: Feature specification from `/specs/007-redemption-flow-tests/spec.md`

## Summary

Implement 22 integration tests covering the voucher redemption flow: successful redemption (US1, 4 tests), all 9 error paths (US2, 9 tests), preview functionality (US3, 3 tests), audit logging and security (US4, 5 tests), and batch auto-close (US5, 1 test). Tests use existing infrastructure (VoucherTestCase, fixtures, helpers) and are written to the Voucher Card DocType test stub. Two helper enhancements are required: a PIN extraction helper and a preview-by-PIN helper.

## Technical Context

**Language/Version**: Python 3.11+ (Frappe v15)
**Primary Dependencies**: Frappe Framework (`frappe.tests.utils.FrappeTestCase`), `hmac` module, `csv`/`io` (for PIN extraction)
**Storage**: MariaDB via Frappe ORM (card records, batch state, redemption logs, subscription transactions)
**Testing**: Frappe's `FrappeTestCase` with pytest runner via `bench run-tests`
**Target Platform**: Linux server (x.conanacademy.com)
**Project Type**: Single project (Frappe app)
**Performance Goals**: Full test suite completes within 60 seconds
**Constraints**: Tests must be independent and idempotent; reuse existing season `SEAS-00027`
**Scale/Scope**: 22 tests across 5 user stories in a single test file

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

| Principle | Status | Notes |
|-----------|--------|-------|
| I. Cryptographic Security | PASS | Tests verify `hmac.compare_digest()` usage (FR-011), HMAC-based PIN lookup, no plaintext PIN persistence |
| II. Auditable Lifecycle | PASS | Tests verify immutable Redemption Log entries for every attempt, card state machine transitions, batch counter consistency |
| III. Financial Precision | PASS | Tests verify subscription transaction creation; face value passed through correctly |
| IV. Self-Healing Architecture | N/A | No Redis operations in test scope; tests operate at Frappe ORM layer |
| V. Test-First Coverage | PASS | This IS the test coverage — every error code tested, every state transition tested, both positive and negative cases |

**Pre-Phase 0 Gate**: PASS — no violations.

## Project Structure

### Documentation (this feature)

```text
specs/007-redemption-flow-tests/
├── plan.md              # This file
├── research.md          # Phase 0: Research findings
├── data-model.md        # Phase 1: Entity/field reference for tests
├── quickstart.md        # Phase 1: How to run the tests
└── contracts/
    └── test-matrix.md   # Phase 1: FR → test mapping
```

### Source Code (repository root)

```text
memora_admin/memora_admin/
├── doctype/memora_voucher_card/
│   └── test_memora_voucher_card.py   # PRIMARY: All 22 redemption tests (FR-014)
├── tests/
│   ├── voucher_test_base.py          # EXISTING: VoucherTestCase base class
│   ├── voucher_fixtures.py           # MODIFY: Add grant_components support to make_product_grant()
│   └── voucher_helpers.py            # MODIFY: Add get_pins_from_export(), preview_card_by_pin()
└── api/
    └── voucher.py                    # READ-ONLY: Source of redeem_voucher(), preview_voucher()
```

**Structure Decision**: Tests go in the existing Voucher Card DocType stub per FR-014. Fixture/helper enhancements go in the existing shared infrastructure files.

## Post-Design Constitution Re-Check

| Principle | Status | Post-Design Notes |
|-----------|--------|-------------------|
| I. Cryptographic Security | PASS | TC-21 verifies `compare_digest` via source inspection. PIN retrieval uses `export_for_print()` (Fernet-encrypted) — no plaintext persisted. HMAC secret read from `site_config.json` per constitution. |
| II. Auditable Lifecycle | PASS | TC-04/TC-17-20 verify Redemption Log immutability. TC-01-03 verify card state machine. TC-22 verifies batch auto-close. All state transitions covered. |
| III. Financial Precision | PASS | TC-01 verifies Subscription Transaction.amount_paid matches batch.face_value. No float math in tests — values compared as-is from Frappe ORM. |
| IV. Self-Healing Architecture | N/A | Tests don't touch Redis. Redemption flow operates at DB layer. |
| V. Test-First Coverage | PASS | 22 tests cover: 9/9 error codes (100%), positive + negative for each state, audit log for every attempt type, timing-safe comparison, batch auto-close. Meets SC-001 through SC-005. |

**Post-Design Gate**: PASS — no violations introduced during design.

## Complexity Tracking

No constitution violations — table not needed.
