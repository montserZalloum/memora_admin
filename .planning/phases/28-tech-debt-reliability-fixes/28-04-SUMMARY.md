---
phase: 28
plan: 04
subsystem: auth-admin
tags: [admin-auth, dependency-injection, input-validation, jwt, fastapi]
dependency_graph:
  requires: ["28-02"]
  provides: ["RequireAdmin dependency", "ADMIN_ROLE constant", "TokenPayload.role field", "Path-validated player_id"]
  affects: []
tech_stack:
  added: []
  patterns: ["reusable admin dependency via Annotated+Depends", "Path regex validation for route params", "conditional JWT claim inclusion"]
key_files:
  created: []
  modified:
    - fastapi_app/models/auth.py
    - fastapi_app/core/security.py
    - fastapi_app/api/deps.py
    - fastapi_app/api/v1/endpoints/access.py
    - fastapi_app/api/v1/endpoints/wallet.py
    - fastapi_app/main.py
decisions:
  - id: "28-04-D1"
    decision: "ADMIN_ROLE constant + require_admin dependency in deps.py as single source of truth for admin auth"
    rationale: "Eliminates 4 duplicated inline checks and the 'System Manager' magic string from all endpoints"
  - id: "28-04-D2"
    decision: "getattr(user, 'role', None) in require_admin for backward compatibility with old tokens"
    rationale: "Tokens issued before this change lack the role claim; getattr prevents AttributeError"
  - id: "28-04-D3"
    decision: "redirect_slashes=True explicit on FastAPI app replaces dual route decorators"
    rationale: "FastAPI's built-in trailing slash redirect makes duplicate decorators unnecessary"
metrics:
  duration: "3m 2s"
  completed: "2026-02-11"
---

# Phase 28 Plan 04: Admin Auth Dependency and Input Validation Summary

**One-liner:** Reusable RequireAdmin dependency extracting "System Manager" to single constant, role field in JWT/TokenPayload, Path regex on player_id params, dual route decorators removed

## Performance

| Metric | Value |
|--------|-------|
| Tasks completed | 2/2 |
| Duration | 3m 2s |
| Deviations | 1 (auto-fixed missing import) |

## Accomplishments

### Task 1: Add role to TokenPayload and JWT, create require_admin dependency
- Added `role: str | None = None` field to `TokenPayload` model
- Updated `create_access_token` with optional `role` parameter (only included in JWT for admin users, keeping player tokens lean)
- Defined `ADMIN_ROLE = "System Manager"` constant in deps.py as single source of truth
- Created `require_admin` async dependency that raises HTTP 403 for non-admins
- Exported `RequireAdmin` type alias (`Annotated[TokenPayload, Depends(require_admin)]`) for clean endpoint injection

### Task 2: Update admin endpoints, add Path validation, remove dual decorators
- Replaced all 4 inline `if user.role != "System Manager"` checks across access.py (3) and wallet.py (1) with `RequireAdmin` dependency injection
- Added `Path(pattern=r"^[a-zA-Z0-9._@-]+$")` validation on `player_id` path parameters in both access.py and wallet.py
- Removed duplicate route decorators in wallet.py (`@router.get("/")` + `@router.get("")` collapsed to single `@router.get("")`)
- Added explicit `redirect_slashes=True` to FastAPI app constructor in main.py
- Cleaned up unused imports (`HTTPException`, `status` from wallet.py)

## Task Commits

| # | Hash | Message |
|---|------|---------|
| 1 | d26ebc6 | feat(28-04): add role to TokenPayload and JWT, create require_admin dependency |
| 2 | 2ffd9d0 | refactor(28-04): replace inline admin checks with RequireAdmin dependency, add Path validation |

## Files Modified

| File | Changes |
|------|---------|
| `fastapi_app/models/auth.py` | Added `role: str \| None = None` to TokenPayload |
| `fastapi_app/core/security.py` | Added `role` param to `create_access_token`, conditional JWT inclusion |
| `fastapi_app/api/deps.py` | Added `ADMIN_ROLE` constant, `require_admin` dependency, `RequireAdmin` alias |
| `fastapi_app/api/v1/endpoints/access.py` | 3 endpoints use `RequireAdmin`, `Path` validation on `player_id` |
| `fastapi_app/api/v1/endpoints/wallet.py` | `get_player_wallet` uses `RequireAdmin` + `Path`, dual decorators removed |
| `fastapi_app/main.py` | Added `redirect_slashes=True` to FastAPI constructor |

## Decisions Made

| ID | Decision | Rationale |
|----|----------|-----------|
| 28-04-D1 | ADMIN_ROLE constant + require_admin dependency in deps.py | Eliminates 4 duplicated inline checks and magic string from endpoints |
| 28-04-D2 | getattr(user, 'role', None) for backward compat with old tokens | Tokens without role claim won't raise AttributeError |
| 28-04-D3 | redirect_slashes=True replaces dual route decorators | FastAPI built-in handles trailing slash redirect |

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 3 - Blocking] Kept HTTPException import in access.py**
- **Found during:** Task 2
- **Issue:** Plan suggested removing HTTPException from access.py imports, but EMPTY_KEYS validation checks still use it
- **Fix:** Kept HTTPException in the import statement
- **Files modified:** fastapi_app/api/v1/endpoints/access.py

## Issues & Risks

None. All success criteria met.

## Next Phase Readiness

Phase 28 is now complete (4/4 plans). All tech debt items addressed:
- Plan 01: Interaction buffer LTRIM data loss fix
- Plan 02: Shared Redis constants, deps.py DRY consolidation
- Plan 03: Wallet service cleanup (dead code, Lua safety)
- Plan 04: Admin auth dependency, input validation, routing cleanup
