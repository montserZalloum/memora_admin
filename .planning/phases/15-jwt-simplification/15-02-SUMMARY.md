---
phase: 15-jwt-simplification
plan: 02
subsystem: auth
tags: [jwt, login, mobile-auth, frappe-hook, session-invalidation]

# Dependency graph
requires:
  - phase: 15-01
    provides: JWT payload with plan_id, SessionService with plan_id storage
provides:
  - Identifier-based login (email or mobile number)
  - Mobile lookup via Frappe User.mobile_no
  - Enriched login response with profile data
  - Plan change session invalidation hook
affects: [15-03, mobile-clients, admin-plan-management]

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "Identifier-based login (email or mobile)"
    - "Mobile lookup via Frappe User doctype"
    - "Enriched login response with profile + XP"
    - "Plan change triggers session invalidation"

key-files:
  created:
    - memora_admin/events/plan_change_sync.py
  modified:
    - fastapi_app/models/auth.py
    - fastapi_app/services/frappe.py
    - fastapi_app/api/v1/endpoints/auth.py
    - memora_admin/hooks.py

key-decisions:
  - "is_email helper uses simple @ check (no regex) per CONTEXT.md"
  - "Mobile lookup returns None on any error (generic failure)"
  - "Plan change invalidates session immediately (no graceful transition)"
  - "Wallet XP fetched from WalletService for login response"

patterns-established:
  - "identifier field in LoginRequest accepts email or mobile"
  - "Plan field required for login (401 if not assigned)"
  - "has_value_changed check before session invalidation"

# Metrics
duration: 2min
completed: 2026-02-05
---

# Phase 15 Plan 02: Login Flow with Identifier Summary

**Identifier-based login with mobile support, enriched response with profile/XP, and plan change session invalidation**

## Performance

- **Duration:** 2 min
- **Started:** 2026-02-05T07:48:21Z
- **Completed:** 2026-02-05T07:50:42Z
- **Tasks:** 3
- **Files modified:** 4
- **Files created:** 1

## Accomplishments
- Login accepts identifier field (email or mobile number)
- Mobile number login resolves to email via Frappe User.mobile_no lookup
- Login response includes profile data (display_name, avatar, gender, xp)
- Login fails with clear error if player has no plan assigned
- Plan change in Frappe invalidates player session immediately
- Refresh flow retrieves plan_id from session (not token)

## Task Commits

Each task was committed atomically:

1. **Task 1: Update LoginRequest model and add mobile lookup to FrappeAuthService** - `4513295` (feat)
2. **Task 2: Update login and refresh endpoints** - `34037f7` (feat)
3. **Task 3: Add plan change session invalidation hook** - `e773b4d` (feat)

## Files Created/Modified
- `fastapi_app/models/auth.py` - Changed email to identifier field in LoginRequest
- `fastapi_app/services/frappe.py` - Added is_email helper, lookup_user_by_mobile, get_player_profile methods
- `fastapi_app/api/v1/endpoints/auth.py` - Updated login/refresh with identifier support, enriched response, wallet XP
- `memora_admin/events/plan_change_sync.py` - Session invalidation on plan change
- `memora_admin/hooks.py` - Registered plan_change_sync handler

## Decisions Made
- **Email detection:** Simple `@` check via is_email helper. Per CONTEXT.md, no complex regex needed.
- **Mobile lookup error handling:** Returns None on any error (network, not found). Generic 401 response prevents enumeration.
- **Plan requirement:** Login explicitly checks for plan and returns clear error "Player must have a plan assigned".
- **Wallet integration:** WalletService.get_wallet called during login for XP in profile response.
- **Session invalidation:** Direct Redis key deletion (memora:session:{user_id}) matches SessionService pattern.

## Deviations from Plan

None - plan executed exactly as written.

## Issues Encountered

None.

## User Setup Required

None - no external service configuration required.

## Next Phase Readiness
- Identifier-based login working for both email and mobile
- Profile data returned with login response
- Plan change session invalidation active
- Ready for integration tests in plan 03

---
*Phase: 15-jwt-simplification*
*Completed: 2026-02-05*
