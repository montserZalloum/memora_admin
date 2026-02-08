# Phase 21 Plan 01: Product Catalog API Summary

**One-liner:** GET /catalog endpoint with per-plan Redis cache (no TTL) and per-player purchased/pending exclusion filtering

## What Was Built

### Frappe Whitelisted API (`memora_admin/memora_admin/api/catalog.py`)
- `get_plan_catalog(plan_id)` assembles catalog from Product Grant + Item Price + Grant Component + Plan Subject
- Subject metadata falls back to subject title when Plan Subject entry is missing
- Skips grants with missing Item records (defensive)

### Pydantic Response Models (`fastapi_app/models/catalog.py`)
- `CatalogSubject`: subject_id, alias_title, notes
- `CatalogProduct`: product_grant_id, bundle_name, price, subjects[]
- `CatalogResponse`: products[]

### CatalogService (`fastapi_app/services/catalog.py`)
- Per-plan cache with NO TTL (infinite, event-driven invalidation only)
- `get_catalog()`: cache-first with Frappe fallback on miss
- `get_player_catalog()`: post-cache filtering via Redis pipeline (single round-trip)
  - Purchased: excludes products where ALL subjects are in player's access set
  - Pending: excludes products in player's pending set (Phase 22 will populate)
- `invalidate()`: deletes plan cache key for event-driven invalidation

### Endpoint (`fastapi_app/api/v1/endpoints/catalog.py`)
- `GET /api/v1/catalog/` with JWT auth protection
- No-plan players get empty 200 response
- Redis failures return 503 Service Unavailable (no fallback)

### Dependency Injection (`fastapi_app/api/deps.py`)
- `CatalogServiceDep` added following PlanService pattern exactly

### Router Registration (`fastapi_app/api/v1/router.py`)
- catalog.router included in v1 router

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 3 - Blocking] Fixed stale FrappeClientSettings import in main.py**
- **Found during:** Task 3 verification
- **Issue:** `main.py` imported `FrappeClientSettings` from `frappe_client.py` but the class was previously removed (documented in MEMORY.md). This prevented the FastAPI server from restarting with new code.
- **Fix:** Removed `FrappeClientSettings` import, simplified `FrappeClient()` instantiation to use default Settings
- **Files modified:** `fastapi_app/main.py`
- **Commit:** `75ff0d8`

## Decisions Made

| Decision | Rationale |
|----------|-----------|
| No TTL on catalog cache | Per CONTEXT.md: event-driven invalidation only, maximum performance |
| Post-cache filtering via smembers | Single pipeline round-trip for both access and pending sets |
| Check access set for "purchased" detection | Existing `memora:access:{player_id}` set already tracks granted subjects; avoids extra Frappe call |
| Empty pending set = show all products | Forward-compatible with Phase 22 which will populate pending set |
| Cache empty results too | Prevents repeated Frappe calls for plans with no published grants |

## Commits

| Task | Commit | Description |
|------|--------|-------------|
| 1 | `feaae4d` | Frappe catalog API and Pydantic models |
| 2 | `48ad099` | CatalogService with Redis cache and per-player filtering |
| 3 | `75ff0d8` | Catalog endpoint, deps, router, main.py fix |

## Verification Results

- GET /api/v1/catalog/ route registered and accessible
- Unauthenticated requests return 401
- All imports resolve cleanly (models, service, endpoint, deps)
- FastAPI server restarts cleanly with new code

## Next Phase Readiness

- Phase 22 (Purchase Submission) needs to populate `memora:pending:{player_id}` Redis set
- Phase 22 should implement cache invalidation hooks (pubsub + Frappe doc_events)
- No blockers for Phase 22

## Metrics

- **Duration:** ~3 minutes
- **Completed:** 2026-02-08
- **Tasks:** 3/3
