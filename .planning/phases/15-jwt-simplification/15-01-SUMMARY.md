---
phase: 15-jwt-simplification
plan: 01
subsystem: auth
tags: [jwt, pydantic, redis, session, frappe-doctype]

# Dependency graph
requires:
  - phase: 08-leaderboard
    provides: Redis session management pattern
  - phase: 14-profile-display
    provides: Profile data patterns for login response
provides:
  - JWT access token with plan_id field (no role/tz)
  - SessionService storing plan_id for refresh flow
  - LoginProfile and EnrichedTokenResponse models
  - Memora Player Profile gender field
affects: [15-02, 15-03, login-endpoint, refresh-endpoint]

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "Session storage as JSON object (not plain string)"
    - "validate_session returns tuple (is_valid, plan_id)"
    - "Timezone hardcoded to Asia/Amman for all players"

key-files:
  created: []
  modified:
    - fastapi_app/core/security.py
    - fastapi_app/services/session.py
    - fastapi_app/models/auth.py
    - memora_admin/memora_admin/doctype/memora_player_profile/memora_player_profile.json

key-decisions:
  - "Store plan_id in Redis session JSON to avoid Frappe roundtrip on refresh"
  - "Timezone hardcoded to Asia/Amman (removed from token)"
  - "Role removed from token (all FastAPI users are players)"
  - "Gender field optional in Player Profile schema"

patterns-established:
  - "Session JSON format: {fid, plan} for extensibility"
  - "validate_session tuple return for plan_id access"

# Metrics
duration: 2min
completed: 2026-02-05
---

# Phase 15 Plan 01: JWT Token Structure & Session Updates Summary

**JWT payload simplified with plan_id replacing role/timezone, session storage upgraded to JSON for refresh flow**

## Performance

- **Duration:** 2 min
- **Started:** 2026-02-05T07:43:21Z
- **Completed:** 2026-02-05T07:45:40Z
- **Tasks:** 3
- **Files modified:** 4

## Accomplishments
- Access token now includes plan_id, no longer includes role/tz fields
- SessionService stores plan_id alongside family_id as JSON for refresh token flow
- LoginProfile and EnrichedTokenResponse models ready for enriched login response
- Memora Player Profile has optional gender field

## Task Commits

Each task was committed atomically:

1. **Task 1: Add gender field to Memora Player Profile schema** - `a8c914e` (feat)
2. **Task 2: Update JWT creation and models** - `26ce1f4` (feat)
3. **Task 3: Update SessionService to store plan_id** - `5f6075d` (feat)

## Files Created/Modified
- `memora_admin/memora_admin/doctype/memora_player_profile/memora_player_profile.json` - Added gender Select field
- `fastapi_app/core/security.py` - Updated create_access_token signature (plan_id, no role/tz)
- `fastapi_app/models/auth.py` - Updated TokenPayload, added LoginProfile and EnrichedTokenResponse
- `fastapi_app/services/session.py` - JSON session storage with plan_id

## Decisions Made
- **Session storage format:** JSON object `{"fid": ..., "plan": ...}` instead of plain string. Enables future extensibility without migration.
- **Timezone approach:** Hardcoded note in docstring rather than constant, since timezone is only needed at wallet/streak service level.
- **Gender field optional:** Allows existing profiles to work without migration. Returned as null if not set.
- **Legacy session handling:** get_session_data parses legacy plain string format for backward compatibility.

## Deviations from Plan

None - plan executed exactly as written.

## Issues Encountered

None.

## User Setup Required

None - no external service configuration required.

## Next Phase Readiness
- JWT creation function ready for login endpoint (plan 02)
- SessionService ready to store plan_id on login
- Models ready for enriched login response
- Ready to implement identifier-based login with mobile support

---
*Phase: 15-jwt-simplification*
*Completed: 2026-02-05*
