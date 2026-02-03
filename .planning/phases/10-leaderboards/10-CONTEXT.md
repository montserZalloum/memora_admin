# Phase 10: Leaderboards - Context

**Gathered:** 2026-02-03
**Status:** Ready for planning

<domain>
## Phase Boundary

Competitive XP rankings via API. Users can view leaderboards (daily/weekly/all-time), optionally filtered by subject. Users can retrieve their own rank with context (neighbors, distance to next tier). No streak leaderboard.

</domain>

<decisions>
## Implementation Decisions

### Ranking display
- Number of players returned is configurable via request parameter (limit)
- Each entry shows: rank, display name, XP value, avatar URL
- All players visible — no anonymization option
- View-only list — entries don't link to profiles

### Your rank presentation
- Separate endpoint from main leaderboard (GET /leaderboard/{type}/me)
- Include ±2 neighbors for context around user's position
- Include distance to next tier (XP needed to pass player above)
- Unranked users (0 XP) treated as tied for last place
- Claude's discretion: how to visually present "your rank" section

### Tie-breaking rules
- Earlier achiever wins — whoever reached that XP first ranks higher
- Tied players share the same rank number (e.g., two #5s, then #7)

### Leaderboard types
- Daily XP (resets at midnight Asia/Amman)
- Weekly XP (resets Friday midnight Asia/Amman)
- All-time XP
- Type specified via request parameter
- No streak leaderboard

### Competition scope
- Optional filtering by subject_id (not deeper in hierarchy)
- Fixed time periods only (daily/weekly/all-time) — no custom date ranges
- All players included regardless of subscription status

### Claude's Discretion
- Redis data structure for efficient rank lookups
- Archival approach for daily/weekly history
- Exact response schema structure
- Error handling patterns

</decisions>

<specifics>
## Specific Ideas

- Weekly reset on Friday midnight aligns with Middle East weekend pattern
- Subject filtering enables class-specific competitions
- Neighbors (±2) gives motivation context without overwhelming data

</specifics>

<deferred>
## Deferred Ideas

- Streak leaderboard — explicitly removed from scope
- Track/unit level filtering — subject only for now
- Custom date range queries — fixed periods only

</deferred>

---

*Phase: 10-leaderboards*
*Context gathered: 2026-02-03*
