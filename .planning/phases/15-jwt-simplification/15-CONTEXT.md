# Phase 15: JWT Simplification - Context

**Gathered:** 2026-02-05
**Status:** Ready for planning

<domain>
## Phase Boundary

Streamline JWT access token payload and enhance login flow. Adds plan_id to token, removes unused fields (timezone, role), enables mobile number login, and enriches login response with player profile data.

</domain>

<decisions>
## Implementation Decisions

### Token Payload Changes
- Add `plan_id` field from Memora Player Profile.plan (Frappe document name format, e.g., "PLAN-00001")
- Remove `timezone` field — hardcode "Asia/Amman" directly in code where needed
- Remove `role` field — all FastAPI users are players (admins use Frappe Desk)

### plan_id Source
- Read from Memora Player Profile.plan field (linked Memora Plan doctype)
- Plan is a required field — login fails if player has no plan assigned
- Reject login with clear error: "Player must have a plan assigned"
- Use full Frappe document name (not numeric ID) for plan_id in token

### Plan Change Behavior
- When admin changes player's plan in Frappe, invalidate all player tokens
- Player must re-login to get new token with updated plan_id
- No graceful transition — immediate invalidation acceptable

### Login Request Changes
- Change from `{ email, password }` to `{ identifier, password }`
- `identifier` field accepts either email or mobile number
- Auto-detect type: email format → email lookup, otherwise → mobile lookup
- Mobile number lookup: query Memora Player Profile by mobile_number field
- Mobile match: exact match required (no normalization)

### Login Response Changes
- Enrich response with player profile data
- New structure: `{ access_token, refresh_token, token_type, profile: { display_name, avatar, gender, xp } }`
- Profile data sourced from Memora Player Profile + Wallet

### Token Migration
- No migration needed — no production data exists
- Old tokens without plan_id can simply be invalidated

### Claude's Discretion
- Error message wording for login failures
- How to detect email vs mobile format in identifier field
- Profile data fetch strategy (parallel vs sequential)
- Rate limit key changes for identifier-based login

</decisions>

<specifics>
## Specific Ideas

- Login should work identically whether user provides email or mobile number
- Profile fields in response match what client needs to display user dashboard immediately after login
- No separate /me endpoint needed if login returns profile data

</specifics>

<deferred>
## Deferred Ideas

None — discussion stayed within phase scope (mobile login was added to this phase per user request)

</deferred>

---

*Phase: 15-jwt-simplification*
*Context gathered: 2026-02-05*
