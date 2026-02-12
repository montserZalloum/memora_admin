# Phase 30: Frappe Auth API Bridge - Context

**Gathered:** 2026-02-12
**Status:** Ready for planning

<domain>
## Phase Boundary

Whitelisted Frappe APIs that let FastAPI verify player passwords, register players, and manage passwords — all without creating Frappe sessions. Three APIs: `verify_player_password`, `register_player`, `set_player_password`. Plus admin Desk integration for password reset.

</domain>

<decisions>
## Implementation Decisions

### API response shape
- `verify_player_password` returns the same profile data bundle that the current login flow already sends (docname + display_name + avatar + gender + XP) — keep response shape consistent with existing auth
- Error responses are **generic** ("Invalid credentials") — no distinction between wrong phone vs wrong password (prevents phone enumeration)
- No brute-force lockout on Frappe side — FastAPI handles rate limiting in Phase 31
- `register_player` callable from **both** FastAPI (self-registration) and Frappe Desk (admin creates player manually)

### Session invalidation
- Password reset = **immediate force logout** on all devices — delete refresh tokens from Redis
- Current access tokens expire naturally (short-lived), but refresh is blocked immediately
- Invalidation scope: **all devices**, no exceptions — even self-service password change logs out everywhere
- No special audit logging for now — rely on Frappe's built-in Version history

### Registration defaults
- Wallet (XP) initialized to 0 in Redis in the **same call** as profile creation — player is fully ready after register
- `display_name` is **optional** — auto-generate default if not provided (e.g., "لاعب 12345" pattern)
- `gender` and `avatar` accepted as **optional fields** at registration — mobile app can collect during signup flow
- Duplicate phone returns **specific error** ("Phone already registered") — safe because OTP has already been verified by the time register_player is called

### Claude's Discretion
- Session invalidation mechanism (direct Redis DEL vs pubsub signal) — choose based on existing codebase patterns
- Default display name format and generation logic
- Exact wallet initialization approach (direct Redis call vs service method)

</decisions>

<specifics>
## Specific Ideas

- Keep verify response consistent with the existing login flow — don't invent a new shape, match what's already there
- Registration duplicate phone error is acceptable because it sits behind OTP verification (Phase 31 ensures phone ownership before calling register)

</specifics>

<deferred>
## Deferred Ideas

None — discussion stayed within phase scope

</deferred>

---

*Phase: 30-frappe-auth-api-bridge*
*Context gathered: 2026-02-12*
