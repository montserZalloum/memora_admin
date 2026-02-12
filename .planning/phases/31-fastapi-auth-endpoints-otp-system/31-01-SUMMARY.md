---
phase: 31-fastapi-auth-endpoints-otp-system
plan: 01
subsystem: auth
tags: [jwt, otp, redis, pydantic, sms, rate-limiting]

# Dependency graph
requires:
  - phase: 30-frappe-auth-api-bridge
    provides: "FrappeAuthService, check_password, player_profile Frappe APIs"
provides:
  - "Updated create_access_token with optional email + mobile param"
  - "jwt_access_token_expire_minutes default 60 min"
  - "All auth Pydantic models (player login, admin login, register, password reset)"
  - "OTPService with Redis-backed storage and pluggable OTPProvider"
  - "session_timeout_days plumbing (Frappe Settings -> FastAPI model)"
affects:
  - "31-02 (player login endpoint uses PlayerLoginRequest, create_access_token with mobile)"
  - "31-03 (registration endpoint uses RegisterRequest, OTPService)"
  - "31-04 (password reset endpoint uses PasswordResetRequest, OTPService)"

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "OTPProvider protocol for pluggable SMS delivery"
    - "StaticOTPProvider dev stub (always '1111')"
    - "Redis SETNX for atomic phone reservation"
    - "Lua script atomic rate limiting for OTP requests"
    - "Single-use reset tokens via Redis GET+DELETE"

key-files:
  created:
    - "fastapi_app/services/otp.py"
  modified:
    - "fastapi_app/core/security.py"
    - "fastapi_app/core/config.py"
    - "fastapi_app/models/auth.py"
    - "fastapi_app/models/settings.py"
    - "fastapi_app/models/__init__.py"
    - "memora_admin/api/settings.py"

key-decisions:
  - "email and mobile keyword-only params with * separator to prevent positional arg confusion"
  - "LoginProfile drops gender field (per CONTEXT.md mobile-first simplification)"
  - "EnrichedTokenResponse renamed to PlayerLoginResponse for clarity"
  - "LoginRequest removed -- replaced by PlayerLoginRequest and AdminLoginRequest"
  - "OTP always '1111' via StaticOTPProvider -- real SMS provider swapped in later"

patterns-established:
  - "OTPProvider Protocol: runtime_checkable protocol for SMS delivery abstraction"
  - "Pending registration pattern: phone_reserved SETNX + pending:{id} JSON + OTP_TTL"
  - "3-step password reset: send OTP -> verify -> temp token -> confirm"
  - "OTP rate limiting: 3/phone/10min + 10/IP/10min via atomic Lua INCR+EXPIRE"

# Metrics
duration: 3min
completed: 2026-02-12
---

# Phase 31 Plan 01: Core Auth Infrastructure Summary

**Updated JWT creation (mobile claim, keyword-only email), 12 auth Pydantic models, and OTPService with Redis-backed storage, rate limiting, and pluggable provider protocol**

## Performance

- **Duration:** 3 min
- **Started:** 2026-02-12T12:54:17Z
- **Completed:** 2026-02-12T12:57:19Z
- **Tasks:** 2
- **Files modified:** 7

## Accomplishments

- create_access_token updated: email optional (keyword-only), mobile param added, JWT payload only includes truthy identity claims
- All 12 auth Pydantic models created for player login, admin login, registration (3-step), and password reset (3-step)
- OTPService (409 lines) with full lifecycle: pending registration, verify, resend, password reset, reset token validation
- Rate limiting (3/phone/10min, 10/IP/10min), 60s cooldown, 3 max attempts, 5-min OTP TTL, 15-min reset token TTL

## Task Commits

Each task was committed atomically:

1. **Task 1: Update core security, config, and models** - `84e1714` (feat)
2. **Task 2: Create OTP service with pluggable provider** - `971fd6c` (feat)

## Files Created/Modified

- `fastapi_app/core/security.py` - create_access_token: email optional keyword, mobile param, conditional payload claims
- `fastapi_app/core/config.py` - jwt_access_token_expire_minutes default 15 -> 60
- `fastapi_app/models/auth.py` - 12 models: PlayerLoginRequest, AdminLoginRequest, RegisterRequest, RegisterVerifyRequest, RegisterResendRequest, RegisterResponse, PlayerLoginResponse, LoginProfile, PasswordResetRequest, PasswordResetVerifyRequest, PasswordResetConfirmRequest, PasswordResetVerifyResponse
- `fastapi_app/models/settings.py` - GamificationSettings: added session_timeout_days
- `fastapi_app/models/__init__.py` - Updated exports for new models
- `memora_admin/api/settings.py` - Returns session_timeout_days from Frappe Settings
- `fastapi_app/services/otp.py` - OTPProvider protocol, StaticOTPProvider, OTPService (9 methods, 409 lines)

## Decisions Made

- **Keyword-only params via `*` separator:** email and mobile are keyword-only to prevent positional argument confusion since existing callers pass email as 2nd positional arg (Plan 31-02 will rewrite those callers)
- **LoginProfile drops gender:** Per CONTEXT.md mobile-first simplification
- **Rename EnrichedTokenResponse -> PlayerLoginResponse:** Clearer naming for player-specific login response
- **Remove LoginRequest:** Replaced by separate PlayerLoginRequest (mobile+password) and AdminLoginRequest (email+password)
- **Static OTP "1111":** Dev-mode provider always returns "1111" -- real SMS provider swapped via OTPProvider protocol later

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 3 - Blocking] Updated models/__init__.py exports**
- **Found during:** Task 1
- **Issue:** models/__init__.py imported LoginRequest which was removed and didn't export new models
- **Fix:** Updated imports and __all__ to reflect new model set
- **Files modified:** fastapi_app/models/__init__.py
- **Committed in:** 84e1714 (Task 1 commit)

---

**Total deviations:** 1 auto-fixed (1 blocking)
**Impact on plan:** Essential fix -- without updating __init__.py, model imports from fastapi_app.models would fail.

## Issues Encountered

None

## User Setup Required

None - no external service configuration required.

## Next Phase Readiness

- All auth models ready for Plans 02-04 endpoint implementation
- create_access_token signature ready for player tokens (mobile claim) and admin tokens (email claim)
- OTPService ready for injection into registration (Plan 03) and password reset (Plan 04) endpoints
- Note: existing auth.py callers (LoginRequest, EnrichedTokenResponse, positional email) will break -- Plan 31-02 rewrites auth.py entirely

---
*Phase: 31-fastapi-auth-endpoints-otp-system, Plan: 01*
*Completed: 2026-02-12*
