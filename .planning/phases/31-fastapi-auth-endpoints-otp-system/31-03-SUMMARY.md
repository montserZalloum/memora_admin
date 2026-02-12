---
phase: 31-fastapi-auth-endpoints-otp-system
plan: 03
subsystem: auth
tags: [fastapi, otp, registration, frappe-client, redis-cache, auto-login]

# Dependency graph
requires:
  - phase: 30-frappe-auth-api-bridge
    provides: "register_player Frappe API (creates Player Profile + wallet)"
  - phase: 31-01
    provides: "OTPService with Redis-backed storage, auth Pydantic models (RegisterRequest, RegisterVerifyRequest, etc.)"
  - phase: 31-02
    provides: "auth.py with player/admin login + refresh endpoints"
provides:
  - "POST /auth/player/register - 2-step OTP registration (step 1: send OTP)"
  - "POST /auth/player/register/verify - verify OTP, create account, auto-login"
  - "POST /auth/player/register/resend - resend OTP with cooldown"
  - "GET /auth/registration-options - grades, plans, seasons (avatars/genders client-side)"
  - "check_phone_exists Frappe API for upfront duplicate detection"
  - "get_registration_options Frappe API with grade/major resolution"
affects:
  - "31-04 (password reset endpoints use same auth.py file and OTPService)"

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "Registration options cached in Redis (memora:registration_options, 300s TTL)"
    - "Season auto-populated from latest published season"
    - "Major auto-derived from plan when not provided"
    - "Auto-login after registration (same flow as player_login)"

key-files:
  created: []
  modified:
    - "fastapi_app/api/v1/endpoints/auth.py"
    - "memora_admin/api/auth.py"

key-decisions:
  - "Upfront phone uniqueness check via check_phone_exists Frappe API (better UX than discovering at verify time)"
  - "Registration options fetched via shared _get_registration_options helper (used by both GET endpoint and verify)"
  - "Season not exposed to mobile client; server auto-selects latest published season"
  - "Major auto-derived from plan's major field; fallback to first major of selected grade"
  - "Auto-login reuses exact same flow as player_login (device registration, session, tokens, wallet)"

patterns-established:
  - "Registration flow: register (send OTP) -> verify (create + auto-login)"
  - "Shared _get_registration_options() for cache-first Frappe API calls"
  - "check_phone_exists for lightweight phone enumeration (safe after OTP intent)"

# Metrics
duration: 12min
completed: 2026-02-12
---

# Phase 31 Plan 03: Registration Endpoints Summary

**2-step OTP registration flow with auto-login, upfront phone uniqueness check, registration options endpoint with Redis caching, and auto-population of season and major**

## Performance

- **Duration:** 12 min
- **Started:** 2026-02-12T15:18:28Z
- **Completed:** 2026-02-12T15:30:34Z
- **Tasks:** 1
- **Files modified:** 2

## Accomplishments

- POST /auth/player/register: validates phone uniqueness via Frappe API, creates pending registration with OTP in Redis, returns opaque pending_id
- POST /auth/player/register/verify: verifies OTP, auto-populates season and major, calls register_player, then auto-logs in (device registration, session, tokens, wallet)
- POST /auth/player/register/resend: resends OTP with 60s cooldown enforcement
- GET /auth/registration-options: returns grades (with nested majors + titles), plans, and seasons; cached in Redis for 5 minutes (avatars/genders are hardcoded client-side)
- check_phone_exists and get_registration_options Frappe whitelisted APIs added to memora_admin/api/auth.py

## Task Commits

Each task was committed atomically:

1. **Task 1: Registration endpoints and Frappe registration options API** - `0d0e7ae` (feat)

## Files Created/Modified

- `fastapi_app/api/v1/endpoints/auth.py` - Added 4 registration endpoints (register, verify, resend, registration-options) and _get_registration_options helper
- `memora_admin/api/auth.py` - Added check_phone_exists and get_registration_options Frappe whitelisted APIs

## Decisions Made

- **Upfront phone check via Frappe API:** Better UX to catch duplicates before OTP flow rather than after verification. Falls back gracefully if Frappe is unreachable (register_player catches duplicates at verify time as safety net).
- **Shared _get_registration_options helper:** Both the GET endpoint and the verify endpoint need registration options data (for season/major auto-population). Shared function with Redis cache avoids duplicate Frappe calls.
- **Season auto-selection:** Server picks the latest published season (sorted by season_seq DESC). Mobile client never sees or selects season -- it's an internal concern.
- **Major auto-derivation chain:** First tries plan's major field. If null, falls back to first major of the selected grade. Ensures the DocType's `reqd: 1` constraint is always satisfied.
- **FrappeAPIError graceful handling:** Registration verify catches "Phone already registered" from Frappe (race condition where two users verify OTP simultaneously for same phone) and returns 409.

## Deviations from Plan

None - plan executed exactly as written.

## Issues Encountered

None

## User Setup Required

None - no external service configuration required.

## Next Phase Readiness

- Registration flow complete: REG-01 through REG-06 satisfied
- auth.py now has 7 endpoints (player/login, admin/login, refresh, registration-options, player/register, player/register/verify, player/register/resend)
- Ready for Plan 04 (password reset endpoints) to add remaining OTP-based endpoints

---
*Phase: 31-fastapi-auth-endpoints-otp-system, Plan: 03*
*Completed: 2026-02-12*
