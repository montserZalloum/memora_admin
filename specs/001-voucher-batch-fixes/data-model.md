# Data Model: Voucher Batch Counter Fixes & Auto-Close

**Date**: 2026-02-15

## Entity Changes

### 1. Memora Voucher Batch (MODIFY)

**DocType**: `Memora Voucher Batch`
**File**: `memora_admin/memora_admin/doctype/memora_voucher_batch/memora_voucher_batch.json`

#### New Field

| Field | Type | Label | Read Only | Default | Required | Position |
|-------|------|-------|-----------|---------|----------|----------|
| `expired_count` | Int | Expired Count | Yes | 0 | No | After `voided_count` |

#### Existing Fields (unchanged but now actively maintained)

| Field | Currently Maintained By | Gap | Fix |
|-------|------------------------|-----|-----|
| `allocated_count` | `_update_batch_counters()` in allocation.py only | Not recounted after redemption or individual void | Recounted by shared helper |
| `redeemed_count` | `redeem_voucher()` only | Correct but now part of shared recount | No functional change |
| `voided_count` | `void_card()` and `void_batch()` | Correct but now part of shared recount | No functional change |

#### Counter Invariant

At all times for an Active batch:
```
generated_count = allocated_count + redeemed_count + voided_count + expired_count + available_count
```
Where `available_count` is implicit (not stored): `generated_count - allocated_count - redeemed_count - voided_count - expired_count`

### 2. Memora Voucher Card (NO CHANGE)

**DocType**: `Memora Voucher Card`

No schema changes. The `Expired` status already exists in the Select options:
```
Available | Allocated | Redeemed | Void | Expired
```

Valid transitions (existing, unchanged):
```
Available  → {Allocated, Void, Expired}
Allocated  → {Redeemed, Void, Expired, Available}
Redeemed   → {} (terminal)
Void       → {} (terminal)
Expired    → {} (terminal)
```

### 3. Memora Voucher Batch (Status Transitions — NO SCHEMA CHANGE)

Existing state machine (unchanged):
```
Draft → Generated → Active → Closed
                 ↘         ↗
                   Closed
```

**New behavior**: The `Active → Closed` transition is now triggered automatically (in addition to manually via `void_batch()`).

**Auto-close trigger condition**:
```python
batch.status == "Active"
AND frappe.db.count("Memora Voucher Card", {
    "batch": batch_name,
    "status": ["in", ["Available", "Allocated"]]
}) == 0
```

**Auto-close distinction**: Auto-closed batches have `void_reason = NULL/empty`. Manually voided batches have `void_reason` populated.

## New Module

### `services/voucher/batch_utils.py`

**Purpose**: Shared helper for batch counter recount and auto-close evaluation.

**Function**: `recount_and_maybe_close(batch_name: str) -> dict`

**Behavior**:
1. Count cards by status using 4 `frappe.db.count()` queries
2. Update all 4 counter fields on the batch via `frappe.db.set_value()`
3. Check if zero non-terminal cards remain (Available + Allocated == 0)
4. If batch is Active and condition met, transition to Closed via `frappe.db.set_value()`
5. Return dict with counter values and whether batch was closed

**Call Sites**:
- `api/voucher.py:redeem_voucher()` — replaces manual `redeemed_count` update
- `api/voucher.py:void_card()` — replaces manual `voided_count` update
- `tasks/season_expiration.py:expire_season_cards()` — new addition after card expiration

**Not called from**:
- `api/voucher.py:void_batch()` — already handles its own closure explicitly
- `memora_voucher_allocation.py:_update_batch_counters()` — keeps its own recount (only modifies allocated_count, and auto-close should not trigger from allocation since batch may still be Generated)

## Validation Rules

| Rule | Enforced By | Type |
|------|-------------|------|
| `expired_count` is read-only | DocType JSON (`"read_only": 1`) | Schema |
| `expired_count` defaults to 0 | DocType JSON (`"default": "0"`) | Schema |
| Batch status transitions are valid | `MemoraVoucherBatch._validate_status_transition()` | Python |
| Auto-close only on Active batches | `recount_and_maybe_close()` status check | Python |
| Auto-close skips empty batches | Implicit — empty batches never reach Active status | Business logic |
| Counters use recount (not increment) | `recount_and_maybe_close()` implementation | Python |
