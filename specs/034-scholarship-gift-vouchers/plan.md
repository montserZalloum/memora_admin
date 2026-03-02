# Implementation Plan: Scholarship & Gift Voucher System

**Branch**: `034-scholarship-gift-vouchers` | **Date**: 2026-03-02 | **Spec**: [spec.md](./spec.md)
**Input**: Feature specification from `/specs/034-scholarship-gift-vouchers/spec.md`

## Summary

Enable admins to create free voucher batches for scholarships, gifts, and promotions with a dedicated "Direct Activate" flow that bypasses library allocation. Adds `batch_purpose` field to Batch and Card, enforces face_value=0 for non-Sale, gates allocation vs direct-activate by purpose, extends Subscription Transaction payment methods, and provides a dedicated Script Report for non-sale grants.

This is a **Frappe-only feature** — no FastAPI changes, no Redis changes, no game API impact. All changes are within the admin panel (DocTypes, form JS, API endpoints, Script Report).

## Technical Context

**Language/Version**: Python 3.11+ (Frappe v15)
**Primary Dependencies**: Frappe Framework (ORM, DocTypes, hooks, Script Reports), ERPNext (Sales Invoice — unaffected)
**Storage**: MariaDB via Frappe ORM (existing tables extended with new fields)
**Testing**: `frappe.tests.utils.FrappeTestCase`
**Target Platform**: Frappe admin panel (web UI)
**Project Type**: Single (Frappe app module)
**Performance Goals**: N/A — admin operations, not game API hot path
**Constraints**: Max batch size 1,000 cards, existing sale flows must be completely unchanged
**Scale/Scope**: ~5 DocType schema changes, 1 new API endpoint, 1 new Script Report, ~3 JS form changes

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

| # | Principle | Applicable? | Compliance | Notes |
|---|-----------|-------------|------------|-------|
| I | Self-Healing Cache Architecture | No | N/A | No Redis changes. Direct Activate bypasses library allocation — no cache keys affected. Redemption uses existing pipeline which already handles cache. |
| II | Sub-20ms Game API Performance | No | N/A | No FastAPI endpoints added/modified. Direct Activate is a Frappe admin action. |
| III | Content Hierarchy Integrity | No | N/A | Voucher system is independent of content hierarchy. |
| IV | Double-Gate Access Control | Indirect | PASS | Redemption of directly-activated cards uses the existing `_handle_approval()` pipeline which creates `Memora Player Subscription` records and fires Redis SADD. No changes to access control logic. |
| V | Cryptographic Voucher Security | Yes | PASS | No changes to PIN generation, HMAC storage, or export encryption. Direct Activate does not touch PINs — it only changes card status. Redemption flow unchanged. |
| VI | Financial Precision | Yes | PASS | Non-Sale batches enforce `face_value=0`. No commission, no invoice, no Decimal arithmetic needed. Sale batches are completely unchanged. |
| VII | Auditable State Machines | Yes | PASS | Direct Activate transitions cards `Available → Allocated` — an existing valid transition. Batch transitions `Generated → Active` — also existing. Library sentinel "Admin-Direct" provides audit trail. No new states added. |
| VIII | Test-First Coverage | Yes | PLAN | Tests will be written for: validation rules, Direct Activate flow, cross-purpose guards, report correctness. |

**Gate result**: PASS — no violations. All changes work within existing state machines and patterns.

## Project Structure

### Documentation (this feature)

```text
specs/034-scholarship-gift-vouchers/
├── spec.md              # Feature specification
├── plan.md              # This file
├── research.md          # Phase 0 output
├── data-model.md        # Phase 1 output
├── quickstart.md        # Phase 1 output
├── contracts/           # Phase 1 output
│   └── api.md           # Direct Activate endpoint contract
└── tasks.md             # Phase 2 output (by /speckit.tasks)
```

### Source Code (repository root)

```text
memora_admin/memora_admin/
├── doctype/
│   ├── memora_voucher_batch/
│   │   ├── memora_voucher_batch.json      # MODIFY: add batch_purpose field
│   │   ├── memora_voucher_batch.py        # MODIFY: add validation rules
│   │   └── memora_voucher_batch.js        # MODIFY: add Direct Activate button, purpose-based UI
│   ├── memora_voucher_card/
│   │   ├── memora_voucher_card.json       # MODIFY: add batch_purpose, recipient_note fields
│   │   ├── memora_voucher_card.py         # MODIFY: depends_on for recipient_note visibility
│   │   └── memora_voucher_card.js         # MODIFY: conditional field visibility
│   └── memora_subscription_transaction/
│       └── memora_subscription_transaction.json  # MODIFY: add payment_method options
├── api/
│   └── voucher.py                         # MODIFY: add direct_activate(), guard allocation
├── services/voucher/
│   └── batch_utils.py                     # MODIFY: extend recount for direct-activate awareness
├── report/
│   └── scholarship_gift_grants/           # NEW: Script Report
│       ├── scholarship_gift_grants.json
│       ├── scholarship_gift_grants.py
│       └── scholarship_gift_grants.js
└── tests/
    └── test_scholarship_vouchers.py       # NEW: test suite
```

**Structure Decision**: Extends existing Frappe module structure. No new DocTypes — only field additions to existing DocTypes and one new Script Report.

## Phase 0: Research

### Research Task 1: Direct Activate — Batch State Machine Integration

**Question**: How should Direct Activate interact with the existing batch state machine? Currently `Generated → Active` is triggered by the first allocation completing (`_activate_batch_if_needed()`). Direct Activate should also trigger this transition.

**Decision**: Direct Activate performs a bulk SQL UPDATE on all `Available` cards in the batch, setting `status='Allocated'`, `library='Admin-Direct'`. Then it transitions the batch from `Generated → Active` (same as allocation does). This mirrors `_apply_allocation()` in the allocation DocType but without creating an Allocation document.

**Rationale**: Creating an Allocation document would add unnecessary complexity — the Allocation DocType is designed for library-based transfers with approval workflows, sale models, and invoicing. Direct Activate has none of these. A standalone API function is cleaner.

**Alternatives considered**:
1. *Create a hidden Allocation doc* — rejected because it requires a Customer link (library), sale_model selection, and triggers invoice logic we explicitly want to bypass.
2. *New batch status "Directly Activated"* — rejected because it adds a new state to the batch machine. Using existing `Active` status keeps the state machine unchanged and allows existing reports/queries to work.

### Research Task 2: "Admin-Direct" Sentinel in Library Field

**Question**: The `library` field on Voucher Card is a Link field to `Customer`. Setting it to "Admin-Direct" (a non-existent Customer) would break Frappe's link validation.

**Decision**: Use `frappe.db.sql()` (direct SQL UPDATE) to set `library='Admin-Direct'` — bypassing ORM link validation. This is the same pattern used in `_apply_allocation()` which also uses direct SQL for bulk updates. The `library` field on the Card form will show "Admin-Direct" as plain text (Frappe renders unresolvable links as the raw value, which is acceptable).

**Rationale**: Creating a real Customer record named "Admin-Direct" would pollute the Customer list used for actual B2B libraries. A sentinel string via SQL is cleaner and matches the existing bulk-update pattern.

**Alternatives considered**:
1. *Create a real Customer "Admin-Direct"* — rejected because it pollutes the Customer DocType with a fake entry that admins might accidentally use in other contexts.
2. *Use NULL for library* — rejected because the library field is used in reports and audit trails. NULL would be ambiguous (could mean "not yet allocated").
3. *New field `activation_method`* — rejected as over-engineering. The `library='Admin-Direct'` sentinel is sufficient for filtering and reporting.

### Research Task 3: Batch Purpose Field — Select vs Link

**Question**: Should `batch_purpose` be a Select field or a Link to a separate DocType?

**Decision**: Select field with options: `Sale\nScholarship\nGift\nPromotion`. Default: `Sale`.

**Rationale**: The spec defines exactly 4 fixed values. A Link field would require a new DocType, add admin overhead, and provide no benefit since the values are business-defined constants unlikely to change.

### Research Task 4: Card-Level batch_purpose — Stored vs Fetched

**Question**: Should each card store `batch_purpose` or fetch it from the parent batch?

**Decision**: Store as a read-only field on each card (propagated from batch during `generate_cards_job()`). This enables:
- List view filtering by purpose without joins
- Conditional field visibility in JS without additional API calls
- Report queries directly on the Card table

**Rationale**: Denormalization is justified because (a) `batch_purpose` is immutable after generation, (b) it enables O(1) lookups on the card, (c) the spec explicitly requires it (FR-014).

### Research Task 5: Export Flow for Direct Activate Cards

**Question**: Does `export_for_print()` work for directly activated cards?

**Decision**: No modification needed. Currently `export_for_print()` filters to `status='Available'` cards only. For directly activated batches, ALL cards are `Allocated` — so export returns an empty CSV. However, the export should happen BEFORE Direct Activate (while cards are still Available). The admin workflow is: Generate → Export for Print → Direct Activate → Distribute PINs.

**Rationale**: The spec assumption "The existing card export functionality works for directly activated cards without modification" is correct in the sense that export_for_print still functions — it just returns no rows if called after activation. The admin should export first, then activate.

**Alternative**: Modify export to include `Allocated` cards with `library='Admin-Direct'`. Rejected because it would also include library-allocated cards by default, requiring additional filtering logic. The "export then activate" workflow is cleaner.

### Research Task 6: Guard Points for Cross-Purpose Enforcement

**Question**: Where exactly should guards be placed to prevent cross-purpose misuse?

**Decision**: Three guard points:
1. **`fill_cards()` in `allocation.py`**: Reject if batch purpose is non-Sale. Message: "Cannot allocate cards from a non-sale batch. Use Direct Activate instead."
2. **`submit_allocation()` in `allocation.py`**: Same guard (defense in depth).
3. **`direct_activate()` in `voucher.py`**: Reject if batch purpose is Sale. Message: "Direct Activate is only available for non-sale batches."

Plus JS-level visibility: Direct Activate button only shown for non-Sale batches in Generated status.

**Rationale**: Server-side guards at both API entry points ensure enforcement even if JS is bypassed. The double-guard (fill_cards + submit_allocation) prevents partial progress on an invalid allocation.

### Research Task 7: Manual Subscription Transaction — Scholarship/Gift Payment Methods

**Question**: How to add "Scholarship" and "Gift" to the payment_method Select field?

**Decision**: Update the `options` string in `memora_subscription_transaction.json` to:
```
Payment Gateway\nManual-Admin\nVoucher\nScholarship\nGift
```

No code changes needed — the existing `on_update()` handler already processes any payment method that reaches `Completed` status. For Scholarship/Gift transactions, `amount_paid=0` and no voucher card is linked (no `transaction_id`). No invoice is generated because invoice creation is only triggered by the allocation flow, not by subscription transactions directly.

**Rationale**: Minimal change — reuses the existing pipeline. The payment method string serves as the audit trail for how access was granted.

## Phase 1: Design

*(See `data-model.md`, `contracts/api.md`, `quickstart.md` for full artifacts)*

### Data Model Summary

**Memora Voucher Batch** — 1 new field:
- `batch_purpose` (Select: Sale/Scholarship/Gift/Promotion, default Sale, reqd)

**Memora Voucher Card** — 2 new fields:
- `batch_purpose` (Select, read-only, propagated from batch)
- `recipient_note` (Small Text, visible only for non-Sale)

**Memora Subscription Transaction** — 2 new options:
- `payment_method` adds: `Scholarship`, `Gift`

### API Summary

**New endpoint**: `direct_activate(batch_name)` — @frappe.whitelist
- Guards: batch must be `Generated`, purpose must be non-Sale
- Bulk SQL: `UPDATE cards SET status='Allocated', library='Admin-Direct' WHERE batch=? AND status='Available'`
- Transitions batch: `Generated → Active`
- Updates `allocated_count`
- Returns `{status: "activated", activated_count: N}`

**Modified endpoints**:
- `fill_cards()`: Add guard rejecting non-Sale batches
- `submit_allocation()`: Add guard rejecting non-Sale batches
- `generate_cards_job()`: Propagate `batch_purpose` to cards during bulk insert

### Validation Rules

1. `batch.validate()`: If `batch_purpose != 'Sale'` and `face_value > 0` → throw
2. `batch.validate()`: If status != 'Draft', `batch_purpose` is immutable
3. `card.validate()`: `batch_purpose` and `recipient_note` read-only rules

### Report: Scholarship & Gift Grants

- **Type**: Script Report on `Memora Voucher Batch`
- **Filters**: batch_purpose (multi-select non-Sale), date range, product grant
- **Columns**: batch, purpose, product grant, total, activated, redeemed, voided, remaining
- **Summary**: Total cards, total redeemed, avg redemption rate
- **Drill-down**: Not a Frappe Script Report native feature — implement as a linked view (click batch → opens batch form filtered to its cards)

## Constitution Re-Check (Post-Design)

| # | Principle | Status |
|---|-----------|--------|
| V | Cryptographic Voucher Security | PASS — Direct Activate uses SQL UPDATE on status only. No PIN exposure. |
| VI | Financial Precision | PASS — face_value=0 enforced. No Decimal arithmetic on non-Sale batches. |
| VII | Auditable State Machines | PASS — All transitions are existing valid transitions. "Admin-Direct" sentinel provides audit trail. Redemption Log captures library as "Admin-Direct". |
| VIII | Test-First Coverage | PLAN — Test cases defined in spec acceptance scenarios. |

**Final gate**: PASS

## Complexity Tracking

No violations to justify. All changes work within existing patterns.
