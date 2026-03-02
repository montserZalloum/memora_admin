# Data Model: Scholarship & Gift Voucher System

**Feature**: 034-scholarship-gift-vouchers | **Date**: 2026-03-02

## Entity Changes

### 1. Memora Voucher Batch (MODIFY)

**File**: `memora_admin/memora_admin/doctype/memora_voucher_batch/memora_voucher_batch.json`

| New Field | Type | Options | Default | Required | Read-Only | Notes |
|---|---|---|---|---|---|---|
| `batch_purpose` | Select | Sale\nScholarship\nGift\nPromotion | Sale | Yes | After Draft | Governs distribution flow and face_value constraint |

**Placement**: After `batch_name`, before `status` (Section Break: Configuration).

**Validation rules** (in `memora_voucher_batch.py`):
1. If `batch_purpose != 'Sale'` and `face_value > 0`: throw "Non-sale batches must have zero face value."
2. If `status != 'Draft'` and `batch_purpose` has changed from DB value: throw "Batch purpose cannot be changed after Draft status."

**State machine**: Unchanged. Direct Activate uses existing `Generated → Active` transition.

**JS changes** (`memora_voucher_batch.js`):
1. `batch_purpose` change handler: auto-set `face_value = 0` and make read-only when non-Sale
2. `refresh`: show Direct Activate button only for non-Sale + Generated status
3. `refresh`: make `batch_purpose` read-only when status != Draft

---

### 2. Memora Voucher Card (MODIFY)

**File**: `memora_admin/memora_admin/doctype/memora_voucher_card/memora_voucher_card.json`

| New Field | Type | Options | Default | Required | Read-Only | Notes |
|---|---|---|---|---|---|---|
| `batch_purpose` | Select | Sale\nScholarship\nGift\nPromotion | Sale | No | Yes (always) | Propagated from batch at generation time |
| `recipient_note` | Small Text | — | — | No | No | Visible only for non-Sale cards. Hidden on Sale cards. |

**Placement**:
- `batch_purpose`: After `batch`, before `status`
- `recipient_note`: After `void_reason` (at the end of the form)

**Conditional visibility**:
- JS: `recipient_note` visible only when `batch_purpose != 'Sale'`
- JSON: `depends_on: eval:doc.batch_purpose && doc.batch_purpose !== 'Sale'`

**Propagation**: `batch_purpose` is set during `generate_cards_job()` bulk insert. For Direct Activate, the value is already on the card.

---

### 3. Memora Subscription Transaction (MODIFY)

**File**: `memora_admin/memora_admin/doctype/memora_subscription_transaction/memora_subscription_transaction.json`

| Modified Field | Change |
|---|---|
| `payment_method` | Add options: `Scholarship`, `Gift` |

**New options string**: `Payment Gateway\nManual-Admin\nVoucher\nScholarship\nGift`

**No code changes** — existing `_handle_approval()` handles all payment methods generically.

---

## No New DocTypes

This feature does not create any new DocTypes. All data is stored in existing DocTypes with new fields.

## Entity Relationships

```
Memora Voucher Batch (batch_purpose)
    │
    ├── [generate] → Memora Voucher Card (batch_purpose, recipient_note)
    │                    │
    │                    ├── [library allocation] → library = Customer name
    │                    │                          (Sale batches only)
    │                    │
    │                    └── [direct activate] → library = "Admin-Direct"
    │                                            (Non-Sale batches only)
    │
    └── [redeem] → Memora Subscription Transaction (payment_method: "Voucher")
                   Memora Voucher Redemption Log (library: "Admin-Direct")

Memora Subscription Transaction (payment_method: "Scholarship"/"Gift")
    └── [manual admin grant] → No voucher card involved
```

## Field Dependencies

| Field | Source | When Set | Immutable After |
|---|---|---|---|
| `batch.batch_purpose` | Admin form input | Batch creation | Draft status |
| `card.batch_purpose` | `batch.batch_purpose` | Card generation | Always (read-only) |
| `card.library` | "Admin-Direct" literal | Direct Activate | Standard rules (return clears it) |
| `card.recipient_note` | Admin form input | Any time (non-Sale only) | Never (editable) |
| `trx.payment_method` | "Scholarship"/"Gift" | Manual creation | Standard rules |
