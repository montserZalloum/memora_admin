# Phase 14: Profile Display Names - Context

**Gathered:** 2026-02-03
**Status:** Ready for planning

<domain>
## Phase Boundary

Enrich leaderboard API responses with human-readable display names and avatars from Memora Player Profile. The leaderboard infrastructure already exists (Phase 10); this phase adds profile data to each entry. Profile creation/editing is managed in Frappe Admin, not in scope here.

</domain>

<decisions>
## Implementation Decisions

### Response Structure
- Flat fields: display_name and avatar as top-level fields in leaderboard entries
- Keep player_id in response for client correlation
- Field names match Frappe schema exactly: `display_name`, `avatar`
- Avatar returned as file identifier (not full URL) — client constructs full path

### Fallback Behavior
- Missing profile → display_name: "Anonymous {last 4 digits of player_id}"
- Missing profile → avatar: return default identifier (e.g., "default_avatar")
- Empty display_name in existing profile → treat as missing, use fallback
- No indicator flag for fallback vs real profile — client sees consistent data shape

### Cache Warming Strategy
- Dual approach: Frappe hook pushes on profile update + on-demand fills gaps
- Hourly scheduled job pre-warms cache for active leaderboard players
- 1-hour TTL on cached profiles (per success criteria)

### Profile Fields
- Cache stores: {player_id, display_name, avatar}
- API exposes: player_id, display_name, avatar (alongside xp, rank)
- Only these two profile fields — no country, grade, or extras
- display_name is NOT unique — multiple players can share same name

### Claude's Discretion
- Batch fetch strategy for cache misses (tradeoff against 25ms target)
- Redis key structure and hash design
- Error handling for Frappe fetch failures
- Exact pre-warming job implementation (which players to cache)

</decisions>

<specifics>
## Specific Ideas

- Research notes from v1.3 planning: "N+1 query risk: Must use Redis pipeline for batch profile fetch from day 1"
- Performance target: Leaderboard with profiles must stay under 25ms (was 20ms raw)
- Fallback format: "Anonymous 1234" (clearer than "Player 1234")

</specifics>

<deferred>
## Deferred Ideas

None — discussion stayed within phase scope

</deferred>

---

*Phase: 14-profile-display-names*
*Context gathered: 2026-02-03*
