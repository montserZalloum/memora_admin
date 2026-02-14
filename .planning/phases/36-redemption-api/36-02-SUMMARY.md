---
phase: 36-redemption-api
plan: 02
subsystem: api
tags: [fastapi, hmac, voucher, rate-limiting, pydantic, lua-scripts, redis]

requires:
  - phase: 36-01
    provides: preview_voucher() and redeem_voucher() Frappe whitelisted methods with SELECT FOR UPDATE locking

provides:
  - POST /api/v1/voucher/preview endpoint with JWT auth and no rate limiting
  - POST /api/v1/voucher/redeem endpoint with JWT auth and failed-attempt rate limiting
  - VoucherService with HMAC-SHA256 computation (PIN never reaches Frappe in plaintext)
  - Failed-attempt-only rate limiting via Redis Lua scripts (5/player/hr, 20/IP/hr)
  - Pydantic v2 request/response models for voucher API
  - VoucherServiceDep dependency injection in deps.py
  - voucher_hmac_secret config setting

affects: [37-admin-panel, mobile-app-integration]

tech-stack:
  added: []
  patterns:
    - "Failed-attempt-only rate limiting via Lua scripts (check limit before, record failure after)"
    - "HMAC computation at API gateway layer (FastAPI) before proxying to Frappe"
    - "Error-code-to-HTTP-status mapping dict for machine-readable API responses"
    - "Service factory with settings injection (VoucherServiceDep needs SettingsDep for HMAC secret)"

key-files:
  created:
    - fastapi_app/models/voucher.py
    - fastapi_app/services/voucher.py
    - fastapi_app/api/v1/endpoints/voucher.py
  modified:
    - fastapi_app/api/deps.py
    - fastapi_app/api/v1/router.py
    - fastapi_app/core/config.py
    - .env.example

key-decisions:
  - "ERROR_STATUS_MAP: 404 for INVALID_PIN, 409 for conflict states, 410 for gone states, 422 for validation, 429 for rate limit"
  - "SERVICE_ERROR returned when FrappeAPIError caught (generic error for non-200 Frappe responses)"
  - "VoucherService factory takes SettingsDep unlike other services (needs HMAC secret from config)"
  - "Lua script results cast to int() defensively for redis.asyncio compatibility"

patterns-established:
  - "Failed-attempt-only rate limiting: check_rate_limit() before operation, record_failure() after known failure error codes"
  - "HMAC at gateway: PIN -> HMAC in FastAPI service, only pin_hmac crosses to Frappe"
  - "Error code mapping as module-level dict shared between endpoint and service"

duration: 4min
completed: 2026-02-14
---

# Phase 36 Plan 02: FastAPI Voucher Endpoints Summary

**POST /voucher/preview and /voucher/redeem endpoints with HMAC-SHA256 at gateway, failed-attempt Lua rate limiting, and Frappe delegation via VoucherServiceDep**

## Performance

- **Duration:** 4 min
- **Started:** 2026-02-14T12:52:04Z
- **Completed:** 2026-02-14T12:56:09Z
- **Tasks:** 2
- **Files modified:** 7

## Accomplishments

- POST /api/v1/voucher/preview accepts JWT + PIN, computes HMAC in FastAPI, delegates to Frappe preview_voucher(), returns available grants or machine-readable error code (no rate limiting per user decision)
- POST /api/v1/voucher/redeem accepts JWT + PIN + grant_id, checks rate limit BEFORE operation, delegates to Frappe redeem_voucher(), records failure AFTER known error codes, returns success or error with correct HTTP status
- VoucherService computes HMAC-SHA256 so plaintext PIN never crosses the FastAPI-to-Frappe boundary
- Failed-attempt-only rate limiting via two Lua scripts (CHECK_LIMIT_SCRIPT and INCREMENT_SCRIPT) with Redis TTL auto-expiry (no cleanup job)
- All 10 error codes plus RATE_LIMITED mapped to appropriate HTTP status codes (404, 409, 410, 422, 429)
- RATE_LIMITED response includes retry_after seconds for client countdown display

## Task Commits

Each task was committed atomically:

1. **Task 1: Create Pydantic models and VoucherService** - `775ea86` (feat)
2. **Task 2: Create voucher endpoints and wire into router** - `0cd9dfb` (feat)

## Files Created/Modified

- `fastapi_app/models/voucher.py` - Pydantic v2 request/response models: VoucherPreviewRequest, VoucherRedeemRequest, VoucherPreviewResponse, VoucherRedeemResponse, VoucherErrorResponse, VoucherGrant
- `fastapi_app/services/voucher.py` - VoucherService with _compute_hmac(), check_rate_limit(), record_failure(), preview(), redeem() + FAILURE_ERRORS constant + Lua scripts
- `fastapi_app/api/v1/endpoints/voucher.py` - POST /voucher/preview and POST /voucher/redeem with error-code-to-HTTP-status mapping, _get_client_ip helper, try/except for RedisError and generic exceptions
- `fastapi_app/api/deps.py` - Added VoucherService import, get_voucher_service factory (with SettingsDep for HMAC secret), VoucherServiceDep type alias
- `fastapi_app/api/v1/router.py` - Added voucher import and router.include_router(voucher.router)
- `fastapi_app/core/config.py` - Added voucher_hmac_secret: str = "" setting
- `.env.example` - Added VOUCHER_HMAC_SECRET placeholder with sync note

## Decisions Made

1. **Error code to HTTP status mapping** - Used Claude's discretion per CONTEXT.md: 404 for INVALID_PIN (not found), 409 for conflict states (ALREADY_REDEEMED, ALL_GRANTS_OWNED, ALREADY_OWNED), 410 for gone states (EXPIRED, VOID), 422 for validation errors (NOT_ALLOCATED, BATCH_INACTIVE, SEASON_INACTIVE, GRANT_NOT_IN_BATCH), 429 for RATE_LIMITED.

2. **SERVICE_ERROR for Frappe failures** - When FrappeAPIError is caught (non-200 response from Frappe), the service returns `{"error": "SERVICE_ERROR"}` rather than propagating the specific Frappe error. This prevents leaking internal details to the client while the endpoint logs the full error via structlog.

3. **VoucherService factory with SettingsDep** - Unlike other services that only need Redis + FrappeClient, VoucherService also needs the HMAC secret from config. The get_voucher_service factory accepts SettingsDep to pass settings.voucher_hmac_secret to the constructor.

4. **Defensive int() cast on Lua script results** - Redis Lua scripts may return bytes or int depending on redis.asyncio version. Added `int(retry)` cast in check_rate_limit to prevent comparison issues.

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] Corrected Frappe API method path (double memora_admin)**
- **Found during:** Task 1 (VoucherService implementation)
- **Issue:** Plan specified `memora_admin.api.voucher.preview_voucher` as the Frappe method path, but the actual path is `memora_admin.memora_admin.api.voucher.preview_voucher` (the module is nested under the app namespace)
- **Fix:** Used the correct double-nested path in both preview() and redeem() calls
- **Files modified:** `fastapi_app/services/voucher.py`
- **Verification:** Confirmed by reading the actual voucher.py file location
- **Committed in:** 775ea86 (Task 1 commit)

**2. [Rule 2 - Missing Critical] Added FrappeAPIError handling in service methods**
- **Found during:** Task 1 (VoucherService implementation)
- **Issue:** Plan described Frappe delegation but didn't specify error handling for FrappeAPIError (non-200 HTTP responses from Frappe). Without handling, unhandled exceptions would propagate as 500 errors with raw traceback
- **Fix:** Added try/except FrappeAPIError in both preview() and redeem() methods, returning {"error": "SERVICE_ERROR"} and logging full error details via structlog
- **Files modified:** `fastapi_app/services/voucher.py`
- **Verification:** Service gracefully returns error dict on Frappe failure
- **Committed in:** 775ea86 (Task 1 commit)

---

**Total deviations:** 2 auto-fixed (1 bug fix, 1 missing critical)
**Impact on plan:** Both auto-fixes necessary for correctness. The API path fix prevents 404 errors on every call. The error handling prevents raw exception leaks. No scope creep.

## Issues Encountered

None

## User Setup Required

**Environment variable required:** Add `VOUCHER_HMAC_SECRET` to `.env` with the same value as `voucher_hmac_secret` in Frappe's `site_config.json`. Without this, HMAC computation will use an empty string and all PIN lookups will fail.

## Next Phase Readiness

- Phase 36 (Redemption API) is fully complete: Frappe transactional methods (Plan 01) + FastAPI proxy endpoints (Plan 02)
- Student mobile app can call POST /api/v1/voucher/preview and POST /api/v1/voucher/redeem with JWT auth
- Rate limiting protects against brute-force PIN guessing (5 failed/player/hr, 20 failed/IP/hr)
- Ready for Phase 37 (Admin Panel / Invoice integration)

## Self-Check: PASSED

- [x] `fastapi_app/models/voucher.py` exists
- [x] `fastapi_app/services/voucher.py` exists
- [x] `fastapi_app/api/v1/endpoints/voucher.py` exists
- [x] `.planning/phases/36-redemption-api/36-02-SUMMARY.md` exists
- [x] Commit `775ea86` (Task 1) found
- [x] Commit `0cd9dfb` (Task 2) found

---
*Phase: 36-redemption-api*
*Completed: 2026-02-14*
