---
phase: 30-frappe-auth-api-bridge
plan: 01
subsystem: auth
tags: [frappe-api, password, redis, player-auth]

requires:
  - phase: 29
    provides: "PBKDF2-SHA256 password hashing, mobile field, PLAYER-##### autoname"
provides:
  - "Three whitelisted Frappe APIs for player auth (verify, register, set_password)"
  - "Desk Reset Password button on Player Profile"
affects: [phase-31-fastapi-auth-endpoints]

tech-stack:
  added: []
  patterns: ["whitelisted API with FrappeClient auth", "mobile-to-docname resolution", "generic error anti-enumeration"]

key-files:
  created: [memora_admin/api/auth.py]
  modified: [memora_admin/memora_admin/doctype/memora_player_profile/memora_player_profile.js]

key-decisions:
  - "Lazy imports for check_password, update_password, and get_fastapi_redis inside functions to avoid circular imports"
  - "Unicode escape for Arabic default display_name to keep source file ASCII-safe"
  - "_get_player_xp returns 0 on any Redis failure (consistent with self-healing wallet pattern)"
  - "_initialize_redis_wallet is non-fatal -- wallet self-heals via ensure_hydrated() on first API call"

patterns-established:
  - "mobile-to-docname resolution: always resolve phone to PLAYER-##### before __Auth table operations"
  - "anti-enumeration: same generic error for both 'not found' and 'wrong password' in verify"
  - "session invalidation: r.delete(memora:session:{player}) on password change (same as devices.py)"

duration: 2min
completed: 2026-02-12
---

# Phase 30 Plan 01: Frappe Auth API Bridge Summary

**Three whitelisted Frappe APIs (verify_player_password, register_player, set_player_password) bridging FastAPI sidecar to Frappe password infrastructure, plus Desk Reset Password button with session invalidation via Redis DEL.**

## Accomplishments

### Task 1: Frappe auth API with three whitelisted functions
Created `memora_admin/api/auth.py` (187 lines) with:

- **verify_player_password(mobile, password)**: Normalizes phone, resolves mobile-to-docname, verifies via `check_password()` against __Auth table, returns profile dict with XP from Redis wallet. Generic "Invalid credentials" for both not-found and wrong-password (anti-enumeration).
- **register_player(mobile, password, plan, grade, major, season, ...)**: Validates uniqueness, generates Arabic default display_name, creates doc via `insert(ignore_permissions=True)` (DocType hooks handle PBKDF2 hashing + wallet DocType creation), seeds Redis wallet, returns profile dict with xp=0.
- **set_player_password(player_name, new_password)**: Validates existence + 8-char minimum, updates hash via `update_password()`, invalidates sessions via `r.delete(session_key)`.
- **Three helpers**: `_get_player_xp` (Redis hget, 0 on failure), `_initialize_redis_wallet` (Redis hset, non-fatal), `_invalidate_player_sessions` (Redis delete, non-fatal).

### Task 2: Reset Password button on Player Profile Desk form
Added "Reset Password" button to Actions menu (alongside existing "Grant Access"):
- Dialog with Password + Confirm Password fields
- Client-side validation: match check + 8-char minimum (also enforced server-side)
- Calls `memora_admin.api.auth.set_player_password` with freeze overlay
- Green alert on success: "Password reset. Player will be logged out."

## Task Commits

| Task | Name | Commit | Files |
|------|------|--------|-------|
| 1 | Create Frappe auth API | 2f66614 | memora_admin/api/auth.py |
| 2 | Add Reset Password button | 808298c | memora_player_profile.js |

## Files Created/Modified

### Created
- `memora_admin/api/auth.py` -- 3 whitelisted functions + 3 helpers (187 lines)

### Modified
- `memora_admin/memora_admin/doctype/memora_player_profile/memora_player_profile.js` -- Added Reset Password button (+51 lines)

## Decisions Made

1. **Lazy imports inside functions** -- `check_password`, `update_password`, and `get_fastapi_redis` imported inside function bodies to avoid circular imports and keep module load lightweight (same pattern as devices.py).
2. **Unicode escape for Arabic default** -- Used `\u0644\u0627\u0639\u0628` for Arabic display_name default to keep source file ASCII-safe while producing correct Arabic output.
3. **Non-fatal Redis operations** -- All three helpers wrap Redis calls in try/except, returning safe defaults (0 for XP, None for wallet init, silent for session invalidation). Consistent with existing self-healing cache patterns.

## Deviations from Plan

None -- plan executed exactly as written.

## Issues Encountered

None.

## Next Phase Readiness

Phase 31 (FastAPI Auth Endpoints + OTP System) can proceed immediately:
- `verify_player_password` is ready for the FastAPI login endpoint to call via FrappeClient
- `register_player` is ready for the FastAPI registration endpoint to call via FrappeClient
- `set_player_password` is ready for the FastAPI password reset endpoint to call via FrappeClient
- All three use `allow_guest=False` -- FastAPI must authenticate via API key (FrappeClient pattern already established)

No blockers. No new concerns.

---
*Phase: 30-frappe-auth-api-bridge*
*Completed: 2026-02-12*
