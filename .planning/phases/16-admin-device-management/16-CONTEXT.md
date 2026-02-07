# Phase 16: Admin Device Management - Context

**Gathered:** 2026-02-07
**Status:** Ready for planning

<domain>
## Phase Boundary

Admins can view and remove player devices from Frappe Desk. The existing `Memora Player Device` child table on Player Profile is populated from Redis (source of truth) and provides read-only visibility plus per-device removal with immediate session invalidation. No new device registration flows — that's handled at login in FastAPI.

</domain>

<decisions>
## Implementation Decisions

### Device display
- Use existing `Memora Player Device` child table fields: device_id, device_name, platform, last_login, user_agent, push_token
- All fields are **read-only** — data comes from Redis sync only, admin cannot hand-edit
- No device count badge or summary — admin scrolls to the table to see devices
- Empty table shown when player has no devices (standard Frappe behavior)

### Sync behavior
- Sync happens **on form load only** — every time admin opens Player Profile, fetch fresh from Redis and populate child table
- **Redis always wins** — on sync, Frappe child table is completely replaced with current Redis data
- **Silent sync** — no "last synced" indicator, data just appears
- No manual "Refresh Devices" button — form load always fetches latest

### Removal flow
- **Button per row** — each device row has a "Remove" button
- **Confirmation dialog always** — "Remove [device_name]? Player will be logged out immediately."
- **One at a time only** — no bulk "Remove All" action, forces admin to be deliberate
- After removal: **green toast message** ("Device removed successfully") + row disappears from table
- Removal clears device from Redis (source of truth) and triggers immediate session invalidation

### Edge cases
- **Active session on removed device** — player's session is invalidated immediately, logged out on next request
- **Device limit** — controlled globally via `max_devices_per_player` in Memora Settings, no per-player admin override needed
- **Redis unavailable** — show error message ("Could not fetch live device data"), do not show stale Frappe data

### Claude's Discretion
- Device table placement on Player Profile form (inline section vs separate tab)
- Exact confirmation dialog wording and styling
- How session invalidation is implemented (Redis key deletion, token blacklist, etc.)
- Error message styling when Redis is unavailable

</decisions>

<specifics>
## Specific Ideas

No specific requirements — open to standard Frappe patterns for child table display, form scripts, and whitelisted API methods.

</specifics>

<deferred>
## Deferred Ideas

None — discussion stayed within phase scope

</deferred>

---

*Phase: 16-admin-device-management*
*Context gathered: 2026-02-07*
