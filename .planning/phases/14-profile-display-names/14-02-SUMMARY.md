---
phase: 14-profile-display-names
plan: 02
subsystem: profile-cache
tags: [frappe-hooks, redis-cache, pubsub, scheduled-tasks]
requires:
  - "14-01: ProfileService created"
provides:
  - "Frappe hook pushes profiles to Redis on update"
  - "Batch API for FastAPI cache miss handling"
  - "Pub/sub invalidation for profile changes"
  - "Hourly pre-warming for leaderboard players"
affects:
  - "14-03: Will use ProfileService with cache populated"
tech-stack:
  added: []
  patterns:
    - "Frappe doc_events for cache push"
    - "Redis pub/sub for cross-service cache invalidation"
    - "Pipeline SET for batch cache warming"
key-files:
  created:
    - memora_admin/events/profile_sync.py
    - memora_admin/api/__init__.py
    - memora_admin/api/profile.py
    - memora_admin/tasks/profile_cache.py
  modified:
    - memora_admin/hooks.py
    - fastapi_app/core/pubsub.py
key-decisions:
  - "Use set_value with expires_in_sec for Frappe cache TTL"
  - "Hourly idempotency via Redis key rather than daily log"
duration: ~2 minutes
completed: 2026-02-05
---

# Phase 14 Plan 02: Frappe-ProfileService Integration Summary

**One-liner:** Frappe hooks push profile updates to Redis, pub/sub invalidates FastAPI cache, hourly task pre-warms leaderboard players.

## Performance

| Operation | Target | Implementation |
|-----------|--------|----------------|
| Profile push | Sub-second | Frappe hook on insert/update |
| Cache invalidation | Immediate | Pub/sub message to FastAPI |
| Pre-warming | 300 profiles/hour | Pipeline SET with 1h TTL |

## Accomplishments

1. **Profile sync hook** - Pushes profile data to Redis on create/update with 1-hour TTL
2. **Batch profile API** - Frappe whitelist API for FastAPI to fetch profiles on cache miss
3. **Pub/sub handler** - FastAPI listener invalidates ProfileService cache on profile message
4. **Scheduled pre-warming** - Hourly task caches profiles for top 100 players from each leaderboard

## Task Commits

| Task | Name | Commit | Key Changes |
|------|------|--------|-------------|
| 1 | Create profile_sync hook and batch API | ffcdd09 | Hook + API + hooks.py registration |
| 2 | Add pub/sub handler and scheduled task | 01090e3 | pubsub.py + profile_cache.py |

## Files Created

| File | Purpose |
|------|---------|
| `memora_admin/events/profile_sync.py` | Hook to push profiles to Redis cache |
| `memora_admin/api/__init__.py` | API module init |
| `memora_admin/api/profile.py` | Batch profile fetch API |
| `memora_admin/tasks/profile_cache.py` | Hourly cache warming task |

## Files Modified

| File | Changes |
|------|---------|
| `memora_admin/hooks.py` | Added after_insert + on_update hooks, scheduled task at :30 |
| `fastapi_app/core/pubsub.py` | Added profile invalidation handler |

## Decisions Made

| Decision | Rationale |
|----------|-----------|
| Use `set_value` with `expires_in_sec` | Frappe cache API for TTL support |
| Hourly idempotency via Redis key | More appropriate than daily task log for hourly task |
| Collect from 3 leaderboards | alltime, daily, weekly covers active players |
| Publish invalidation message | Ensures FastAPI in-memory cache is also cleared |

## Deviations from Plan

None - plan executed exactly as written.

## Issues Encountered

None.

## Next Phase Readiness

**Ready for 14-03:** Frappe integration complete. ProfileService can now:
1. Receive immediate cache updates via hook
2. Invalidate on pub/sub message
3. Fetch profiles via batch API on cache miss
4. Rely on hourly pre-warming for leaderboard players

---

*Phase: 14-profile-display-names*
*Plan: 02 - Frappe-ProfileService Integration*
*Completed: 2026-02-05*
