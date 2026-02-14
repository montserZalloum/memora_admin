---
phase: 36-redemption-api
verified: 2026-02-14T13:15:00Z
status: passed
score: 5/5 truths verified
re_verification: false
---

# Phase 36: Redemption API Verification Report

**Phase Goal:** Students can enter a PIN in the app to preview what a voucher unlocks and then redeem it -- content unlocks instantly via the existing subscription pipeline, with full security protections.

**Verified:** 2026-02-14T13:15:00Z
**Status:** PASSED
**Re-verification:** No — initial verification

## Goal Achievement

### Observable Truths

| # | Truth | Status | Evidence |
|---|-------|--------|----------|
| 1 | Student can call POST /api/v1/voucher/preview with a PIN and see available product grants (grants they already own are filtered out) | ✓ VERIFIED | FastAPI endpoint exists at `/api/v1/voucher/preview`, accepts JWT auth + PIN, calls VoucherService.preview() which computes HMAC and delegates to Frappe preview_voucher(). Frappe method filters already-owned grants via get_grant_keys() + ownership check (lines 514-520). Returns {face_value, grants} or {error: CODE}. |
| 2 | Student can call POST /api/v1/voucher/redeem with a PIN and chosen grant -- card is atomically marked Redeemed (SELECT FOR UPDATE), Subscription Transaction is created (payment_method="Voucher", status="Completed"), and content unlocks instantly via Phase 23 hook | ✓ VERIFIED | FastAPI endpoint exists at `/api/v1/voucher/redeem`, accepts JWT + PIN + grant_id. VoucherService.redeem() computes HMAC and delegates to Frappe redeem_voucher(). Frappe method uses SELECT FOR UPDATE (line 566), marks card Redeemed (lines 644-649), creates Subscription Transaction with two-step save (insert Pending Approval lines 659-668, save Completed lines 673-674), triggers _handle_approval(). Voucher bypass in purchase_sync.py prevents admin notification (line 23). |
| 3 | All error codes return machine-readable codes (INVALID_PIN, NOT_ALLOCATED, ALREADY_REDEEMED, EXPIRED, VOID, BATCH_INACTIVE, SEASON_INACTIVE, ALL_GRANTS_OWNED, GRANT_NOT_IN_BATCH, ALREADY_OWNED, RATE_LIMITED) -- ALREADY_OWNED does not consume the card | ✓ VERIFIED | All 10 error codes implemented in Frappe voucher.py (lines 367-378 mapping dict, lines 381-386 status mapping). ALREADY_OWNED check at lines 629-641 returns error WITHOUT marking card Redeemed (card stays Allocated). FastAPI ERROR_STATUS_MAP (lines 24-36) maps codes to HTTP status. RATE_LIMITED handled in endpoint (lines 119-124). Note: Error codes are machine-readable English per user decision, NOT Arabic messages. |
| 4 | Every redemption attempt (success and failure) is logged in Voucher Redemption Log with masked PIN (last 4 digits), and HMAC comparison uses hmac.compare_digest() for timing-attack safety | ✓ VERIFIED | _log_attempt() function (lines 389-426) creates immutable log entries with pin_masked formatted as `****{last4}` (line 417). Called 9 times in redeem_voucher() for all paths (lines 572-683). HMAC timing-safe comparison via hmac_module.compare_digest() in both preview (line 490) and redeem (line 581). |
| 5 | Rate limiting applies only to failed redeem attempts (5/player/hour, 20/IP/hour), rate limit keys auto-expire via Redis TTL with no cleanup job needed | ✓ VERIFIED | VoucherService implements failed-attempt-only rate limiting: check_rate_limit() before operation (lines 89-108), record_failure() after known errors (lines 110-119). FastAPI endpoint checks limit before redeem (lines 119-124), records failure only for FAILURE_ERRORS (lines 130-133). Lua INCREMENT_SCRIPT sets EXPIRE with WINDOW_SECONDS on first increment (lines 30-32). Preview endpoint has NO rate limiting per user decision (line 61 comment). Constants: PLAYER_LIMIT=5, IP_LIMIT=20, WINDOW_SECONDS=3600 (lines 57-59). |

**Score:** 5/5 truths verified

### Required Artifacts

All artifacts from both plan must_haves verified:

| Artifact | Expected | Status | Details |
|----------|----------|--------|---------|
| `memora_admin/memora_admin/api/voucher.py` | preview_voucher and redeem_voucher whitelisted methods | ✓ VERIFIED | Both methods exist with @frappe.whitelist(allow_guest=False). preview_voucher at lines 462-537, redeem_voucher at lines 540-693. Contains all validation chains, SELECT FOR UPDATE, two-step save, timing-safe HMAC, audit logging. |
| `memora_admin/events/purchase_sync.py` | Voucher bypass in admin notification | ✓ VERIFIED | Lines 22-24: early return for payment_method == "Voucher" prevents admin email notification for voucher redemptions. |
| `fastapi_app/services/voucher.py` | VoucherService with HMAC, rate limiting, Frappe delegation | ✓ VERIFIED | Class VoucherService (lines 54-173) with _compute_hmac(), check_rate_limit(), record_failure(), preview(), redeem(). Lua scripts for atomic rate limiting (lines 18-34). FAILURE_ERRORS constant (lines 40-51). |
| `fastapi_app/models/voucher.py` | Pydantic request/response schemas | ✓ VERIFIED | All models present: VoucherPreviewRequest, VoucherRedeemRequest, VoucherGrant, VoucherPreviewResponse, VoucherRedeemResponse, VoucherErrorResponse with retry_after field. |
| `fastapi_app/api/v1/endpoints/voucher.py` | POST /voucher/preview and POST /voucher/redeem endpoints | ✓ VERIFIED | Both endpoints exist with proper JWT auth (CurrentUser), VoucherServiceDep injection, error-to-HTTP-status mapping (lines 24-36), RedisError handling. Preview at lines 52-89, redeem at lines 97-151. |
| `fastapi_app/api/deps.py` | VoucherServiceDep type alias | ✓ VERIFIED | get_voucher_service factory (lines 315-318) injects Redis, FrappeClient, and settings.voucher_hmac_secret. VoucherServiceDep type alias at line 321. |
| `fastapi_app/api/v1/router.py` | Voucher router inclusion | ✓ VERIFIED | Lines 20 (import voucher) and 43 (router.include_router(voucher.router)) wire the voucher endpoints into the API. |
| `fastapi_app/core/config.py` | voucher_hmac_secret setting | ✓ VERIFIED | Lines 49-50: voucher_hmac_secret field added to Settings class with empty string default and sync comment. |

### Key Link Verification

All key links from both plan must_haves verified:

| From | To | Via | Status | Details |
|------|-----|-----|--------|---------|
| memora_admin/memora_admin/api/voucher.py | Memora Subscription Transaction | two-step save (insert Pending Approval, save Completed) | ✓ WIRED | Lines 659-674: trx.insert() with status="Pending Approval", then trx.status="Completed" + trx.save(). Pattern matches plan requirement. |
| memora_admin/memora_admin/api/voucher.py | memora_admin.memora_admin.api.products | get_grant_keys for ownership check | ✓ WIRED | Line 17 imports get_grant_keys, used at lines 514 (preview) and 629 (redeem) for ownership validation. |
| fastapi_app/api/v1/endpoints/voucher.py | fastapi_app/services/voucher.py | VoucherServiceDep dependency injection | ✓ WIRED | Endpoints use VoucherServiceDep parameter (lines 57, 102), imported from deps.py (line 12). Service methods called at lines 69, 127. |
| fastapi_app/services/voucher.py | memora_admin.api.voucher.preview_voucher | FrappeClient.call() | ✓ WIRED | Line 134: await self.frappe.call("memora_admin.memora_admin.api.voucher.preview_voucher", ...) with pin_hmac and player_id. |
| fastapi_app/services/voucher.py | memora_admin.api.voucher.redeem_voucher | FrappeClient.call() | ✓ WIRED | Line 156: await self.frappe.call("memora_admin.memora_admin.api.voucher.redeem_voucher", ...) with pin_hmac, player_id, product_grant_id, ip_address. |
| fastapi_app/api/v1/router.py | fastapi_app/api/v1/endpoints/voucher.py | router.include_router | ✓ WIRED | Line 20 imports voucher, line 43 includes voucher.router. Routes verified: /api/v1/voucher/preview and /api/v1/voucher/redeem both POST. |

### Requirements Coverage

All Phase 36 requirements verified:

| Requirement | Status | Evidence |
|-------------|--------|----------|
| REDEEM-01: POST /api/v1/voucher/preview endpoint | ✓ SATISFIED | Endpoint exists, validates PIN via HMAC, returns available grants filtering owned ones via get_grant_keys() ownership check. |
| REDEEM-02: POST /api/v1/voucher/redeem endpoint | ✓ SATISFIED | Endpoint exists, redeems card for chosen grant, returns success or error with HTTP status mapping. |
| REDEEM-03: preview_voucher(pin_hmac, player_id) whitelisted method | ✓ SATISFIED | Frappe method with full validation chain: HMAC timing-safe comparison, card status, batch status, season check, ownership filtering. |
| REDEEM-04: redeem_voucher() with SELECT FOR UPDATE | ✓ SATISFIED | Frappe method uses SELECT FOR UPDATE at line 566 for atomic card locking during redemption. |
| REDEEM-05: Creates Subscription Transaction triggering Phase 23 hook | ✓ SATISFIED | Two-step save pattern creates transaction with payment_method="Voucher", status changes from Pending Approval to Completed triggering on_update → _handle_approval(). |
| REDEEM-06: Content unlocks instantly via on_subscription_change | ✓ SATISFIED | Subscription Transaction _handle_approval() (Phase 23) creates Player Subscriptions and syncs to Redis memora:access:{player_id} via existing pipeline. |
| REDEEM-07: Error codes return Arabic messages | ⚠️ USER OVERRIDE | Plan specified "Arabic messages" but user decision overrode this — error codes are machine-readable English (INVALID_PIN, etc.) not Arabic. This is intentional and correct per user context. |
| REDEEM-08: ALREADY_OWNED does not consume card | ✓ SATISFIED | Lines 629-641: ownership check returns {"error": "ALREADY_OWNED"} WITHOUT marking card Redeemed (card stays Allocated). |
| REDEEM-09: HMAC uses hmac.compare_digest() | ✓ SATISFIED | Lines 490 and 581 use hmac_module.compare_digest() for timing-attack safe comparison. |
| SEC-01: Rate limiting (5/player/hr, 20/IP/hr) | ✓ SATISFIED | VoucherService implements Lua-based rate limiting with PLAYER_LIMIT=5, IP_LIMIT=20, WINDOW_SECONDS=3600. Applied only to failed redeem attempts. |
| SCHED-03: Rate limit keys auto-expire via TTL | ✓ SATISFIED | INCREMENT_SCRIPT sets EXPIRE on first increment (line 31), Redis TTL auto-expires keys with no cleanup job. |

**Notes:**
- REDEEM-07: Phase goal success criteria mentioned "Arabic messages" but user explicitly decided to use machine-readable English error codes instead. This is intentional and correct.
- Success criterion 1 mentioned rate limiting on preview, but user decided preview should NOT be rate limited (forgiving for young students). Only redeem failures are rate-limited. This is intentional and correct.

### Anti-Patterns Found

**No blocker anti-patterns detected.**

Scanned files:
- memora_admin/memora_admin/api/voucher.py (694 lines)
- memora_admin/events/purchase_sync.py (modified section)
- fastapi_app/services/voucher.py (174 lines)
- fastapi_app/models/voucher.py (45 lines)
- fastapi_app/api/v1/endpoints/voucher.py (152 lines)

Results:
- No TODO/FIXME/HACK comments
- No placeholder/stub implementations
- No console.log-only handlers
- No empty return statements
- All error paths properly handled
- All success paths properly wired

### Verification Checklist

From Plan 01 (Frappe API):
- [x] Both preview_voucher and redeem_voucher are whitelisted Frappe methods
- [x] preview_voucher performs read-only validation and returns grant list or error
- [x] redeem_voucher uses SELECT FOR UPDATE for atomic card locking
- [x] redeem_voucher uses two-step save for Subscription Transaction (insert Pending Approval, save Completed)
- [x] hmac.compare_digest() used for HMAC comparison (not ==)
- [x] _log_attempt() creates Redemption Log entries for both success and failure
- [x] ALREADY_OWNED does not change card status (card stays Allocated)
- [x] purchase_sync.py skips notification for payment_method == "Voucher"
- [x] All error codes match spec: INVALID_PIN, NOT_ALLOCATED, ALREADY_REDEEMED, EXPIRED, VOID, BATCH_INACTIVE, SEASON_INACTIVE, ALL_GRANTS_OWNED, GRANT_NOT_IN_BATCH, ALREADY_OWNED

From Plan 02 (FastAPI layer):
- [x] POST /api/v1/voucher/preview accepts PIN + JWT auth, returns available grants or error code
- [x] POST /api/v1/voucher/redeem accepts PIN + grant_id + JWT auth, returns success or error with HTTP status
- [x] Rate limiting applies only to failed redeem attempts (5/player/hour, 20/IP/hour)
- [x] Preview endpoint is NOT rate limited
- [x] HMAC computed in FastAPI (PIN never sent to Frappe in plaintext)
- [x] Rate limit counters auto-expire via Redis TTL (no cleanup job)
- [x] Error codes map to HTTP status: 404 (INVALID_PIN), 409 (ALREADY_REDEEMED, ALL_GRANTS_OWNED, ALREADY_OWNED), 410 (EXPIRED, VOID), 422 (NOT_ALLOCATED, BATCH_INACTIVE, SEASON_INACTIVE, GRANT_NOT_IN_BATCH), 429 (RATE_LIMITED)
- [x] RATE_LIMITED response includes retry_after seconds
- [x] VoucherService injected with Redis, FrappeClient, and HMAC secret from config
- [x] Voucher router wired into /api/v1/

### Commits Verified

All task commits from both plans exist and are reachable:

- `6555ebd` - feat(36-01): add preview_voucher and redeem_voucher whitelisted methods
- `b6777d1` - feat(36-01): skip admin notification for Voucher payment method
- `775ea86` - feat(36-02): add voucher models, service, and HMAC config
- `0cd9dfb` - feat(36-02): add voucher endpoints and wire into router

All commits verified via git log.

### Human Verification Required

**No human verification needed** — all success criteria are programmatically verifiable and have been verified.

The following were considered but are NOT needed:
- API endpoint accessibility: Routes verified via fastapi_app.main route inspection
- Rate limiting behavior: Lua scripts and Redis TTL pattern verified in code
- Error response format: Pydantic models and JSONResponse usage verified
- Content unlock chain: Two-step save pattern + existing Phase 23 pipeline verified

## Summary

**PHASE 36 GOAL ACHIEVED**

All 5 observable truths verified. All required artifacts exist, are substantive, and properly wired. All 11 requirements satisfied (1 with intentional user override). All key links functional. No anti-patterns detected. No gaps found.

Students can now:
1. Preview a voucher by entering a PIN in the app (sees available grants, already-owned filtered out)
2. Redeem the voucher by choosing a grant (card atomically marked Redeemed via SELECT FOR UPDATE)
3. Content unlocks instantly via existing Phase 23 subscription pipeline
4. All security protections active: HMAC timing-safe comparison, failed-attempt rate limiting (5/player/hr, 20/IP/hr), immutable audit logging, Voucher bypass for admin notifications

Ready for Phase 37 (Admin Panel / Invoice integration).

---

_Verified: 2026-02-14T13:15:00Z_
_Verifier: Claude (gsd-verifier)_
