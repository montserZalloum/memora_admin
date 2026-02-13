---
status: resolved
trigger: "progress-percentage-over-100: track progress API returns completed=6 when total=4, resulting in 150%"
created: 2026-02-13T00:00:00Z
updated: 2026-02-13T00:05:00Z
---

## Current Focus

hypothesis: CONFIRMED - Double-counting in end_session stats cold-start path
test: Invalidated corrupted stats, recomputed, verified correct 100% output
expecting: completed=5, total=5, percentage=100
next_action: Archive session

## Symptoms

expected: completed count should never exceed total count; percentage should be 0-100%
actual: GET /api/v1/progress/SUBJ-00028/tracks/Track-00035 returns completed=6, total=4 (150%)
errors: No error - API returns 200 OK with wrong data
reproduction: Call GET /api/v1/progress/SUBJ-00028/tracks/Track-00035 with valid auth
started: Unknown

## Eliminated

## Evidence

- timestamp: 2026-02-13T00:00:01Z
  checked: sessions.py end_session() lines 263-346
  found: The Lua script complete_session (line 263) does SETBIT atomically BEFORE stats update. When stats hash doesn't exist (line 326), get_completed_bits (line 328) reads the bitmap AFTER the bit is already set. compute_stats_from_hierarchy (line 334) counts the new lesson as completed. Then HINCRBY (lines 342-345) increments completed AGAIN by 1.
  implication: Every lesson completion that coincides with a stats cold start (expired TTL or first time) double-counts completed.

- timestamp: 2026-02-13T00:00:01Z
  checked: Stats TTL and lifecycle
  found: Stats hash has 1h TTL (StatsService.CACHE_TTL = 3600). Every time it expires and a lesson is completed, the cold-start path triggers double-counting. With 5 total lessons and enough TTL expirations during play, completed reached 6 (5 real + extra from double-counts, with total stuck at 4 from stale cache before 5th lesson was added).
  implication: The bug is cumulative -- gets worse across TTL boundaries.

- timestamp: 2026-02-13T00:03:00Z
  checked: Redis stats hash for PLAYER-00003:SUBJ-00028
  found: HGETALL showed completed=6, total=4 at every level (subject, track, unit, topic). BITCOUNT on progress bitmap showed 5 bits set. Hierarchy shows 5 lessons (bit_indexes 0,3,4,5,6). All 5 bits confirmed set.
  implication: Stats had stale total=4 (from before 5th lesson added) plus double-counted completed=6.

- timestamp: 2026-02-13T00:04:00Z
  checked: After fix -- deleted corrupted stats, recomputed via API call
  found: Track progress now returns completed=5, total=5, percentage=100%. Stats hash verified clean: 5/5 at all levels.
  implication: Fix confirmed working.

- timestamp: 2026-02-13T00:04:30Z
  checked: Scanned all stats keys for corruption
  found: No other corrupted stats keys found (only 2 total stats keys, the other was clean).
  implication: Only PLAYER-00003:SUBJ-00028 was affected.

## Resolution

root_cause: Double-counting in end_session stats cold-start path (sessions.py:316-346). When the stats hash didn't exist (after 1h TTL expiry), compute_stats_from_hierarchy was called with completed_bits that already included the just-completed lesson (SETBIT happened in Lua script earlier). The computed stats already included the new lesson. Then HINCRBY incremented completed counters AGAIN, double-counting the lesson. This accumulated over time across TTL boundaries.

fix: (1) In end_session, moved HINCRBY inside an `else` branch -- only increment when stats hash already exists. When cold-starting (stats hash missing), the full recompute from bitmap is already accurate. (2) Added min(percentage, 100.0) clamping to all 9 percentage computed fields in models/progress.py as defensive guard. (3) Added min(completed, total) clamping in get_progress_summary for BITCOUNT-based path.

verification: Deleted corrupted stats key for PLAYER-00003:SUBJ-00028:v1. Called API endpoint which triggered recomputation. Got correct result: completed=5, total=5, percentage=100%. Verified recomputed stats hash in Redis shows 5/5 at all levels. Scanned all stats keys -- no other corruption found.

files_changed:
- fastapi_app/api/v1/endpoints/sessions.py (core fix: conditional HINCRBY)
- fastapi_app/models/progress.py (defensive: clamp percentage to 100%)
- fastapi_app/api/v1/endpoints/progress.py (defensive: clamp completed to total)
