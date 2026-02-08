# Phase 21 Plan 02: Cache Invalidation Summary

**One-liner:** Event-driven catalog cache invalidation via Frappe doc_events + Redis pubsub to FastAPI CatalogService

## What Was Built

### Frappe Event Handler (`memora_admin/events/catalog_sync.py`)
- `on_product_grant_changed(doc, method)` handles after_insert, on_update, on_trash
- Two-pronged invalidation:
  1. Direct `r.delete(f"memora:catalog:{plan_id}")` for immediate cache clear
  2. `r.publish("memora:cache:invalidate", ...)` for FastAPI sidecar notification
- Reuses `get_fastapi_redis()` from `access_sync.py` (same Redis instance)

### Hooks Registration (`memora_admin/hooks.py`)
- Added `Memora Product Grant` to `doc_events` dict
- Triggers on after_insert, on_update, on_trash

### FastAPI Pubsub Handler (`fastapi_app/core/pubsub.py`)
- Added `"catalog"` message type handler in `_handle_invalidation()`
- Calls `catalog_service.invalidate(plan_id)` via `app.state.catalog_service`
- Follows exact same pattern as existing hierarchy/plan/profile handlers

### FastAPI Lifespan (`fastapi_app/main.py`)
- Creates `CatalogService` instance in lifespan (after ProfileService, before pubsub task)
- Stores on `app.state.catalog_service` for pubsub handler access

## Deviations from Plan

None - plan executed exactly as written.

## Decisions Made

| Decision | Rationale |
|----------|-----------|
| Two-pronged invalidation (delete + pubsub) | Direct delete ensures immediate cache clear even if pubsub has delay; pubsub ensures FastAPI in-process state also invalidates |
| No hooks for Product Bundle/Subject changes | Per plan: Product Grant is the primary invalidation trigger; lower-frequency changes can be added later if needed |
| Reuse get_fastapi_redis from access_sync | DRY: same Redis connection logic, same .env configuration |

## Commits

| Task | Commit | Description |
|------|--------|-------------|
| 1 | `ca6f687` | Frappe event hooks and doc_events registration |
| 2 | `d60ca7d` | FastAPI pubsub handler and lifespan CatalogService |

## Verification Results

- Import chain works: `memora_admin.events.catalog_sync.on_product_grant_changed` importable
- `hooks.py` has `Memora Product Grant` doc_events (after_insert, on_update, on_trash)
- `pubsub.py` has `"catalog"` handler calling `catalog_service.invalidate()`
- `main.py` creates CatalogService in lifespan and stores on `app.state`
- FastAPI server restarts cleanly and returns healthy status

## Next Phase Readiness

- Phase 21 complete: catalog API + cache invalidation pipeline fully wired
- Phase 22 (Purchase Submission) can proceed with no blockers
- Phase 22 needs to populate `memora:pending:{player_id}` Redis set

## Metrics

- **Duration:** ~2 minutes
- **Completed:** 2026-02-08
- **Tasks:** 2/2
