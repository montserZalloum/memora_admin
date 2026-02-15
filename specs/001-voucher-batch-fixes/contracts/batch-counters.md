# Internal API Contract: Batch Counter Recount & Auto-Close

**Date**: 2026-02-15 | **Type**: Internal Python API (not HTTP)

## Module

`memora_admin.memora_admin.services.voucher.batch_utils`

## Function: `recount_and_maybe_close`

### Signature

```python
def recount_and_maybe_close(batch_name: str) -> dict:
    """Recount all batch counters from actual card states and auto-close if eligible.

    Args:
        batch_name: The name of the Memora Voucher Batch to update.

    Returns:
        dict with keys:
            allocated_count (int): Current count of Allocated cards
            redeemed_count (int): Current count of Redeemed cards
            voided_count (int): Current count of Void cards
            expired_count (int): Current count of Expired cards
            closed (bool): Whether the batch was auto-closed by this call
    """
```

### Behavior Contract

1. **Recount phase** (ALWAYS runs):
   - Queries `tabMemora Voucher Card` for each status using `frappe.db.count()`
   - Updates `allocated_count`, `redeemed_count`, `voided_count`, `expired_count` on the batch
   - Uses `frappe.db.set_value()` with `update_modified=True`

2. **Auto-close phase** (conditional):
   - Reads current batch status
   - If `status == "Active"` AND `allocated_count + available_count == 0` (where available = no cards with status Available or Allocated):
     - Sets `status = "Closed"` via `frappe.db.set_value()`
     - Returns `closed: True`
   - Otherwise returns `closed: False`

3. **Does NOT**:
   - Call `frappe.db.commit()` — caller is responsible for transaction boundaries
   - Update `generated_count` — this is set once during card generation and never changes
   - Modify `void_reason` — auto-closed batches are distinguished by absence of void_reason
   - Close batches in Draft or Generated status (only Active)

### Idempotency

Calling `recount_and_maybe_close()` multiple times for the same batch produces identical counter values. If the batch was already closed on a previous call, subsequent calls update counters but skip the close transition (batch is already Closed).

### Performance

- 4 `COUNT(*)` queries on indexed `batch` + `status` columns
- 1 `SET_VALUE` call (single UPDATE statement for all counters)
- 1 conditional `SET_VALUE` for status transition
- Total: < 5ms for max 1000 cards per batch

## Call Site Contracts

### 1. `redeem_voucher()` in `api/voucher.py`

**Before** (current):
```python
# 14. Update batch redeemed_count
redeemed = frappe.db.count("Memora Voucher Card", {"batch": card.batch, "status": "Redeemed"})
frappe.db.set_value("Memora Voucher Batch", card.batch, "redeemed_count", redeemed, update_modified=True)
```

**After** (planned):
```python
# 14. Recount batch counters and auto-close if all cards terminal
from memora_admin.memora_admin.services.voucher.batch_utils import recount_and_maybe_close
recount_and_maybe_close(card.batch)
```

**Effect**: Now also recounts `allocated_count` (fixing the gap where Allocated → Redeemed didn't update allocated_count) and checks for auto-close.

### 2. `void_card()` in `api/voucher.py`

**Before** (current):
```python
# Update parent batch voided_count
new_voided = frappe.db.count("Memora Voucher Card", {"batch": card.batch, "status": "Void"})
frappe.db.set_value("Memora Voucher Batch", card.batch, "voided_count", new_voided)
```

**After** (planned):
```python
# Recount batch counters and auto-close if all cards terminal
from memora_admin.memora_admin.services.voucher.batch_utils import recount_and_maybe_close
recount_and_maybe_close(card.batch)
```

**Effect**: Now also recounts `allocated_count` (fixing the gap where Allocated → Void didn't update allocated_count) and checks for auto-close.

### 3. `expire_season_cards()` in `tasks/season_expiration.py`

**Before** (current):
```python
# No counter update after expiring cards
```

**After** (planned):
```python
# After expiring cards in a batch, recount and auto-close
from memora_admin.memora_admin.services.voucher.batch_utils import recount_and_maybe_close
recount_and_maybe_close(batch_name)
```

**Effect**: New — updates all counters (especially `expired_count` and `allocated_count`) and auto-closes the batch if all cards are now terminal.

### 4. `void_batch()` in `api/voucher.py` — NO CHANGE

`void_batch()` continues to handle its own counter updates and explicit closure. It sets `void_reason` to distinguish from auto-close.
