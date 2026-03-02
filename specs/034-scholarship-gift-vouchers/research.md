# Research: Scholarship & Gift Voucher System

**Feature**: 034-scholarship-gift-vouchers | **Date**: 2026-03-02

## R1: Direct Activate — Batch State Machine Integration

**Decision**: Direct Activate is a standalone API endpoint (`direct_activate(batch_name)`) that performs a bulk SQL UPDATE on all Available cards, setting `status='Allocated'` and `library='Admin-Direct'`. It then transitions the batch from `Generated → Active` and updates the `allocated_count` counter. No Allocation document is created.

**Rationale**: The Allocation DocType is designed for library-based transfers with approval workflows, sale models, and invoicing. Direct Activate has none of these concerns. A standalone function avoids coupling to library-specific logic.

**Alternatives considered**:
1. Create a hidden Allocation doc with a synthetic Customer — rejected: requires sale_model, triggers invoice logic, pollutes allocation list.
2. Add a new batch status "Directly Activated" — rejected: adds state to the machine, breaks existing reports that filter on "Active".
3. Create a lightweight "Direct Allocation" DocType — rejected: over-engineering for what is essentially a bulk status update.

**Code references**:
- `_apply_allocation()` in `memora_voucher_allocation.py:104` — existing bulk SQL UPDATE pattern to mirror
- `_activate_batch_if_needed()` in `memora_voucher_allocation.py` — existing `Generated → Active` transition to replicate

---

## R2: "Admin-Direct" Sentinel Value

**Decision**: Set `library='Admin-Direct'` via direct SQL (bypassing Frappe ORM link validation). The Card's `library` field is a Link to `Customer`, but direct SQL allows non-existent values. Frappe renders unresolvable links as raw text.

**Rationale**: Creating a real Customer would pollute the B2B library list. NULL would be ambiguous. A sentinel string provides a clear audit trail visible in reports and the Redemption Log.

**Validation**: Tested pattern — existing `_apply_allocation()` already uses `frappe.db.sql()` for bulk card updates, setting `library` to a Customer name via SQL. The same pattern works with a non-existent value.

**Impact on existing code**:
- `export_for_print()`: No impact — filters by `status='Available'`, not by library.
- `void_batch()` / `void_card()`: No impact — operates on status, not library.
- `redeem_voucher()`: No impact — checks `status='Allocated'`, reads `card.library` for the Redemption Log. "Admin-Direct" will appear in the log.
- `recount_and_maybe_close()`: No impact — counts by status, not library.
- `fill_cards()`: Will be guarded to reject non-Sale batches before reaching card queries.
- Reports (Batch Performance, Sales by Library): Sales by Library joins on Customer — "Admin-Direct" rows won't match, which is correct (non-sale cards have no sales). Batch Performance works on batch-level data, unaffected.

---

## R3: Batch Purpose Field Design

**Decision**: Select field `batch_purpose` with options `Sale\nScholarship\nGift\nPromotion`, default `Sale`, required, placed after `batch_name` in the form layout.

**Rationale**: Fixed business-defined values. A separate DocType would add overhead with no extensibility benefit.

**Read-only enforcement**: After batch leaves Draft status, `batch_purpose` becomes read-only. Enforced in both Python (`validate()`) and JS (`refresh()`).

---

## R4: Card-Level Purpose Propagation

**Decision**: Add `batch_purpose` (Select, read-only) to `Memora Voucher Card`. Populated during `generate_cards_job()` bulk insert.

**Rationale**: Enables card-level filtering in list views and conditional `recipient_note` visibility without parent-batch lookups.

**Implementation**: Add `batch_purpose` to the `fields` and `insert_rows` arrays in `generate_cards_job()`. Each card row gets the batch's purpose value.

---

## R5: Export Flow Compatibility

**Decision**: No changes to `export_for_print()`. Admin workflow: Generate → Export → Direct Activate → Distribute.

**Rationale**: Export filters `status='Available'`. After Direct Activate, all cards are `Allocated`, so export returns empty. This is correct — PINs should be exported before activation.

**Alternative explored**: Modify export to include `library='Admin-Direct'` cards. Rejected because it changes export semantics for all batches and requires additional filter logic.

---

## R6: Cross-Purpose Guard Placement

**Decision**: Three server-side guards + JS-level UI gates.

| Guard Point | File | Condition | Error Message |
|---|---|---|---|
| `fill_cards()` | `allocation.py` | `batch.batch_purpose != 'Sale'` | "Cannot allocate cards from a non-sale batch. Use Direct Activate instead." |
| `submit_allocation()` | `allocation.py` | `batch.batch_purpose != 'Sale'` | "Cannot allocate cards from a non-sale batch. Use Direct Activate instead." |
| `direct_activate()` | `voucher.py` | `batch.batch_purpose == 'Sale'` | "Direct Activate is only available for non-sale batches (Scholarship, Gift, Promotion)." |
| Batch form JS | `memora_voucher_batch.js` | Hide Direct Activate button for Sale | N/A (button not rendered) |
| Allocation form JS | `memora_voucher_allocation.js` | Show warning if batch is non-Sale | N/A (informational) |

**Rationale**: Server-side enforcement is mandatory (JS can be bypassed). Double-guarding at fill_cards + submit_allocation prevents partial invalid allocations.

---

## R7: Payment Method Extension

**Decision**: Add `Scholarship` and `Gift` to `payment_method` Select options on `Memora Subscription Transaction`.

**Current options**: `Payment Gateway\nManual-Admin\nVoucher`
**New options**: `Payment Gateway\nManual-Admin\nVoucher\nScholarship\nGift`

**Impact analysis**:
- `_handle_approval()`: Works for any payment method — no method-specific branching except skip-notification for "Voucher". Scholarship/Gift will get the real-time notification (desirable for manual admin grants).
- `_handle_rejection()`: Works generically.
- Reports: Payment method is filterable — new values appear naturally.
- Redis cleanup: `r.srem(pending_key)` runs for all methods. Scholarship/Gift won't have pending entries (admin creates directly as Completed), so it's a no-op.

**No code changes needed** — only the JSON schema option string.
