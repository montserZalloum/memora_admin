---
phase: 31-fastapi-auth-endpoints-otp-system
plan: 02
subsystem: auth
tags: [jwt, fastapi, frappe-client, player-login, admin-login, token-refresh, device-registration]

# Dependency graph
requires:
  - phase: 30-frappe-auth-api-bridge
    provides: "verify_player_password Frappe API (phone+password -> profile)"
  - phase: 31-01
    provides: "Updated create_access_token (keyword-only email/mobile), auth Pydantic models, OTPService"
provides:
  - "POST /auth/player/login - phone+password login via FrappeClient (no Frappe session)"
  - "POST /auth/admin/login - email+password login via FrappeAuthService"
  - "POST /auth/refresh - handles both player and admin tokens"
  - "Old /auth/login endpoint removed (MIGR-07)"
affects:
  - "31-03 (registration endpoints use same auth.py file)"
  - "31-04 (password reset endpoints use same auth.py file)"

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "Player login via FrappeClient.call(verify_player_password) - single API call, no Frappe session"
    - "Admin login retains FrappeAuthService for Frappe User auth"
    - "Refresh endpoint passes email/mobile from payload for transparent player/admin support"
    - "session_timeout_days from Memora Settings drives player refresh token TTL"

key-files:
  created: []
  modified:
    - "fastapi_app/api/v1/endpoints/auth.py"

key-decisions:
  - "Player login uses FrappeClient.call instead of FrappeAuthService (single HTTP call vs 4)"
  - "Admin login uses jwt_refresh_token_expire_days from .env; player login uses session_timeout_days from Memora Settings"
  - "Admin tokens include role='System Manager' claim for authorization checks"
  - "Refresh endpoint passes role from payload to preserve admin role across refreshes"

patterns-established:
  - "Player auth: FrappeClient.call(verify_player_password) returns player_id as JWT sub"
  - "Admin auth: FrappeAuthService.verify_credentials() with email as JWT sub + role claim"
  - "Token refresh: email/mobile/role passed from refresh token payload to new access token"

# Metrics
duration: 3min
completed: 2026-02-12
---

# Phase 31 Plan 02: Player Login + Admin Login Endpoints Summary

**Rewrote auth.py with separated player/admin login endpoints and transparent token refresh, eliminating Frappe sessions for player auth (AUTH-01 through AUTH-05, MIGR-07)**

## Performance

- **Duration:** 3 min
- **Started:** 2026-02-12T12:59:37Z
- **Completed:** 2026-02-12T13:02:12Z
- **Tasks:** 1
- **Files modified:** 1

## Accomplishments

- POST /auth/player/login: verifies phone+password via FrappeClient.call(verify_player_password), returns tokens + LoginProfile (display_name, avatar, xp) -- no Frappe session created
- POST /auth/admin/login: verifies email+password via FrappeAuthService, returns TokenResponse (tokens only), includes role claim
- POST /auth/refresh: handles both player and admin tokens by passing email/mobile/role from payload
- Old /auth/login endpoint removed (MIGR-07) -- returns 404
- Player refresh token TTL driven by session_timeout_days from Memora Settings (not hardcoded .env value)

## Task Commits

Each task was committed atomically:

1. **Task 1: Player login and admin login endpoints** - `5ace1f1` (feat)

## Files Created/Modified

- `fastapi_app/api/v1/endpoints/auth.py` - Complete rewrite: 3 endpoints (player/login, admin/login, refresh), removed old /login endpoint, removed EnrichedTokenResponse/LoginRequest/is_email imports

## Decisions Made

- **Player login uses FrappeClient.call:** Single API call to verify_player_password replaces the 4-call FrappeAuthService flow (login, get_logged_user, get_profile, logout). Player identity is PLAYER-##### docname (not email).
- **Admin retains FrappeAuthService:** Admin login unchanged -- uses Frappe User email+password auth with session creation/teardown. Only players migrate to the new flow.
- **session_timeout_days for player TTL:** Player refresh token and session TTL use session_timeout_days from SettingsService (admin-configurable in Memora Settings DocType). Admin login uses jwt_refresh_token_expire_days from .env.
- **role claim in admin tokens:** Admin access tokens include `role="System Manager"` for authorization. Refresh endpoint preserves this across token refreshes.
- **Wallet XP from WalletService:** Player login fetches XP from WalletService (with FrappeClient for hydration) rather than relying solely on verify_player_password's XP value, ensuring consistency with the Redis wallet state.

## Deviations from Plan

None - plan executed exactly as written.

## Issues Encountered

None

## User Setup Required

None - no external service configuration required.

## Next Phase Readiness

- auth.py has player/login and admin/login working -- ready for Plan 03 (registration endpoints) and Plan 04 (password reset endpoints) to add more endpoints to this same file
- OTPService from Plan 01 ready for injection into registration and password reset flows
- All auth Pydantic models from Plan 01 available for Plans 03-04

---
*Phase: 31-fastapi-auth-endpoints-otp-system, Plan: 02*
*Completed: 2026-02-12*
