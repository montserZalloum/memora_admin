---
phase: 03
plan: 04
subsystem: access-control
tags: [webhooks, admin-api, redis, background-tasks, idempotency]
dependency-graph:
  requires: [03-01, 03-02]
  provides: [payment-webhook, admin-grant-api, retry-queue]
  affects: [07-integration]
tech-stack:
  added: []
  patterns: [background-tasks, idempotency-keys, retry-queue]
key-files:
  created:
    - fastapi_app/api/v1/endpoints/access.py
    - fastapi_app/api/v1/endpoints/webhooks.py
  modified:
    - fastapi_app/models/access.py
    - fastapi_app/api/v1/router.py
decisions:
  - key: webhook-idempotency
    choice: Redis key with 24h TTL tracking processing/completed state
    rationale: Prevents duplicate processing; 24h TTL sufficient for payment provider retry windows
  - key: background-processing
    choice: FastAPI BackgroundTasks for webhook processing
    rationale: Fast acknowledgment to payment provider; processing continues async
  - key: retry-queue
    choice: Redis list for failed webhook payloads
    rationale: Durable queue for failure recovery; processed by scheduled task
metrics:
  duration: 3min
  completed: 2026-02-02
---

# Phase 03 Plan 04: Webhook and Grant Endpoints Summary

**One-liner:** Payment webhook with idempotency and background processing, admin grant/revoke endpoints with role-based access control

## What Was Built

### Admin Grant Endpoints (fastapi_app/api/v1/endpoints/access.py)

Admin-only endpoints for access management:

- **POST /api/v1/access/grants** - Grant access to content keys
- **DELETE /api/v1/access/grants** - Revoke access from content keys
- **GET /api/v1/access/grants/{player_id}** - List player's current grants

All endpoints:
- Require System Manager role (403 ADMIN_REQUIRED otherwise)
- Validate content_keys not empty (400 EMPTY_KEYS otherwise)
- Log operations with structlog for audit trail

### Payment Webhook Endpoint (fastapi_app/api/v1/endpoints/webhooks.py)

Provider-agnostic webhook for payment completion:

- **POST /api/v1/webhooks/payment** - Receive and process payment webhooks

Features:
- Idempotency via event_id tracking (Redis key with 24h TTL)
- Background processing for fast acknowledgment
- Retry queue for failed Redis writes

### Webhook Models (fastapi_app/models/access.py)

New models added:

```python
class WebhookPayload(BaseModel):
    event_id: str           # Unique event ID for idempotency
    event_type: str         # e.g., "payment.completed"
    transaction_id: str     # Payment provider's transaction ID
    player_id: str          # Memora player ID (user_id)
    product_grant_id: str   # Memora Product Grant DocType name
    amount: float
    currency: str
    timestamp: str          # ISO format timestamp

class WebhookResponse(BaseModel):
    status: str  # "accepted", "already_processed", "error"
    message: str | None = None

class GrantRequest(BaseModel):
    player_id: str
    content_keys: list[str]

class GrantResponse(BaseModel):
    granted: int
    message: str

class RevokeRequest(BaseModel):
    player_id: str
    content_keys: list[str]

class RevokeResponse(BaseModel):
    revoked: int
    message: str
```

## Implementation Notes

### Webhook Idempotency Flow

1. Receive webhook with event_id
2. Check Redis key `memora:webhook:{event_id}`
3. If "completed" or "processing" -> return "already_processed"
4. Set key to "processing" with 24h TTL
5. Queue background task for processing
6. Return "accepted" immediately
7. Background task: grant access, set key to "completed"

### Retry Queue

Failed webhook processing adds payload to `memora:webhook:retry_queue` (Redis list).
`process_retry_queue()` function provided for scheduled task integration (Phase 7).

### Stubbed Functionality

Webhook processing currently stubs:
- Frappe API call to get grant keys from product_grant_id
- Frappe API call to create Memora Player Subscription

These will be implemented in Phase 7 (Integration) when Frappe whitelisted methods are available.

## Decisions Made

| Decision | Choice | Rationale |
|----------|--------|-----------|
| Idempotency key TTL | 24 hours | Sufficient for payment provider retry windows |
| Background processing | FastAPI BackgroundTasks | Simple, built-in; no external queue needed |
| Retry queue | Redis list | Durable, simple FIFO for failure recovery |
| Admin role check | "System Manager" string match | Matches Frappe role system |

## Deviations from Plan

None - plan executed exactly as written.

## Success Criteria Met

- [x] ACCESS-04: Payment webhook grants access via SADD and queues subscription creation
- [x] ACCESS-05: Admin can grant/revoke access via FastAPI endpoints
- [x] Webhook is idempotent (duplicate events return "already_processed")
- [x] Failed Redis writes queued for retry
- [x] Fast webhook acknowledgment via background processing
- [x] All endpoints use structured logging

## Verification Results

```bash
# All endpoints import correctly
python3 -c "from fastapi_app.api.v1.endpoints.access import router; ..."
# access router imported

python3 -c "from fastapi_app.api.v1.endpoints.webhooks import router; ..."
# webhooks router imported

# Router includes all endpoints
grep -E "(access|webhooks)" fastapi_app/api/v1/router.py
# router.include_router(access.router)
# router.include_router(webhooks.router)

# Webhook payload validates correctly
python3 -c "from fastapi_app.models.access import WebhookPayload; ..."
# Webhook payload validates correctly
```

## Artifacts

| File | Purpose | Exports |
|------|---------|---------|
| fastapi_app/api/v1/endpoints/access.py | Admin grant/revoke endpoints | router, create_grant, revoke_grant, get_player_grants |
| fastapi_app/api/v1/endpoints/webhooks.py | Payment webhook endpoint | router, payment_webhook, process_retry_queue |
| fastapi_app/models/access.py | Webhook and grant models | WebhookPayload, WebhookResponse, GrantRequest, GrantResponse, RevokeRequest, RevokeResponse |

## Commit Log

| Hash | Type | Description |
|------|------|-------------|
| 48646c3 | feat | Add webhook and grant models |
| 9e75eea | feat | Add admin grant/revoke endpoints |
| 66bd4a8 | feat | Add payment webhook endpoint |

## Next Phase Readiness

Ready for 03-05 (if exists) or Phase 4. This plan provides:
- Admin endpoints for manual access management
- Webhook endpoint for automated payment processing
- Retry infrastructure for failure recovery
- All endpoints integrated with router

No blockers identified.
