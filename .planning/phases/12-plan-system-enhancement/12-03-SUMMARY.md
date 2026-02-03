---
phase: 12-plan-system-enhancement
plan: 03
name: Plan JSON Serving Endpoint
subsystem: fastapi-api
tags: [fastapi, redis, caching, api, pydantic]

dependency-graph:
  requires: ["12-01"]
  provides: ["plan-manifest-endpoint", "plan-caching"]
  affects: ["12-04"]

tech-stack:
  added: []
  patterns: ["service-caching", "dependency-injection", "pydantic-models"]

file-tracking:
  created:
    - fastapi_app/models/plan.py
    - fastapi_app/services/plan.py
    - fastapi_app/api/v1/endpoints/plans.py
  modified:
    - fastapi_app/models/__init__.py
    - fastapi_app/api/deps.py
    - fastapi_app/api/v1/router.py

decisions: []

metrics:
  duration: 2m21s
  completed: 2026-02-03
---

# Phase 12 Plan 03: Plan JSON Serving Endpoint Summary

**One-liner:** FastAPI endpoint for Plan manifest JSON with 1hr Redis caching via PlanService

## What Was Done

### Task 1: Pydantic Models for Plan JSON
Created `fastapi_app/models/plan.py` with:
- `PlanSubject`: Subject entry with id, title, alias_title, image, total_lessons, total_tracks, is_premium, is_free_preview, hierarchy_url
- `PlanManifest`: Full manifest with schema_version, version, generated_at, plan_id, title, grade/major/season IDs, subjects list
- Exported from `fastapi_app/models/__init__.py`

### Task 2: PlanService with Redis Caching
Created `fastapi_app/services/plan.py` following HierarchyService pattern:
- `CACHE_TTL = 3600` (1 hour)
- `get_manifest()`: Redis cache check -> Frappe API fallback -> cache result
- `invalidate()`: Clear single plan manifest cache
- `invalidate_all()`: Batch clear via SCAN pattern matching

### Task 3: Plan API Endpoint
Created endpoint and wired to router:
- `GET /api/v1/plans/{plan_id}/manifest`
- Returns `PlanManifest` or 404 if not found
- Added `get_plan_service` dependency provider in deps.py
- Registered `plans.router` in v1/router.py

## Commits

| Commit | Type | Description |
|--------|------|-------------|
| 79499c4 | feat | Add Pydantic models for Plan manifest |
| 4351d28 | feat | Add PlanService with Redis caching |
| 6b20be8 | feat | Add Plan manifest API endpoint |

## Key Files

```
fastapi_app/
├── models/
│   ├── plan.py           # NEW: PlanManifest, PlanSubject models
│   └── __init__.py       # Updated: exports new models
├── services/
│   └── plan.py           # NEW: PlanService with caching
└── api/
    ├── deps.py           # Updated: get_plan_service, PlanServiceDep
    └── v1/
        ├── router.py     # Updated: includes plans router
        └── endpoints/
            └── plans.py  # NEW: /plans/{plan_id}/manifest endpoint
```

## Verification Results

1. **Pydantic models validate correctly:**
   - PlanManifest: 11 fields including nested subjects
   - PlanSubject: 9 fields including hierarchy_url

2. **PlanService works:**
   - Redis caching with 1hr TTL
   - Fallback to Frappe API on cache miss
   - invalidate() and invalidate_all() methods present

3. **Endpoint accessible:**
   - Registered at `/api/v1/plans/{plan_id}/manifest`
   - Returns 404 for non-existent plans
   - FastAPI starts without import errors

## Deviations from Plan

None - plan executed exactly as written.

## Next Phase Readiness

**Ready for 12-04:**
- Plan manifest endpoint operational
- Caching layer ready for integration
- `is_free_preview` field in PlanSubject ready for population
- Frappe API endpoint `memora_admin.api.plan.get_plan_manifest` needs implementation (Phase 12-02)
