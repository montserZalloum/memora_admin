---
phase: 03-access-control
plan: 03
subsystem: access-control
tags: [fastapi, dependencies, gate-pattern, access-validation, redis]

dependency-graph:
  requires:
    - phase: 03-01
      provides: SeasonMeta model, SeasonService for Gate 1
    - phase: 03-02
      provides: AccessService for Gate 2 access checks
  provides:
    - Double-Gate FastAPI dependencies
    - ContentAccessRequest model for route-level access bundling
    - require_season_access (Gate 1) dependency
    - require_content_access (Gate 2) dependency with free bypass
    - SeasonServiceDep and AccessServiceDep type aliases
  affects: [03-04-webhooks, content-endpoints, future-protected-routes]

tech-stack:
  added: []
  patterns: [double-gate-access-control, fastapi-dependency-injection, free-content-bypass]

key-files:
  created: []
  modified:
    - fastapi_app/models/access.py
    - fastapi_app/api/deps.py

key-decisions:
  - "Free content bypass checked FIRST in Gate 2 per RESEARCH.md pitfall #3"
  - "Structured error detail with code/message for 403 responses"
  - "SeasonMeta.from_redis_hash classmethod for cleaner Redis parsing"

patterns-established:
  - "Gate 1 (require_season_access): Season validation with three checks (exists, published, not expired)"
  - "Gate 2 (require_content_access): Player grant check with free content bypass"
  - "Double-Gate composition: Gates can be used individually or combined via require_double_gate"

duration: 2min
completed: 2026-02-02
---

# Phase 03 Plan 03: Double-Gate Dependencies Summary

**Double-Gate FastAPI dependencies for content access control with free content bypass and structured error responses**

## Performance

- **Duration:** 2 min
- **Started:** 2026-02-02T07:56:00Z
- **Completed:** 2026-02-02T07:58:00Z
- **Tasks:** 2
- **Files modified:** 2

## Accomplishments

- ContentAccessRequest model bundles season_id, content_key, is_free for route-level access
- Gate 1 (require_season_access) validates season exists, is published, not expired
- Gate 2 (require_content_access) validates player grants with free content bypass
- Combined require_double_gate dependency for protected content routes
- SeasonMeta.from_redis_hash classmethod for cleaner Redis hash parsing

## Task Commits

Each task was committed atomically:

1. **Task 1: Add ContentAccessRequest model** - `f1ca3c5` (feat)
2. **Task 2: Create Double-Gate FastAPI dependencies** - `cb1a9e9` (feat)

## Files Created/Modified

- `fastapi_app/models/access.py` - Added ContentAccessRequest, AccessDeniedDetail, SeasonMeta.from_redis_hash
- `fastapi_app/api/deps.py` - Added service deps (SeasonServiceDep, AccessServiceDep) and gate deps (require_season_access, require_content_access, require_double_gate)

## Implementation Details

### Gate 1: require_season_access

Validates season status with structured 403 responses:
- SEASON_NOT_FOUND: Season not in Redis cache
- SEASON_INACTIVE: Season exists but not published
- SEASON_EXPIRED: Season end_date has passed

```python
async def require_season_access(
    season_id: str,
    season_service: SeasonServiceDep,
) -> SeasonMeta:
```

### Gate 2: require_content_access

Validates player grants with free content bypass:

```python
async def require_content_access(
    content: ContentAccessRequest,
    user: CurrentUser,
    access_service: AccessServiceDep,
) -> bool:
    # Check free content FIRST
    if content.is_free:
        return True
    # ... check access via AccessService
```

### ContentAccessRequest Model

Bundles access check parameters for clean dependency injection:

```python
class ContentAccessRequest(BaseModel):
    season_id: str           # Season the content belongs to
    content_key: str         # Access key (e.g., "SUB-MATH")
    is_free: bool = False    # If true, bypasses Gate 2
```

## Decisions Made

| Decision | Choice | Rationale |
|----------|--------|-----------|
| Free bypass order | Check is_free FIRST before access service call | Per RESEARCH.md pitfall #3 - avoids unnecessary Redis lookup |
| Error response structure | {code, message} dict | Enables frontend to handle specific error types |
| from_redis_hash location | Classmethod on SeasonMeta | Collocates parsing logic with model; cleaner than service method |

## Deviations from Plan

None - plan executed exactly as written.

## Issues Encountered

None.

## Success Criteria Met

- [x] ACCESS-01: Gate 1 rejects inactive/expired seasons with 403
- [x] ACCESS-02: Gate 2 rejects missing grants with 403
- [x] ACCESS-03: Free content (is_free=true) bypasses Gate 2
- [x] Dependencies are composable and testable
- [x] Error responses use structured format with code and message

## Next Phase Readiness

Ready for 03-04 (Webhooks and admin endpoints). This plan provides:
- Double-Gate dependencies for protected content routes
- ContentAccessRequest model for bundling access parameters
- Structured error responses for frontend handling

No blockers identified.

---
*Phase: 03-access-control*
*Completed: 2026-02-02*
