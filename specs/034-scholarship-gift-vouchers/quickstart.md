# Quickstart: Scholarship & Gift Voucher System

**Feature**: 034-scholarship-gift-vouchers | **Date**: 2026-03-02

## What This Feature Does

Enables admins to create and distribute free voucher batches for scholarships, gifts, and promotions — separate from the paid sales workflow. Cards can be "directly activated" without going through library allocation.

## Implementation Scope

### Files to Modify

| File | Change Type | What Changes |
|---|---|---|
| `memora_voucher_batch.json` | Schema | Add `batch_purpose` Select field |
| `memora_voucher_batch.py` | Logic | Add validation: face_value=0 for non-Sale, immutable purpose after Draft |
| `memora_voucher_batch.js` | UI | Add Direct Activate button, purpose-based face_value lockdown |
| `memora_voucher_card.json` | Schema | Add `batch_purpose` (read-only), `recipient_note` (conditional) |
| `memora_voucher_card.js` | UI | Conditional visibility for `recipient_note` |
| `memora_subscription_transaction.json` | Schema | Add "Scholarship", "Gift" to payment_method options |
| `voucher.py` (API) | Logic | Add `direct_activate()`, propagate `batch_purpose` in generation |
| `allocation.py` (API) | Logic | Add non-Sale guard to `fill_cards()` and `submit_allocation()` |

### Files to Create

| File | Type | Purpose |
|---|---|---|
| `report/scholarship_gift_grants/` | Script Report | Non-sale batch tracking with card distribution stats |
| `tests/test_scholarship_vouchers.py` | Tests | Validation, Direct Activate, guards, report |

### Files NOT Modified

- `generator.py` — PIN generation unchanged
- `crypto.py` — Export encryption unchanged
- `commission.py` — Not invoked for non-Sale (face_value=0)
- `invoice.py` — Not invoked for non-Sale (no allocation)
- `batch_utils.py` — `recount_and_maybe_close()` works as-is (counts by status, not library)
- `memora_subscription_transaction.py` — `_handle_approval()` is generic
- All FastAPI code — zero changes
- All Redis code — zero changes

## Implementation Order

1. **Schema changes** (batch_purpose, card fields, payment_method) — DocType JSON updates + `bench migrate`
2. **Batch validation** (face_value constraint, immutable purpose) — Python
3. **Card generation** (propagate batch_purpose) — modify `generate_cards_job()`
4. **Direct Activate endpoint** — new API function
5. **Cross-purpose guards** — modify `fill_cards()`, `submit_allocation()`
6. **Form JS updates** — buttons, conditional fields, read-only states
7. **Script Report** — new report for non-sale batches
8. **Tests** — validation, flow, guards, report

## Key Design Decisions

| Decision | Why |
|---|---|
| No new DocTypes | Existing DocTypes extended with minimal fields |
| Direct SQL for `library='Admin-Direct'` | Avoids creating a fake Customer record |
| Export before activate | `export_for_print` filters Available cards — export PINs first, then activate |
| `batch_purpose` on Card (denormalized) | Enables list filtering and conditional visibility without joins |
| No Allocation doc for Direct Activate | Allocation DocType has library-specific concerns (sale_model, invoice, approval) |

## Testing Strategy

- **Unit**: Validation rules (face_value constraint, immutable purpose)
- **Integration**: Full lifecycle (create non-Sale batch → generate → activate → redeem)
- **Guard tests**: Verify allocation rejection for non-Sale, direct activate rejection for Sale
- **Report tests**: Verify correct counts and filtering
