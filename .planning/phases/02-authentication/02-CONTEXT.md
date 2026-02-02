# Phase 2: Authentication - Context

**Gathered:** 2026-02-02
**Status:** Ready for planning

<domain>
## Phase Boundary

Players authenticate via JWT tokens verified statelessly. Login with Frappe credentials, receive access + refresh tokens, exchange refresh for new access tokens. Single-session enforcement per player.

</domain>

<decisions>
## Implementation Decisions

### Login Flow
- Credentials submitted as JSON body: `{email, password}`
- Frappe credential verification via internal API call (respects Frappe auth logic, hooks, validations)
- Rate limiting: both per-IP AND per-account
- Limits: 10 attempts/min per IP, 5 attempts/min per target account

### Token Design
- Rich JWT payload: user ID, email, role, timezone, display name
- Both tokens returned in JSON response body (client stores where it wants)
- Access token lifetime: 15 minutes
- Refresh token lifetime: 30 days (extended from original 7 days)
- Refresh tokens are reusable (not rotated on each refresh)

### Error Responses
- Generic error messages on login failure ("Invalid credentials" — doesn't reveal if email exists)
- Rate limit responses include Retry-After header and seconds remaining in body
- No logging of failed login attempts

### Session Behavior
- Single session per player — new login invalidates previous session
- Old device discovers invalidation on next API call (401) — silent, no push notification

### Claude's Discretion
- Error response structure (FastAPI conventions)
- Session invalidation mechanism (token family ID vs refresh blocklist)
- Whether to provide explicit logout endpoint
- Exact implementation of rate limiting storage (Redis counters)

</decisions>

<specifics>
## Specific Ideas

No specific requirements — open to standard approaches.

</specifics>

<deferred>
## Deferred Ideas

None — discussion stayed within phase scope.

</deferred>

---

*Phase: 02-authentication*
*Context gathered: 2026-02-02*
