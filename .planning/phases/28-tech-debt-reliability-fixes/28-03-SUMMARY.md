---
phase: 28-tech-debt-reliability-fixes
plan: 03
subsystem: api
tags: [wallet, xp, lua, redis, service-layer, dead-code]

# Dependency graph
requires:
  - phase: 28-02
    provides: Clean deps.py with RedisClient sub-dependency pattern
provides:
  - Reusable calculate_xp_award function in service layer
  - Safe Lua streak script handling missing/corrupt Redis fields
  - Clean WalletService.get_wallet without bytes-handling dead code
affects: [review-endpoints, future-xp-consumers]

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "Public service-layer functions for cross-endpoint reuse (not underscore-prefixed, not endpoint-local)"
    - "Safe Lua HGET pattern: raw = redis.call('HGET',...); val = (raw and tonumber(raw)) or default"

key-files:
  created: []
  modified:
    - fastapi_app/services/wallet.py
    - fastapi_app/api/v1/endpoints/sessions.py

key-decisions:
  - "calculate_xp_award is a module-level function (not a class method) for easy import from any endpoint"
  - "Lua tonumber safety uses two-step (raw and tonumber(raw)) or 0 pattern to handle false, nil, and non-numeric values"

patterns-established:
  - "Service-layer public functions: cross-endpoint business logic lives in services/*.py as module-level functions, imported directly"
  - "Lua HGET safety: always assign raw value first, then (raw and tonumber(raw)) or default"

# Metrics
duration: 3min
completed: 2026-02-11
---

# Phase 28 Plan 03: Wallet Service Cleanup Summary

**Moved calculate_xp_award to service layer, fixed Lua streak tonumber safety, removed bytes-handling dead code from WalletService**

## Performance

- **Duration:** 3 min
- **Started:** 2026-02-11T21:15:22Z
- **Completed:** 2026-02-11T21:18:23Z
- **Tasks:** 1
- **Files modified:** 2

## Accomplishments
- `calculate_xp_award` promoted from private endpoint-local function to public service-layer function, importable by any endpoint (sessions, reviews, etc.)
- Lua STREAK_UPDATE_SCRIPT rewritten with safe `(raw and tonumber(raw)) or 0` pattern for HGET fields that may be missing or corrupt
- Bytes-handling dead paths (`b"xp"`, `b"streak"`) removed from `WalletService.get_wallet` since `decode_responses=True` guarantees string keys
- sessions.py imports `calculate_xp_award` from `fastapi_app.services.wallet` instead of defining it locally

## Task Commits

All changes were already committed in a prior session:

1. **Task 1: Move _calculate_xp_award to wallet service and fix Lua script** - `4a83ccd` (refactor)
   - All four parts (Part A-D) were applied and committed as part of "fixes for performance"

**Note:** Code changes were already present in commit `4a83ccd` on the develop branch. This execution verified all done criteria are met.

## Files Created/Modified
- `fastapi_app/services/wallet.py` - Added `calculate_xp_award` public function, fixed Lua tonumber safety, removed bytes dead code
- `fastapi_app/api/v1/endpoints/sessions.py` - Removed local `_calculate_xp_award`, added import from service layer

## Decisions Made
- `calculate_xp_award` as module-level function (not WalletService method) -- enables direct import without service instantiation
- Lua safety pattern: two-step assignment `raw = HGET; val = (raw and tonumber(raw)) or 0` -- handles false (missing field), nil (tonumber failure), and valid numbers

## Deviations from Plan

None - plan executed exactly as written. All changes were already applied in a prior session (commit `4a83ccd`).

## Issues Encountered
- All code changes were already committed on develop branch in commit `4a83ccd` ("fixes for performance"). Verification confirmed all plan criteria met without additional code changes needed.

## User Setup Required

None - no external service configuration required.

## Next Phase Readiness
- Wallet service clean and reusable for Phase 28-04
- `calculate_xp_award` available for import by review endpoints or any future XP-awarding code path
- No blockers

---
*Phase: 28-tech-debt-reliability-fixes*
*Completed: 2026-02-11*
