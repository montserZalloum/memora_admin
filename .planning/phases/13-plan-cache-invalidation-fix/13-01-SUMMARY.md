---
phase: 13
plan: 01
title: "Wire Plan Cache Invalidation"
subsystem: content-delivery
tags: [pubsub, cache-invalidation, plan-service, fastapi]

dependency-graph:
  requires: ["12-03", "12-04"]
  provides: ["plan-cache-invalidation-wiring"]
  affects: []

tech-stack:
  added: []
  patterns: ["getattr dispatch", "structured logging", "app.state service registration"]

key-files:
  created: []
  modified:
    - fastapi_app/main.py
    - fastapi_app/core/pubsub.py

decisions:
  - id: "plan-service-registration"
    title: "Register PlanService following HierarchyService pattern"
    rationale: "Consistency with existing service registration approach"
  - id: "elif-dispatch"
    title: "Add elif branch for plan messages (not separate if)"
    rationale: "Maintain proper dispatch flow, only one handler fires per message"

metrics:
  duration: "5m"
  completed: "2026-02-03"
---

# Phase 13 Plan 01: Wire Plan Cache Invalidation Summary

**One-liner:** PlanService registered in FastAPI lifespan, pubsub listener now dispatches `type="plan"` messages to PlanService.invalidate() for immediate cache clearing after build worker publishes.

## What Was Built

### 1. PlanService Registration (main.py)

Added PlanService import and instantiation following exact HierarchyService pattern:

```python
from fastapi_app.services.plan import PlanService

# Inside lifespan(), after HierarchyService:
plan_service = PlanService(
    redis_client=redis_client,
    frappe_client=frappe_client,
)
app.state.plan_service = plan_service
```

### 2. Plan Message Handler (pubsub.py)

Extended `_handle_invalidation()` to handle `type="plan"` messages:

```python
plan_id = payload.get("plan_id")

# ... existing hierarchy handler ...

elif msg_type == "plan" and plan_id:
    plan_service = getattr(app_state, "plan_service", None)

    if plan_service:
        await plan_service.invalidate(plan_id)
        logger.info(
            "plan_cache_invalidated",
            plan_id=plan_id,
            timestamp=timestamp,
        )
    else:
        logger.warning(
            "plan_service_not_available",
            plan_id=plan_id,
        )
```

## Integration Flow

```
Build worker completes Plan JSON generation
    |
    v
Publishes to Redis: memora:cache:invalidate {type: "plan", plan_id: "..."}
    |
    v
FastAPI pubsub listener receives message
    |
    v
_handle_invalidation() extracts type="plan"
    |
    v
Dispatches to app.state.plan_service.invalidate(plan_id)
    |
    v
Redis key memora:plan:{plan_id}:manifest deleted
    |
    v
Next request triggers fresh fetch from Frappe API
```

## Gap Closure

This plan closes the integration gap identified in v1.2 milestone audit:

| Before | After |
|--------|-------|
| Build worker publishes `type="plan"` messages | Same |
| FastAPI logs "unknown_invalidation_message" | FastAPI calls PlanService.invalidate() |
| Plan cache serves stale data until 1hr TTL | Plan cache clears within seconds of rebuild |

## Files Changed

| File | Change |
|------|--------|
| `fastapi_app/main.py` | +8 lines - PlanService import and registration |
| `fastapi_app/core/pubsub.py` | +17 lines - plan_id extraction and elif handler |

## Deviations from Plan

None - plan executed exactly as written.

## Verification Results

1. **PlanService import** - Verified `from fastapi_app.services.plan import PlanService` on line 19
2. **app.state registration** - Verified `app.state.plan_service = plan_service` on line 57
3. **plan_id extraction** - Verified `plan_id = payload.get("plan_id")` on line 89
4. **elif handler** - Verified `elif msg_type == "plan" and plan_id:` on line 108
5. **invalidate call** - Verified `await plan_service.invalidate(plan_id)` on line 113
6. **structured logging** - Verified `plan_cache_invalidated` log with plan_id and timestamp

## Phase 13 Complete

This plan completes Phase 13 (Plan Cache Invalidation Fix):

| Plan | Title | Status |
|------|-------|--------|
| 13-01 | Wire Plan Cache Invalidation | Complete |

**v1.2.1 Gap Closure Complete** - Plan cache invalidation now fully wired.

## Commits

- `f570d5a`: feat(13-01): register PlanService in FastAPI lifespan
- `2f2f03c`: feat(13-01): add Plan message handler in pubsub invalidation dispatch
