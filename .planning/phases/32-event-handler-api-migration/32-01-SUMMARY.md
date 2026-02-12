---
phase: 32-event-handler-api-migration
plan: 01
subsystem: events
tags: [redis, event-handlers, identity-migration, docname, fastapi-redis]

# Dependency graph
requires:
  - phase: 29-player-profile-auth-model
    provides: "PLAYER-##### autoname, mobile field, password field on Player Profile"
  - phase: 31-fastapi-auth-endpoints-otp
    provides: "JWT sub claim = PLAYER-##### for players, FastAPI auth fully aligned"
provides:
  - "All 4 event handlers use PLAYER-##### docname identity (not email)"
  - "profile_sync.py and plan_change_sync.py use get_fastapi_redis() with two-pronged invalidation"
  - "access_sync.py simplified: doc.player used directly (no frappe.get_doc lookup)"
  - "Player Profile JSON schema without user field"
affects: [32-02, 32-03, fastapi-services, redis-keys]

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "Two-pronged invalidation pattern applied to profile_sync and plan_change_sync (direct op + pubsub)"
    - "doc.player used directly as Redis identity key in subscription handlers (no Player Profile lookup)"

key-files:
  created: []
  modified:
    - "memora_admin/events/access_sync.py"
    - "memora_admin/events/device_sync.py"
    - "memora_admin/events/plan_change_sync.py"
    - "memora_admin/events/profile_sync.py"
    - "memora_admin/memora_admin/doctype/memora_player_profile/memora_player_profile.json"

key-decisions:
  - "Simplified access_sync.py: use doc.player directly instead of frappe.get_doc lookup (eliminates unnecessary DB call per subscription change)"
  - "Adopted two-pronged invalidation pattern for profile_sync and plan_change_sync (matching catalog_sync.py)"
  - "Removed user field from Player Profile JSON schema (clean break per user decision)"

patterns-established:
  - "PLAYER-##### docname identity: all event handlers use doc.name or doc.player, never doc.user"
  - "get_fastapi_redis() for all Frappe-to-FastAPI shared Redis data (no frappe.cache())"

# Metrics
duration: 2min
completed: 2026-02-12
---

# Phase 32 Plan 01: Event Handler & Schema Migration Summary

**Migrated 4 event handlers from doc.user/frappe.cache() to doc.name/get_fastapi_redis() and removed user field from Player Profile schema**

## Performance

- **Duration:** 2 min
- **Started:** 2026-02-12T16:27:22Z
- **Completed:** 2026-02-12T16:29:55Z
- **Tasks:** 2
- **Files modified:** 5

## Accomplishments
- All 4 event handlers now use PLAYER-##### docname identity (doc.name or doc.player) instead of doc.user
- profile_sync.py and plan_change_sync.py migrated from frappe.cache() to get_fastapi_redis() with two-pronged invalidation pattern
- access_sync.py simplified: eliminated unnecessary frappe.get_doc() lookup in subscription handlers (doc.player IS the PLAYER-##### docname)
- Player Profile JSON schema cleaned: user field removed from both field_order and fields arrays

## Task Commits

Each task was committed atomically:

1. **Task 1: Migrate event handlers to docname identity + FastAPI Redis** - `f1b9608` (feat)
2. **Task 2: Remove user field from Player Profile JSON schema** - `c677e4a` (chore)

## Files Created/Modified
- `memora_admin/events/access_sync.py` - Simplified subscription handlers: doc.player used directly as identity key, removed frappe.get_doc indirection
- `memora_admin/events/device_sync.py` - Changed doc.user to doc.name for PLAYER-##### identity
- `memora_admin/events/plan_change_sync.py` - Migrated from frappe.cache() to get_fastapi_redis(), doc.user to doc.name, two-pronged invalidation
- `memora_admin/events/profile_sync.py` - Migrated from frappe.cache() to get_fastapi_redis(), doc.user to doc.name, two-pronged invalidation
- `memora_admin/memora_admin/doctype/memora_player_profile/memora_player_profile.json` - Removed user field from schema

## Decisions Made
- Simplified access_sync.py to use doc.player directly instead of looking up Player Profile document -- eliminates one frappe.get_doc() call per subscription change, which is a performance improvement
- Adopted the two-pronged invalidation pattern (direct Redis op + pubsub) for both profile_sync and plan_change_sync, matching the established pattern in catalog_sync.py and build_trigger.py
- Removed user field from Player Profile JSON schema per user decision: "Clean break, no confusion about what identifies a player"

## Deviations from Plan

None - plan executed exactly as written.

## Issues Encountered

None

## User Setup Required

None - no external service configuration required. Schema change takes effect on next `bench migrate`.

## Next Phase Readiness
- Event handlers are fully migrated to PLAYER-##### identity model
- Ready for Plan 02 (Frappe API migration) and Plan 03 (scheduled task fixes)
- Remaining files with doc.user/user-based lookups are in api/ and tasks/ directories (covered by plans 02 and 03)

## Self-Check: PASSED

All 5 modified files verified present on disk. Both task commits (f1b9608, c677e4a) verified in git log.

---
*Phase: 32-event-handler-api-migration*
*Completed: 2026-02-12*
