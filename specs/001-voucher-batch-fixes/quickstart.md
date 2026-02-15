# Quickstart: Voucher Batch Counter Fixes & Auto-Close

**Date**: 2026-02-15

## What This Feature Does

1. **Tracks expired cards separately** — New `expired_count` field on Voucher Batch, updated automatically when the daily season expiration job runs.

2. **Fixes stale `allocated_count`** — After redemption or individual void, `allocated_count` is now recounted (previously it was only updated by allocation operations).

3. **Auto-closes batches** — When all cards in an Active batch reach terminal states (Redeemed, Void, or Expired), the batch automatically transitions to Closed.

## Files to Change

| File | Change | Effort |
|------|--------|--------|
| `memora_voucher_batch.json` | Add `expired_count` field | Small |
| `services/voucher/batch_utils.py` | New shared helper | Medium |
| `api/voucher.py` (`redeem_voucher`) | Replace manual recount with helper | Small |
| `api/voucher.py` (`void_card`) | Replace manual recount with helper | Small |
| `tasks/season_expiration.py` | Add helper call after card expiration | Small |

## Implementation Order

1. **Schema first**: Add `expired_count` to DocType JSON, run `bench migrate`
2. **Helper second**: Create `batch_utils.py` with `recount_and_maybe_close()`
3. **Wire call sites**: Update `redeem_voucher()`, `void_card()`, `expire_season_cards()`
4. **Test**: Run expiration job manually, verify counters and auto-close

## How to Verify

```bash
# 1. After migration, check the field exists
bench --site x.conanacademy.com console
>>> frappe.get_meta("Memora Voucher Batch").has_field("expired_count")
True

# 2. Create a test batch, generate cards, expire them
# (use bench console or the admin panel)

# 3. Check counters
>>> batch = frappe.get_doc("Memora Voucher Batch", "VBATCH-00001")
>>> batch.expired_count, batch.allocated_count, batch.status
(10, 0, 'Closed')  # All cards expired, batch auto-closed
```

## Key Design Decisions

- **Recount, not increment**: All counter updates use `frappe.db.count()` on actual card states. This is idempotent and safe after partial failures.
- **Shared helper**: `recount_and_maybe_close()` centralizes counter logic to prevent drift between call sites.
- **Auto-close only for Active**: Draft and Generated batches are never auto-closed. `void_batch()` is unchanged (explicit closure with reason).
- **No `void_reason` on auto-close**: Distinguishes auto-closed batches from manually voided ones per spec assumption.
