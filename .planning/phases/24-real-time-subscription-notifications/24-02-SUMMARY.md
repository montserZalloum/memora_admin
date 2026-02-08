---
phase: 24-real-time-subscription-notifications
plan: 02
subsystem: api
tags: [websocket, redis-pubsub, notifications, real-time, sse-removal]

# Dependency graph
requires:
  - phase: 24-01
    provides: ConnectionManager, notification models, Frappe-side pub/sub publish
provides:
  - WebSocket endpoint at /api/v1/notifications/ws with JWT auth
  - Notification pub/sub listener for per-user channel relay
  - ConnectionManager lifespan integration
  - SSE endpoint removal
affects: []

# Tech tracking
tech-stack:
  added: []
  removed: [sse-starlette]
  patterns:
    - "Dynamic per-user Redis pub/sub subscribe/unsubscribe tied to WebSocket lifecycle"
    - "Notification listener as separate asyncio background task from cache invalidation listener"
    - "JWT authentication before WebSocket accept (reject at HTTP layer for invalid tokens)"

key-files:
  created:
    - fastapi_app/api/v1/endpoints/notifications.py
  modified:
    - fastapi_app/api/v1/router.py
    - fastapi_app/core/pubsub.py
    - fastapi_app/main.py
    - fastapi_app/api/v1/endpoints/progress.py
    - requirements.txt

key-decisions:
  - "Notification pub/sub listener is separate from cache invalidation listener because channels are dynamic (per-user) vs static"
  - "JWT auth runs before WebSocket accept; Starlette translates close-before-accept to HTTP 403 rejection"
  - "notify_pubsub object stored on app.state so WebSocket endpoint can subscribe/unsubscribe dynamically"

patterns-established:
  - "Per-user pub/sub channel pattern: memora:notify:{user_id}"
  - "First connect subscribes, last disconnect unsubscribes (lifecycle managed by ConnectionManager booleans)"
  - "Background task shutdown: cancel + await CancelledError for clean cleanup"

# Metrics
duration: 4min
completed: 2026-02-08
---

# Phase 24 Plan 02: WebSocket Endpoint + Pub/Sub Integration Summary

**WebSocket notification endpoint with JWT auth, Redis pub/sub per-user relay, lifespan integration, and SSE removal**

## Performance

- **Duration:** 4 min
- **Started:** 2026-02-08T15:46:41Z
- **Completed:** 2026-02-08T15:50:25Z
- **Tasks:** 2/2
- **Files modified:** 6 (1 created, 5 modified)

## Accomplishments

- WebSocket endpoint at `/api/v1/notifications/ws` authenticates via JWT query parameter before accepting connection
- Invalid/expired tokens are rejected immediately (HTTP 403 before WebSocket upgrade)
- Notification pub/sub listener runs as dedicated background task, dynamically subscribing to per-user channels
- ConnectionManager initialized in FastAPI lifespan and stored on `app.state.ws_manager`
- `notify_pubsub` object stored on `app.state` for WebSocket endpoint to call subscribe/unsubscribe
- When a message arrives on `memora:notify:{user_id}`, it is forwarded to all user's WebSocket connections via `ConnectionManager.send_to_user()`
- Deprecated SSE endpoint `/progress/stream/{subject}` completely removed
- `sse-starlette` dependency removed from requirements.txt
- All 6 remaining progress endpoints unaffected

## Task Commits

Each task was committed atomically:

1. **Task 1: Create WebSocket endpoint, wire pubsub notification handler, and integrate with lifespan** - `aa4f3be` (feat)
2. **Task 2: Remove deprecated SSE endpoint and sse-starlette dependency** - `f74c522` (feat)

## Files Created/Modified

- `fastapi_app/api/v1/endpoints/notifications.py` - WebSocket endpoint with JWT auth, pub/sub subscribe/unsubscribe lifecycle
- `fastapi_app/api/v1/router.py` - Notifications router wired into v1 API
- `fastapi_app/core/pubsub.py` - `start_notification_listener` and `_handle_notification` for per-user channel relay
- `fastapi_app/main.py` - ConnectionManager + notification listener initialization in lifespan with clean shutdown
- `fastapi_app/api/v1/endpoints/progress.py` - SSE endpoint, json import, sse-starlette import, Request import removed
- `requirements.txt` - sse-starlette>=2.0.0 removed

## Decisions Made

- **Separate notification listener from cache invalidation listener:** The cache listener uses a single static channel while notification channels are dynamic per-user. Separate asyncio tasks allow independent lifecycle management.
- **JWT auth before WebSocket accept:** Calling `websocket.close()` before `accept()` causes Starlette to reject at the HTTP layer (403), which is more secure than accepting then closing with 1008.
- **notify_pubsub on app.state:** The pubsub object must be accessible from both the background listener task and the WebSocket endpoint handler, so it lives on `app.state`.

## Deviations from Plan

None - plan executed exactly as written.

## Issues Encountered

None.

## User Setup Required

None - no external service configuration required.

## Next Phase Readiness

- Phase 24 is complete: all 2 plans executed
- End-to-end flow works: Frappe approval publishes to Redis -> notification listener receives -> forwards to WebSocket clients
- System ready for production: ConnectionManager handles multi-device, pub/sub handles multi-instance
- No blockers

---
*Phase: 24-real-time-subscription-notifications*
*Completed: 2026-02-08*
