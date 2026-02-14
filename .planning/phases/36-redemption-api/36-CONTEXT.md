# Phase 36: Redemption API - Context

**Gathered:** 2026-02-14
**Status:** Ready for planning

<domain>
## Phase Boundary

Students can enter a PIN in the mobile app to preview what a voucher card unlocks, then redeem it to receive content access instantly via the existing Phase 23 subscription pipeline. Includes rate limiting for brute-force protection and full audit logging. Financial tracking and reporting are separate phases (37, 38).

</domain>

<decisions>
## Implementation Decisions

### Preview response shape
- Claude's discretion on detail level (names, icons, etc.) — pick what's practical given existing Product Grant data
- Already-owned grants are **hidden** from the preview (not shown with a label)
- Preview includes the card's **face value** (e.g., "50 SAR") so student confirms the right card
- If ALL grants are already owned, return **ALL_GRANTS_OWNED error** (not an empty success) — card is preserved

### Multi-grant redemption flow
- **One grant per redemption, card consumed** — student picks one grant from the card, card becomes Redeemed, remaining grants are not given
- No warning or grant count indicator needed — in practice most cards have 1 grant
- ALREADY_OWNED error returns just the error code — no available grants list (student can call preview again)
- **Fire-and-forget** — no confirmation token required, POST /redeem with PIN + grant_id is final. App handles any confirmation UI before calling.

### Error experience
- **Machine-readable error codes only** (INVALID_PIN, ALREADY_REDEEMED, EXPIRED, VOID, BATCH_INACTIVE, SEASON_INACTIVE, ALL_GRANTS_OWNED, GRANT_NOT_IN_BATCH, ALREADY_OWNED, NOT_ALLOCATED, RATE_LIMITED)
- No Arabic messages in API responses — app handles all human-readable copy
- **Specific per state** — different error codes for each card state (not a single vague "invalid" message)
- Redeem success returns **minimal confirmation** only: status + grant reference. App already knows what was redeemed.

### Rate limiting
- Preview is **not rate limited** — students are young, not tech-savvy, need forgiving UX
- Rate limiting applies to **failed attempts only** — successful previews/redeems don't count against the limit
- 5 failed attempts/hour per player, 20 failed attempts/hour per IP (uniform, no auth tiers)
- RATE_LIMITED error **includes retry_after seconds** so the app can show a countdown
- No rate limit headers on non-error responses — only the error code + retry_after on limit hit
- Redis TTL-based expiry, no cleanup job needed

### Claude's Discretion
- Preview response field names and structure
- HTTP status codes for each error type
- Redis key naming for rate limit counters
- Redemption Log field population details
- SELECT FOR UPDATE implementation specifics

</decisions>

<specifics>
## Specific Ideas

- Students have limited tech understanding — errors and rate limits should never leave them stuck with no path forward
- Keep the API surface minimal: 2 endpoints (preview + redeem), error codes as the contract, app owns all UX
- Existing Phase 23 pipeline handles the heavy lifting (Subscription Transaction with status="Completed" triggers Player Subscription creation and Redis SADD)

</specifics>

<deferred>
## Deferred Ideas

None — discussion stayed within phase scope

</deferred>

---

*Phase: 36-redemption-api*
*Context gathered: 2026-02-14*
