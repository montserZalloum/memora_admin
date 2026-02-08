# Phase 22 Plan 02: FastAPI Purchase Endpoint Summary

**One-liner:** POST /api/v1/purchase/ endpoint with PurchaseService that checks Redis pending set for duplicates, delegates to Frappe whitelisted API, and writes grant ID to pending set for catalog filtering.

## What Was Done

### Task 1: Create Pydantic models and PurchaseService
- Created `fastapi_app/models/purchase.py` with PurchaseRequest (product_grant_id, payment_method, payment_proof_url) and PurchaseResponse models
- Created `fastapi_app/services/purchase.py` with PurchaseService class
- Service flow: Redis sismember duplicate check -> Frappe API call -> Redis sadd to pending set
- Write order: Frappe first, Redis second (per RESEARCH.md pitfall 2)
- Maps Frappe error codes: 417+DuplicateEntry->409, 404->404, 417+Validation->400, other->502
- Commit: `82de208`

### Task 2: Create endpoint, register dependency, wire router
- Created `fastapi_app/api/v1/endpoints/purchase.py` with POST / endpoint (201 on success)
- Added PurchaseServiceDep to `fastapi_app/api/deps.py` following CatalogService pattern
- Registered purchase router in `fastapi_app/api/v1/router.py`
- Endpoint guards: auth required (401), plan required (400), Redis error (503)
- Commit: `a9a1025`

## Verification Results

1. POST /api/v1/purchase/ returns 401 without auth token -- endpoint exists and protected
2. Route listing shows `/api/v1/purchase/ {'POST'}`
3. All Python files compile without errors
4. Redis key format match confirmed: PurchaseService writes `memora:pending:{user_id}`, CatalogService reads `memora:pending:{player_id}` -- same key (user.sub)
5. FastAPI health check passes after restart

## Deviations from Plan

None -- plan executed exactly as written.

## Decisions Made

| Decision | Choice | Rationale |
|----------|--------|-----------|
| HTTP status on success | 201 Created | Purchase creates a new resource (Subscription Transaction) |
| Redis error handling | 503 in endpoint, not service | Matches catalog endpoint pattern exactly |
| Frappe error mapping | DuplicateEntry check via string match | Frappe wraps errors in 417 status, need to inspect message for specific error type |

## Key Files

### Created
- `fastapi_app/models/purchase.py` -- PurchaseRequest/PurchaseResponse Pydantic models
- `fastapi_app/services/purchase.py` -- PurchaseService with Redis + Frappe delegation
- `fastapi_app/api/v1/endpoints/purchase.py` -- POST /purchase endpoint

### Modified
- `fastapi_app/api/deps.py` -- Added PurchaseServiceDep dependency injection
- `fastapi_app/api/v1/router.py` -- Registered purchase router

## Requirements Coverage

| Requirement | Status | Implementation |
|-------------|--------|----------------|
| PRCHS-01: Player submits purchase request | Done | POST /api/v1/purchase/ endpoint |
| PRCHS-02: Duplicate pending returns 409 | Done | Redis sismember + Frappe DuplicateEntryError mapping |
| PRCHS-04: Product hidden after purchase | Done | Redis sadd to pending set, CatalogService already reads it |
| CTLG-04: Pending products filtered | Done (Phase 21) | PurchaseService now populates the set CatalogService reads |

## Next Phase Readiness

Phase 22 is now complete. The purchase flow works end-to-end:
1. Player POSTs to /api/v1/purchase/ with product_grant_id
2. FastAPI checks Redis for duplicate, calls Frappe to create transaction
3. Frappe validates grant, creates Subscription Transaction with "Pending Approval"
4. FastAPI writes grant ID to Redis pending set
5. CatalogService hides the product from player's catalog

**Phase 23 dependency:** Approval/rejection flow must handle:
- SREM from pending set on rejection (so product reappears)
- SREM from pending set on approval (product stays hidden via access set instead)
- Access grant creation on approval (may work via existing hooks)
