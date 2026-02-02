# Phase 3: Access Control - Context

**Gathered:** 2026-02-02
**Status:** Ready for planning

<domain>
## Phase Boundary

Content access validated through Double-Gate pattern (season status + player grants). Gate 1 checks season is active and not expired. Gate 2 checks player has direct grant or plan membership. Free preview bypasses Gate 2 for marked content. Payment webhook creates subscriptions and grants. Admin grants sync immediately to Redis.

</domain>

<decisions>
## Implementation Decisions

### Free preview behavior
- Units/Topics with is_free=true provide full content — identical to paid experience
- Free content earns full XP and counts toward streaks (no reduced rewards)
- is_free flag set at Unit or Topic level only — all lessons within inherit
- No upgrade hints in response — free content looks identical to paid

### Grant propagation
- Immediate sync via Frappe doc_events hook — sub-second Redis update on save
- Grants are additive — access valid if either direct grant OR plan membership grants it
- Grants are permanent until explicitly revoked (no expiration dates)
- Grant granularity: Subject-level or Track-level (not Unit/Topic)

### Payment webhook design
- Provider-agnostic interface — specific provider TBD, design for abstraction
- Idempotency via upsert approach — re-applying same grant is safe by design
- Transaction log for failure recovery — background job retries failed Redis writes

### Claude's Discretion
- Webhook location (FastAPI vs Frappe)
- Exact rejection response format and error codes
- Redis data structure for access sets
- Background job implementation for retry queue

</decisions>

<specifics>
## Specific Ideas

No specific requirements — open to standard approaches

</specifics>

<deferred>
## Deferred Ideas

None — discussion stayed within phase scope

</deferred>

---

*Phase: 03-access-control*
*Context gathered: 2026-02-02*
