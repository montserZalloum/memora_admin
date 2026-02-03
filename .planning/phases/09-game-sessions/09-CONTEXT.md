# Phase 9: Game Sessions - Context

**Gathered:** 2026-02-03
**Status:** Ready for planning

<domain>
## Phase Boundary

Lesson flow tracking with session lifecycle and validation. Users start lessons, complete stages client-side, and submit results when done. Sessions enforce single-lesson rule and auto-expire after 1 hour.

</domain>

<decisions>
## Implementation Decisions

### Lesson Flow Architecture
- **No per-stage API calls** — Client fetches lesson JSON from CDN at start, runs entirely offline
- Single API call at lesson complete with full payload (all stages, timing, fails, etc.)
- Store stage data for analytics following existing codebase patterns

### Stage Handling
- Client handles stage ordering — backend trusts client
- No server-side stage sequence validation needed
- All stage data (timing, fail counts, etc.) submitted in one payload at lesson end

### Session Recovery
- Crash means restart — no recovery logic
- Old sessions expire via TTL (1 hour), no explicit cleanup needed
- Simpler is better for this use case

### Concurrent Session Handling
- One active session per user, regardless of device
- Starting new lesson force-closes any existing session (silent, no notification)
- Old session gets no XP — just closed

### Claude's Discretion
- Session start endpoint design and metadata captured
- Session data structure in Redis
- Exact TTL refresh behavior (if any)
- Error response format for edge cases

</decisions>

<specifics>
## Specific Ideas

- Lesson content comes from CDN as JSON — session just tracks that a lesson is in progress
- Stage data payload at completion should match whatever analytics patterns exist in codebase

</specifics>

<deferred>
## Deferred Ideas

None — discussion stayed within phase scope

</deferred>

---

*Phase: 09-game-sessions*
*Context gathered: 2026-02-03*
