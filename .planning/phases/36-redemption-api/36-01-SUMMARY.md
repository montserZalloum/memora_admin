---
phase: 36-redemption-api
plan: 01
subsystem: api
tags: [frappe, hmac, voucher, redemption, select-for-update, subscription-transaction]

requires:
  - phase: 33-voucher-doctype
    provides: Voucher Card, Voucher Batch, Voucher Batch Grant, Voucher Redemption Log DocTypes
  - phase: 34-card-generation
    provides: HMAC-SHA256 PIN storage, compute_hmac, card generation pipeline
  - phase: 35-allocation-distribution
    provides: Card allocation with Allocated status, library assignment
  - phase: 23-purchase-pipeline
    provides: MemoraSubscriptionTransaction._handle_approval() for access grant creation

provides:
  - preview_voucher() whitelisted method for read-only PIN validation and grant listing
  - redeem_voucher() whitelisted method with atomic card redemption via SELECT FOR UPDATE
  - _log_attempt() audit logging for every redemption attempt (success and failure)
  - _check_season_active() season validation helper via player plan
  - Voucher bypass in purchase_sync to suppress admin notifications for auto-approved voucher redemptions

affects: [36-02-PLAN, fastapi-voucher-endpoints]

tech-stack:
  added: []
  patterns:
    - "Return-based error codes from Frappe whitelisted methods (not frappe.throw)"
    - "Two-step save for Subscription Transaction (insert Pending Approval, save Completed)"
    - "SELECT FOR UPDATE for atomic card state transitions"
    - "HMAC timing-safe comparison via hmac.compare_digest()"
    - "Error code to log status mapping via module-level dicts"

key-files:
  created: []
  modified:
    - memora_admin/memora_admin/api/voucher.py
    - memora_admin/events/purchase_sync.py

key-decisions:
  - "Redemption Log status uses DocType Select options (Success, Invalid PIN, etc.) not generic Success/Failed"
  - "Season validation uses player plan -> season chain (batch has no season field)"
  - "Module-level dicts for error code mapping to avoid inline repetition"

patterns-established:
  - "Return-based errors for Frappe methods called by FastAPI proxy: return {error: CODE} instead of frappe.throw()"
  - "_check_season_active() reusable season validation via player's plan chain"

duration: 3min
completed: 2026-02-14
---

# Phase 36 Plan 01: Frappe Voucher Redemption API Summary

**Frappe whitelisted preview_voucher() and redeem_voucher() with SELECT FOR UPDATE locking, HMAC timing-safe comparison, two-step Subscription Transaction save, and immutable audit logging**

## Performance

- **Duration:** 3 min
- **Started:** 2026-02-14T12:43:58Z
- **Completed:** 2026-02-14T12:47:09Z
- **Tasks:** 2
- **Files modified:** 2

## Accomplishments

- preview_voucher() performs full validation chain (PIN lookup, card status, batch status, season, ownership) and returns available grants with face value
- redeem_voucher() atomically locks card via SELECT FOR UPDATE, creates Subscription Transaction with two-step save triggering Phase 23 pipeline (_handle_approval -> Player Subscriptions + Redis SADD)
- All 10 error codes implemented: INVALID_PIN, NOT_ALLOCATED, ALREADY_REDEEMED, EXPIRED, VOID, BATCH_INACTIVE, SEASON_INACTIVE, ALL_GRANTS_OWNED, GRANT_NOT_IN_BATCH, ALREADY_OWNED
- ALREADY_OWNED preserves card (stays Allocated) per user decision
- Every attempt logged in Voucher Redemption Log with masked PIN (****XXXX format)
- Voucher-initiated Subscription Transactions skip admin email notification

## Task Commits

Each task was committed atomically:

1. **Task 1: Add preview_voucher and redeem_voucher to Frappe voucher API** - `6555ebd` (feat)
2. **Task 2: Add Voucher bypass in purchase_sync notification** - `b6777d1` (feat)

## Files Created/Modified

- `memora_admin/memora_admin/api/voucher.py` - Added preview_voucher(), redeem_voucher(), _log_attempt(), _check_season_active(), and error code mapping dicts (+341 lines)
- `memora_admin/events/purchase_sync.py` - Added early return for payment_method == "Voucher" to skip admin notification (+4 lines)

## Decisions Made

1. **Redemption Log status uses DocType Select options** - The Voucher Redemption Log DocType has a Select field with specific options (Success, Invalid PIN, Already Redeemed, etc.) rather than generic Success/Failed. The implementation maps error codes to these values via `_ERROR_TO_LOG_STATUS` dict to match the schema.

2. **Season validation via player plan chain** - The Voucher Batch DocType has no `season` field. Season validation uses the player's plan -> plan's season -> season's is_published/end_date chain, consistent with `_get_expires_at()` in MemoraSubscriptionTransaction. If no plan or season is configured, validation passes (permissive default).

3. **Module-level mapping dicts** - `_CARD_STATUS_ERRORS` and `_ERROR_TO_LOG_STATUS` defined at module level to avoid inline repetition and ensure consistency between preview and redeem validation paths.

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] Used DocType Select options instead of generic Success/Failed for log status**
- **Found during:** Task 1 (preview_voucher and redeem_voucher implementation)
- **Issue:** Plan specified `status="Success"` or `status="Failed"` for _log_attempt, but the Voucher Redemption Log DocType has a Select field with specific options: "Success", "Invalid PIN", "Already Redeemed", "Expired", "Void", "Batch Inactive", "Season Inactive", "All Grants Owned", "Grant Not In Batch", "Already Owned", "Rate Limited", "Not Allocated", "Error". Using "Failed" would cause a Frappe validation error.
- **Fix:** Created `_ERROR_TO_LOG_STATUS` mapping dict to convert error codes to the correct Select option values. Each _log_attempt call uses the specific log status (e.g., "Invalid PIN" instead of "Failed").
- **Files modified:** `memora_admin/memora_admin/api/voucher.py`
- **Verification:** All status values match DocType Select options
- **Committed in:** 6555ebd (Task 1 commit)

**2. [Rule 1 - Bug] Season validation via player plan chain instead of batch season field**
- **Found during:** Task 1 (preview_voucher and redeem_voucher implementation)
- **Issue:** Plan mentioned "Get the batch's season link" but Voucher Batch DocType has no `season` field. Reading a nonexistent field would silently return None, effectively skipping season validation entirely.
- **Fix:** Implemented `_check_season_active()` helper that validates via player_id -> Memora Player Profile.plan -> Memora Academic Plan.season -> Memora Season (is_published, end_date). This is the same chain used by `_get_expires_at()` in MemoraSubscriptionTransaction. Returns True (permissive) when no plan or season is configured.
- **Files modified:** `memora_admin/memora_admin/api/voucher.py`
- **Verification:** Confirmed against Voucher Batch JSON schema (no season field) and MemoraSubscriptionTransaction._get_expires_at() pattern
- **Committed in:** 6555ebd (Task 1 commit)

---

**Total deviations:** 2 auto-fixed (2 bug fixes)
**Impact on plan:** Both auto-fixes necessary for correctness. The log status fix prevents Frappe validation errors. The season validation fix ensures the check actually works against the real data model. No scope creep.

## Issues Encountered

None

## User Setup Required

None - no external service configuration required.

## Next Phase Readiness

- Frappe whitelisted methods ready for FastAPI proxy layer (Plan 02)
- FastAPI VoucherService can call `preview_voucher(pin_hmac, player_id)` and `redeem_voucher(pin_hmac, player_id, product_grant_id, ip_address)` via FrappeClient
- Error dict responses map cleanly to HTTP status codes
- STATE.md blocker about _handle_approval() commit behavior is addressed: two-step save confirmed working (insert Pending Approval, save Completed triggers on_update)

## Self-Check: PASSED

- [x] `memora_admin/memora_admin/api/voucher.py` exists
- [x] `memora_admin/events/purchase_sync.py` exists
- [x] `.planning/phases/36-redemption-api/36-01-SUMMARY.md` exists
- [x] Commit `6555ebd` (Task 1) found
- [x] Commit `b6777d1` (Task 2) found

---
*Phase: 36-redemption-api*
*Completed: 2026-02-14*
