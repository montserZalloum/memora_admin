# Phase 32: Event Handler & API Migration - Context

**Gathered:** 2026-02-12
**Status:** Ready for planning

<domain>
## Phase Boundary

Update all event handlers and Frappe APIs from user-based identity model (doc.user, email lookups) to docname-based identity model (PLAYER-#####, phone lookups). This is the final phase of v2.0 Mobile-First Player Authentication — after this, the old identity model is fully replaced.

</domain>

<decisions>
## Implementation Decisions

### Existing player handling
- **Pre-launch, no real players** — database has only test data. All new players will be PLAYER-##### format
- **Drop old-style compatibility completely** — remove all email/user-based identity paths. Clean break, simpler code
- **Leave test data alone** — don't include cleanup scripts. Just make new code work with PLAYER-##### naming
- **Admin overlap exists** — some events (device management, profile updates) can be triggered by admins acting on player data. Event handlers must distinguish between admin users (Frappe User email) and player docnames (PLAYER-#####)

### doc.user field removal
- **Replace doc.user with doc.name** — rewrite all references to use the player's docname (PLAYER-#####). Remove doc.user dependency entirely
- **Remove doc.user field from schema** — delete from Player Profile JSON schema. Clean break, no confusion about what identifies a player
- **Redis key audit needed** — not certain which Redis keys use doc.user vs docname. Claude must audit all key patterns and fix any that reference user/email instead of docname

### Redis target scope
- **Claude's Discretion: scope of get_fastapi_redis() migration** — audit all event handlers for frappe.cache() usage that affects FastAPI-consumed data, not just plan_change_sync.py and profile_sync.py
- **Claude's Discretion: Redis helper pattern** — check existing get_fastapi_redis() pattern in build_trigger.py/catalog_sync.py and reuse if suitable
- **Claude's Discretion: invalidation pattern** — decide whether to keep two-pronged (direct delete + pubsub) or simplify, based on what's already established

### Migration completeness
- **Full codebase audit** — grep for every doc.user, user=, email-based lookup and fix them all. Nothing left behind
- **Include JavaScript** — audit Python AND JavaScript (.js files in DocTypes). Form handlers, list views, client scripts
- **Audit all FastAPI too** — full audit of fastapi_app/ for any user-based identity references, not just Frappe side
- **Add code comments** — document in key files that player identity is PLAYER-##### docname, not email

### Claude's Discretion
- JWT claims audit — verify JWT 'sub' claim alignment with PLAYER-##### and fix if needed
- Redis helper pattern and connection approach
- Invalidation pattern (two-pronged vs direct-only)
- Scope of get_fastapi_redis() migration beyond SC#3 files

</decisions>

<specifics>
## Specific Ideas

- Admin vs player distinction is important — admins trigger player events via Frappe Desk (e.g., editing subscriptions, managing devices). Event handlers must handle both identity types
- STATE.md already flagged: `profile_sync.py` references `doc.user` which is None for phone-based players
- STATE.md already flagged: Mobile-to-docname resolution required before any __Auth table operation

</specifics>

<deferred>
## Deferred Ideas

None — discussion stayed within phase scope

</deferred>

---

*Phase: 32-event-handler-api-migration*
*Context gathered: 2026-02-12*
