# Implementation Plan: Scholarship & Gift Voucher System

**Branch**: `034-scholarship-gift-vouchers` | **Date**: 2026-03-02 | **Spec**: [spec.md](spec.md)
**Input**: Feature specification from `/specs/034-scholarship-gift-vouchers/spec.md`

## Summary

Enable admins to create free voucher batches for scholarships, gifts, and promotions with a "Direct Activate" flow that bypasses library allocation. Extends existing Voucher Batch, Card, and Subscription Transaction DocTypes with new fields (`batch_purpose`, `recipient_note`, new payment methods). Adds cross-purpose guards to prevent mixing sale and non-sale distribution channels. Includes a dedicated Script Report for tracking non-sale grant distribution. Zero FastAPI/Redis changes — purely Frappe-side.

## Technical Context

**Language/Version**: Python 3.11+ (Frappe v15)
**Primary Dependencies**: Frappe Framework (ORM, DocTypes, hooks, Script Reports), ERPNext (Sales Invoice — unaffected)
**Storage**: MariaDB via Frappe ORM (existing tables extended with new fields)
**Testing**: `frappe.tests.utils.FrappeTestCase`, existing voucher test infrastructure (`VoucherTestBase`, `voucher_fixtures`, `voucher_helpers`)
**Target Platform**: Linux server (x.conanacademy.com)
**Project Type**: Single (Frappe module extension)
**Performance Goals**: N/A — admin-facing only (no game API hot paths affected)
**Constraints**: Max 1,000 cards per batch; `library='Admin-Direct'` set via direct SQL (bypasses ORM link validation); no new DocTypes
**Scale/Scope**: ~100k students, up to 1,000 cards per batch, 4 batch purposes

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

| # | Principle | Status | Notes |
|---|-----------|--------|-------|
| I | Self-Healing Cache Architecture | PASS | No Redis keys added. No cache changes. Purely Frappe-side. |
| II | Sub-20ms Game API Performance | PASS | No FastAPI endpoints modified. Zero game API impact. |
| III | Content Hierarchy Integrity | PASS | No content hierarchy changes. Voucher system is independent. |
| IV | Double-Gate Access Control | PASS | Redemption creates standard Subscription Transactions; access grants flow through existing `_handle_approval()` pipeline. |
| V | Cryptographic Voucher Security | PASS | PIN generation, HMAC storage, timing-safe verification, and Fernet export unchanged. Direct Activate uses existing `Allocated` status — redemption path identical. |
| VI | Financial Precision | PASS | Non-sale batches enforce `face_value=0`. No commission or invoice calculations. Existing `Decimal` paths in `invoice.py`/`commission.py` are not invoked. |
| VII | Auditable State Machines | PASS | Batch state machine unchanged (`Draft → Generated → Active → Closed`). Card state machine unchanged (`Available → Allocated → Redeemed/Void/Expired`). Direct Activate uses existing `Available → Allocated` transition. `batch_purpose` immutability enforced after Draft. |
| VIII | Test-First Coverage | PASS | 20 tests across 5 test classes covering validation, Direct Activate, cross-purpose guards, full redemption lifecycle, and Script Report. Uses `FrappeTestCase` + existing fixtures. |

**Pre-Phase 0 Gate**: PASS — no violations.
**Post-Phase 1 Gate**: PASS — design confirmed. No new Redis keys, no game API changes, no state machine modifications, no financial calculation changes.

## Project Structure

### Documentation (this feature)

```text
specs/034-scholarship-gift-vouchers/
├── plan.md              # This file
├── research.md          # Phase 0 output — 7 research decisions
├── data-model.md        # Phase 1 output — 3 entity changes
├── quickstart.md        # Phase 1 output — implementation scope and order
├── contracts/
│   └── api.md           # Phase 1 output — API contracts
├── checklists/
│   └── requirements.md  # Spec validation checklist
└── tasks.md             # Phase 2 output — 15 tasks across 8 phases
```

### Source Code (Frappe module)

```text
memora_admin/memora_admin/
├── doctype/
│   ├── memora_voucher_batch/
│   │   ├── memora_voucher_batch.json   # MODIFY: Add batch_purpose field
│   │   ├── memora_voucher_batch.py     # MODIFY: Add validation rules
│   │   └── memora_voucher_batch.js     # MODIFY: Direct Activate button, purpose lockdown
│   ├── memora_voucher_card/
│   │   ├── memora_voucher_card.json    # MODIFY: Add batch_purpose + recipient_note
│   │   └── memora_voucher_card.js      # MODIFY: Conditional recipient_note visibility
│   └── memora_subscription_transaction/
│       └── ...transaction.json         # MODIFY: Add Scholarship/Gift payment methods
├── api/
│   ├── voucher.py                      # MODIFY: Add direct_activate(), propagate batch_purpose
│   └── allocation.py                   # MODIFY: Add non-Sale guards to fill_cards/submit_allocation
├── report/
│   └── scholarship_gift_grants/        # CREATE: New Script Report
│       ├── __init__.py
│       ├── scholarship_gift_grants.json
│       ├── scholarship_gift_grants.py
│       └── scholarship_gift_grants.js
└── tests/
    └── test_scholarship_vouchers.py    # CREATE: 20 tests, 5 classes
```

**Structure Decision**: Frappe module extension pattern — modify existing DocTypes (JSON + Python + JS) and add one new Script Report. No new DocTypes. No FastAPI changes. All paths under `memora_admin/memora_admin/`.

## Complexity Tracking

No constitution violations. No complexity justifications needed.
