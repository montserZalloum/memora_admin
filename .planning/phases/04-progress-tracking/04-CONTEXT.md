# Phase 4: Progress Tracking - Context

**Gathered:** 2026-02-02
**Status:** Ready for planning

<domain>
## Phase Boundary

Bitmap-based lesson completion tracking with linear unlock enforcement. Players can mark lessons complete, query progress percentages, and the system enforces unlock rules based on is_linear flags at Track/Unit/Topic levels. XP and streak handling belong to Phase 5; content build pipeline to Phase 6.

</domain>

<decisions>
## Implementation Decisions

### Completion API
- Returns minimal response: `{ success: true }` only
- Wallet/progress fetched via separate endpoints
- Idempotent: re-completing same lesson returns 200 OK silently (no distinction)
- Request body: `{ subject: 'MATH-G5', lesson: 'LESSON-001' }` — subject + lesson identifier
- **Enforces unlock state**: 403 if lesson is locked (respects is_linear rule)

### Progress Response
- Returns **completion percentages only** (not raw bitmaps or lesson lists)
- Client does NOT have access to _b.json, so server computes percentages
- **Full breakdown**: subject total + each track + each unit + each topic percentages
- **Two endpoints**:
  - `GET /progress` — summary of all player's subjects
  - `GET /progress/{subject}` — detailed breakdown for one subject

### Unlock Rules
- is_linear is **configurable per entity** (Track, Unit, Topic each have their own flag)
- Unlock requires **100% completion** of previous item (all lessons done)
- Locked items are **visible but locked** — player sees what's ahead with lock indicator
- **First item always unlocked**: first lesson in topic, first unit in track, etc. is always accessible

### Edge Cases
- **Replay handling**:
  - Track replay count (increment counter for analytics)
  - Trigger wallet for reduced XP (uses `replay_xp` from Memora Settings)
- **Bitmap versioning**: Keep separate bitmaps per content version when structure changes
- **Season expiry**: Archive progress to MariaDB when season ends (for cohort analysis)
- **Failure handling**: Server queues completion requests and returns 202 Accepted (retry is server's job)

### Claude's Discretion
- Include unlock flags in progress response (or separate endpoint)
- Exact progress response JSON structure
- Completion queue implementation (Redis list or similar)
- Bitmap versioning key format

</decisions>

<specifics>
## Specific Ideas

- Replay XP amount comes from `replay_xp` field in Memora Settings doctype
- Progress should be lightweight — percentages only, no raw data transfer
- Lock state is UX hint: visible but not accessible until unlocked

</specifics>

<deferred>
## Deferred Ideas

None — discussion stayed within phase scope

</deferred>

---

*Phase: 04-progress-tracking*
*Context gathered: 2026-02-02*
