---
phase: 31-fastapi-auth-endpoints-otp-system
plan: 04
subsystem: auth
tags: [fastapi, otp, password-reset, anti-enumeration, owasp, redis]

# Dependency graph
requires:
  - phase: 30-frappe-auth-api-bridge
    provides: "set_player_password Frappe API (hash update + session invalidation)"
  - phase: 31-01
    provides: "OTPService with password reset methods, auth Pydantic models (PasswordReset*)"
  - phase: 31-03
    provides: "check_phone_exists Frappe API, auth.py with login + registration endpoints"
provides:
  - "POST /auth/player/password-reset/request - anti-enumeration OTP request"
  - "POST /auth/player/password-reset/verify - OTP verification, returns single-use temp token"
  - "POST /auth/player/password-reset/confirm - sets new password, invalidates all sessions"
  - "check_phone_exists enhanced to return player_name for mobile-to-docname resolution"
  - "OTPService.create_password_reset with phone_exists param for anti-enumeration timing"
affects:
  - "Phase 32 (all auth endpoints complete, ready for production)"

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "Anti-enumeration: same response + timing regardless of phone existence"
    - "3-step password reset: request OTP -> verify -> temp token -> confirm"
    - "phone_exists param controls OTP storage while keeping rate limit timing consistent"

key-files:
  created: []
  modified:
    - "fastapi_app/api/v1/endpoints/auth.py"
    - "fastapi_app/services/otp.py"
    - "memora_admin/api/auth.py"

key-decisions:
  - "Anti-enumeration via consistent timing: rate limit + cooldown ALWAYS execute, OTP storage conditional on phone_exists"
  - "check_phone_exists returns player_name to avoid separate lookup in confirm endpoint"
  - "Password reset request endpoint catches all Frappe errors as 'not found' for anti-enumeration"

patterns-established:
  - "Anti-enumeration pattern: phone_exists bool controls OTP storage, not rate limit/cooldown behavior"
  - "Mobile-to-docname resolution via check_phone_exists (reused across registration and password reset)"

# Metrics
duration: 4min
completed: 2026-02-12
---

# Phase 31 Plan 04: Password Reset Endpoints Summary

**3-step OWASP-compliant password reset with anti-enumeration design: request OTP, verify for temp token, confirm with session invalidation**

## Performance

- **Duration:** 4 min
- **Started:** 2026-02-12T15:33:17Z
- **Completed:** 2026-02-12T15:37:20Z
- **Tasks:** 1
- **Files modified:** 3

## Accomplishments

- POST /auth/player/password-reset/request: always returns generic "If this number is registered..." message regardless of phone existence (anti-enumeration)
- POST /auth/player/password-reset/verify: verifies OTP, returns cryptographically random single-use temp token (15-min TTL)
- POST /auth/player/password-reset/confirm: validates temp token (single-use, deleted on use), resolves mobile to player docname, calls set_player_password for hash update + session invalidation
- OTPService.create_password_reset enhanced with `phone_exists` keyword arg -- rate limits and cooldown always run for timing consistency
- check_phone_exists now returns `player_name` alongside `exists` for mobile-to-docname resolution

## Task Commits

Each task was committed atomically:

1. **Task 1: Password reset endpoints with anti-enumeration design** - `b9a8e1c` (feat)

## Files Created/Modified

- `fastapi_app/api/v1/endpoints/auth.py` - Added 3 password reset endpoints (request, verify, confirm) with imports for password reset models
- `fastapi_app/services/otp.py` - Added `phone_exists` keyword param to `create_password_reset` for anti-enumeration timing consistency
- `memora_admin/api/auth.py` - Enhanced `check_phone_exists` to also return `player_name` for mobile-to-docname resolution

## Decisions Made

- **Anti-enumeration via consistent timing:** Rate limit checks + cooldown + cooldown set all execute regardless of phone existence. Only OTP storage and SMS sending are conditional on `phone_exists=True`. This prevents timing-based enumeration.
- **Frappe errors treated as "not found":** In the request endpoint, if the Frappe call to check_phone_exists fails, it silently treats the phone as non-existent rather than returning an error (which could leak information).
- **check_phone_exists returns player_name:** Avoids a second Frappe call in the confirm endpoint to resolve mobile to docname. The confirm endpoint needs the docname to call set_player_password.

## Deviations from Plan

None - plan executed exactly as written.

## Issues Encountered

None

## User Setup Required

None - no external service configuration required.

## Next Phase Readiness

- All 10 auth endpoints complete:
  - POST /auth/player/login
  - POST /auth/admin/login
  - POST /auth/refresh
  - GET /auth/registration-options
  - POST /auth/player/register
  - POST /auth/player/register/verify
  - POST /auth/player/register/resend
  - POST /auth/player/password-reset/request
  - POST /auth/player/password-reset/verify
  - POST /auth/player/password-reset/confirm
- RESET-01 through RESET-05 all satisfied
- Phase 31 (FastAPI Auth Endpoints + OTP System) is now complete

---
*Phase: 31-fastapi-auth-endpoints-otp-system, Plan: 04*
*Completed: 2026-02-12*
