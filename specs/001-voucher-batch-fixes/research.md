# Research: Voucher Batch Counter Fixes & Auto-Close

**Date**: 2026-02-15 | **Status**: Complete

## Research Tasks

### R-1: Where are batch counters currently updated?

**Finding**: Counter updates are scattered across 3 locations, each using the recount pattern (`frappe.db.count()`):

| Location | File:Line | Counter Updated | Trigger |
|----------|-----------|-----------------|---------|
| `void_batch()` | `api/voucher.py:302` | `voided_count` | Bulk void operation |
| `void_card()` | `api/voucher.py:355` | `voided_count` | Individual card void |
| `redeem_voucher()` | `api/voucher.py:686-689` | `redeemed_count` | Card redemption |
| `_update_batch_counters()` | `memora_voucher_allocation.py:149-157` | `allocated_count` | Allocation/return completion |
| `expire_season_cards()` | `tasks/season_expiration.py:49-57` | **NONE** | Season expiration |

**Gap**: `allocated_count` is NOT recounted after redemption (Allocated → Redeemed) or after void_card (Allocated → Void). Only the allocation flow recounts it.

**Decision**: Extract a shared `recount_and_maybe_close()` helper that recounts ALL mutable counters (`allocated_count`, `redeemed_count`, `voided_count`, `expired_count`) in a single call. Each call site invokes this instead of updating individual counters.
**Rationale**: Eliminates the gap where one counter is updated but others become stale. The recount approach is already the established pattern and is idempotent.
**Alternatives considered**: (1) Incremental counter updates — rejected because they fail on partial job failures and require careful bookkeeping. (2) Scheduled periodic recount job — rejected because it introduces lag between card state change and counter visibility.

---

### R-2: How should auto-close be triggered?

**Finding**: Three operations can move a card to a terminal state:
1. `redeem_voucher()` — Allocated → Redeemed
2. `void_card()` — Available/Allocated → Void
3. `expire_season_cards()` — Available/Allocated → Expired

Additionally, `void_batch()` already handles closure explicitly (sets status = Closed).

**Decision**: Add auto-close check to the same `recount_and_maybe_close()` helper. After recounting, if zero cards remain in non-terminal states (`Available` or `Allocated`) AND the batch is `Active`, transition to `Closed`.
**Rationale**: Collocating recount and auto-close ensures they always run together. The check is a single `frappe.db.count()` query (< 2ms for max 1000 cards).
**Alternatives considered**: (1) Frappe `doc_events` hook on `MemoraVoucherCard.on_update` — rejected because the expiration job uses direct SQL (bypasses ORM hooks). (2) Separate scheduler task — rejected because it introduces lag and the check is trivial to inline.

---

### R-3: Race condition safety for auto-close

**Finding**: The spec raises concern about concurrent redemption + void both triggering auto-close. Analysis:

- `redeem_voucher()` uses `SELECT ... FOR UPDATE` on the card row, so two concurrent redemptions on the same card are serialized.
- Two concurrent operations on *different* cards in the same batch could both trigger `recount_and_maybe_close()`. However, since the recount uses `frappe.db.count()` (reads actual state) and the batch status transition is validated by `MemoraVoucherBatch._validate_status_transition()`, the worst case is:
  - Both see 0 non-terminal cards
  - Both attempt `Active → Closed`
  - First one succeeds; second one finds batch already Closed (no-op because we check current status before transitioning)

**Decision**: Use a read-check-then-write pattern: read current batch status, only attempt `Active → Closed` if currently Active. Use `frappe.db.set_value()` (not `doc.save()`) to minimize overhead. The recount is inherently safe — it reads committed state.
**Rationale**: For max 1000 cards per batch and the operations involved (admin actions, daily cron), true race conditions are extremely unlikely. The read-check pattern handles the edge case without introducing locking overhead.
**Alternatives considered**: (1) `SELECT ... FOR UPDATE` on the batch row — rejected as unnecessary overhead for an admin-facing operation with bounded concurrency. (2) Database-level `UPDATE ... WHERE status = 'Active'` with affected-row check — viable but adds complexity without meaningful benefit at this scale.

---

### R-4: Schema change approach for `expired_count`

**Finding**: The batch DocType JSON (`memora_voucher_batch.json`) has `field_order` array and individual field definitions. Current counter fields:
- `generated_count` (Int, read_only, default 0)
- `allocated_count` (Int, read_only, default 0)
- `redeemed_count` (Int, read_only, default 0)
- `voided_count` (Int, read_only, default 0)

**Decision**: Add `expired_count` field with identical properties (Int, read_only, default 0) immediately after `voided_count` in `field_order`. Then run `bench migrate` to apply the schema change.
**Rationale**: Follows the existing pattern exactly. Placing it after `voided_count` groups all counters together in the admin form.
**Alternatives considered**: (1) Virtual field computed on-the-fly — rejected because it wouldn't be visible in list views or reports without custom code. (2) Reusing `voided_count` for both void+expired — rejected because spec explicitly requires separate tracking (FR-001, SC-005).

---

### R-5: Impact on `void_batch()` flow

**Finding**: `void_batch()` at `api/voucher.py:274-326` already:
1. Voids all Available/Allocated cards via SQL
2. Recounts voided cards
3. Sets batch status to Closed explicitly
4. Sets `void_reason`

**Decision**: Leave `void_batch()` unchanged. It already handles its own closure path with `void_reason`. Auto-close is for the gradual case where cards reach terminal states one-by-one. The `void_batch()` flow is a bulk administrative action.
**Rationale**: `void_batch()` is semantically different — it's an explicit admin action with a reason. Auto-close is implicit lifecycle management. They should remain separate code paths as per the spec assumption: "Auto-closed batches are implicitly distinguished from manually voided batches by the absence of a void_reason value."

---

### R-6: What about `allocated_count` accuracy in existing flows?

**Finding**: `allocated_count` is only recounted in `_update_batch_counters()` (allocation.py:149-157), which runs after allocation completion. It is NOT recounted when:
- A card is redeemed (Allocated → Redeemed) — `redeem_voucher()` only updates `redeemed_count`
- A card is voided (Allocated → Void) — `void_card()` only updates `voided_count`
- Cards are expired (Allocated → Expired) — `expire_season_cards()` updates nothing

**Decision**: The shared `recount_and_maybe_close()` helper will recount ALL counters including `allocated_count`, fixing this gap automatically. Every call site that changes a card's status will trigger a full recount.
**Rationale**: Recounting all counters together is marginally more expensive than recounting one (4 COUNT queries vs 1), but ensures consistency. With max 1000 cards per batch, each COUNT is sub-millisecond.
