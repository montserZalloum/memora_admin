---
phase: 32-event-handler-api-migration
plan: 02
subsystem: api
tags: [frappe-api, identity-migration, docname, player-profile, redis-keys]

# Dependency graph
requires:
  - phase: 32-event-handler-api-migration-01
    provides: Event handler migration to PLAYER-##### identity, user field removed from schema
provides:
  - Frappe APIs using direct docname lookups (no {"user": ...} indirection)
  - Profile batch API filtering by name field
  - Subscription hydration without user-field fallback
  - Device APIs using profile.name for Redis keys
  - Reviews API using pp.name in SQL JOIN
affects: [32-event-handler-api-migration-03, fastapi-sidecar-hydration]

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "Direct docname existence check: frappe.db.exists() with PLAYER-##### instead of {"user": ...} lookup"
    - "profile.name for Redis identity keys (not profile.user)"

key-files:
  created: []
  modified:
    - memora_admin/api/purchase.py
    - memora_admin/api/profile.py
    - memora_admin/api/subscriptions.py
    - memora_admin/api/devices.py
    - memora_admin/api/reviews.py

key-decisions:
  - "Removed subscription fallback blocks entirely (PLAYER-##### is sole identity, no dual-lookup needed)"
  - "Fixed reviews.py pp.user -> pp.name (Deviation Rule 1: discovered during verification grep)"

patterns-established:
  - "Frappe API docname pattern: validate with frappe.db.exists(DocType, docname), then use docname directly"
  - "Identity comment convention: module docstring includes 'Player identity is PLAYER-##### docname (not email). See Phase 32.'"

# Metrics
duration: 3min
completed: 2026-02-12
---

# Phase 32 Plan 02: Frappe API Migration Summary

**Migrated 5 Frappe API files from user-based identity lookups to direct PLAYER-##### docname usage across purchase, profile, subscription, device, and review endpoints**

## Performance

- **Duration:** 3 min
- **Started:** 2026-02-12T16:33:10Z
- **Completed:** 2026-02-12T16:36:26Z
- **Tasks:** 1
- **Files modified:** 5

## Accomplishments
- Eliminated all `{"user": player_id}` lookups from 5 Frappe API files
- Removed subscription API fallback blocks (30+ lines of dead code)
- All profile queries now filter by `name` field instead of removed `user` field
- Device APIs use `profile.name` for Redis key construction (correct PLAYER-##### identity)
- All docstrings updated to document PLAYER-##### identity model
- Phase 32 identity comments added to all module docstrings

## Task Commits

Each task was committed atomically:

1. **Task 1: Migrate Frappe APIs from user-based to docname-based lookups** - `ad850ce` (feat)

**Plan metadata:** (pending)

## Files Created/Modified
- `memora_admin/api/purchase.py` - Direct docname existence check instead of `{"user": user_id}` lookup
- `memora_admin/api/profile.py` - Batch profiles filter by `name`, avatar update uses docname directly
- `memora_admin/api/subscriptions.py` - Removed dual-lookup fallback blocks from access keys and progress
- `memora_admin/api/devices.py` - `profile.name` for Redis key construction (was `profile.user`)
- `memora_admin/api/reviews.py` - Fixed `pp.user` to `pp.name` in SQL JOIN (deviation fix)

## Decisions Made
- Removed subscription fallback blocks entirely rather than simplifying them -- with PLAYER-##### as sole identity, the `{"user": player_id}` fallback path is unreachable dead code
- Fixed reviews.py as part of this plan despite not being in the original file list, because the grep verification step revealed it would break when the user field is removed from schema

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] Fixed pp.user in reviews.py SQL query**
- **Found during:** Task 1 (verification grep across memora_admin/api/)
- **Issue:** `reviews.py:42` uses `WHERE pp.user = %(player)s` in a SQL JOIN query against `tabMemora Player Profile`. With the `user` field removed from schema in Plan 01, this query would fail with a column-not-found error.
- **Fix:** Changed `pp.user` to `pp.name` in the `_get_player_season_seq()` function
- **Files modified:** `memora_admin/api/reviews.py`
- **Verification:** `grep -rn 'profile\.user\|p\.user\|doc\.user\|pp\.user' memora_admin/api/` returns zero matches
- **Committed in:** `ad850ce` (part of Task 1 commit)

---

**Total deviations:** 1 auto-fixed (1 bug fix)
**Impact on plan:** Essential correctness fix. The `reviews.py` file was missed in research but would have broken after schema migration. No scope creep.

## Issues Encountered
None

## User Setup Required
None - no external service configuration required.

## Next Phase Readiness
- All Frappe APIs now use PLAYER-##### docname identity consistently
- Ready for Plan 03 (scheduled tasks: profile_cache.py, fsrs_processor.py)
- FastAPI sidecar hydration calls (FrappeClient) will work correctly with PLAYER-##### docnames

---
*Phase: 32-event-handler-api-migration*
*Completed: 2026-02-12*
