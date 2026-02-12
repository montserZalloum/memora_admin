# Phase 31: FastAPI Auth Endpoints + OTP System - Context

**Gathered:** 2026-02-12
**Status:** Ready for planning

<domain>
## Phase Boundary

Player-facing authentication API endpoints: login, registration, password reset with OTP verification. Admin login endpoint. Token refresh. This phase builds the FastAPI layer on top of the Frappe Auth API Bridge (Phase 30). OTP uses static "1111" stub — real SMS integration is a future phase.

</domain>

<decisions>
## Implementation Decisions

### Login response shape
- Player login returns tokens + profile: `display_name`, `avatar`, `xp` (no `gender` — dropped from login response)
- Same enriched response shape as current `/auth/login`
- X-Device-ID header still required; device limit enforcement unchanged
- Admin login response shape: Claude's discretion (tokens + minimal admin info)

### Registration flow
- Required fields: phone, password, display_name, gender, grade, plan
- Optional field: major
- A separate `GET` endpoint provides available options (grades, majors, plans) for the client to populate pickers
- On duplicate phone: specific error — "Phone number already registered" (not generic)
- Auto-login after registration: returns tokens + profile immediately (no separate login step needed)
- Registration creates Player Profile with wallet and Redis state initialized

### Error responses & messages
- Language: English codes + English messages — client handles i18n/Arabic translation
- Login failures: generic "Invalid credentials" — don't distinguish between "account not found" vs "wrong password" (prevents enumeration)
- Registration duplicate phone: specific error (exception to generic pattern — UX value outweighs enumeration risk since phone is the identifier)
- Error format: FastAPI default `{"detail": "..."}` — no custom error envelope
- OTP errors: include remaining attempts info, e.g. `{"detail": "Invalid OTP", "remaining_attempts": 2}` so app can show countdown

### OTP & token behavior
- OTP validity: 5 minutes
- Password reset temp token: 15 minutes (issued after OTP verification, used to set new password)
- Access token lifetime: 1 hour (changed from 15 min default — `jwt_access_token_expire_minutes` = 60)
- Refresh token lifetime: driven by `session_timeout_days` from Memora Settings (currently 30 days)
- Password reset invalidates ALL existing sessions (all devices must re-login)
- Rate limits per roadmap: 3 OTP/phone/10min, 10 OTP/IP/10min, 3 incorrect verification attempts = OTP invalidated, 60-second resend cooldown

### Claude's Discretion
- Admin login response shape (tokens only vs tokens + admin profile)
- Registration options endpoint structure and caching
- OTP storage implementation (Redis key structure, TTL handling)
- Rate limit implementation details
- Exact error codes for each failure scenario

</decisions>

<specifics>
## Specific Ideas

- Registration options endpoint (`GET /auth/registration-options` or similar) should return grades, majors, and plans so the mobile client can populate picker UI
- `session_timeout_days` from Memora Settings DocType should drive refresh token lifetime (not hardcoded in FastAPI config)
- Current login already does device registration, rate limiting, session creation, wallet fetch — new player login should follow the same patterns but use Phase 30's `verify_player_password()` instead of Frappe User auth

</specifics>

<deferred>
## Deferred Ideas

None — discussion stayed within phase scope

</deferred>

---

*Phase: 31-fastapi-auth-endpoints-otp-system*
*Context gathered: 2026-02-12*
