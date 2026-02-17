# Research: Remaining Endpoint Tests

**Feature**: 014-remaining-endpoint-tests
**Date**: 2026-02-17

## Research Questions & Findings

### RQ-1: What testing patterns are established by Phase 5 endpoint tests?

**Decision**: Follow the exact Phase 5 patterns -- class-based async tests, fixture tuple unpacking, `patch()` at endpoint module level for FrappeClient, conftest helper functions for Redis seeding.

**Rationale**: Consistency across all 6+ test phases. The patterns are proven (all Phase 5 tests pass) and well-documented in conftest.py.

**Key Patterns Confirmed**:
1. Class per endpoint group: `class TestCatalogEndpoints:`
2. Global pytestmark: `pytestmark = pytest.mark.asyncio`
3. Fixture unpacking: `client, token, player_id, family_id = authed_client`
4. Mock patching: `patch("fastapi_app.api.v1.endpoints.catalog.get_frappe_client")`
5. Response validation: status code + JSON body assertions
6. Cleanup: try-finally + `cleanup_player_keys()` helper
7. Redis seeding: conftest helpers (`seed_wallet`, `seed_hierarchy`, etc.)

### RQ-2: What are the exact route paths and prefixes for Phase 6 endpoints?

**Decision**: Use the exact paths from `router.py` prefix registration.

| Endpoint Group | Route Prefix | Key Routes |
|---------------|-------------|------------|
| Catalog | `/api/v1/catalog` | `GET /` |
| Purchase | `/api/v1/purchase` | `POST /` |
| Plans | `/api/v1/plans` | `GET /{plan_id}/manifest` |
| Profile | `/api/v1/profile` | `GET /`, `GET /stats`, `GET /mastery`, `GET /activity`, `PUT /avatar`, `POST /logout` |
| Leaderboard | `/api/v1/leaderboard` | `GET /{lb_type}`, `GET /{lb_type}/me` |
| Reviews | `/api/v1/reviews` | `GET /`, `GET /{subject}`, `POST /{subject}/submit` |
| Settings | `/api/v1/settings` | `GET /gamification` |
| Subscriptions | `/api/v1/subscriptions` | `GET /` |
| Voucher | `/api/v1/voucher` | `POST /preview`, `POST /redeem` |
| Webhooks | `/api/v1/webhooks` | `POST /payment` |
| Notifications | `/api/v1/notifications` | `WS /ws` |

### RQ-3: Which endpoints require authentication and which are public?

**Decision**: Three authentication tiers identified.

| Tier | Endpoints | Fixture to Use |
|------|-----------|---------------|
| **Public (no auth)** | Plans manifest, Settings gamification, Webhooks payment | `app_client` |
| **Player auth (JWT)** | Catalog, Purchase, Profile (all 6 routes), Leaderboard, Reviews, Subscriptions, Voucher, Notifications WS | `authed_client` |
| **Admin auth** | None in Phase 6 (admin endpoints covered in Phase 5) | N/A |

### RQ-4: How should WebSocket notification tests be structured?

**Decision**: Use `httpx.AsyncClient` with ASGI WebSocket support via `TestClient` from Starlette, or use the `websocket_connect` method on the httpx client.

**Rationale**: The notification endpoint uses standard WebSocket protocol. The FastAPI TestClient supports WebSocket testing natively.

**Approach**:
1. Use `httpx.ASGITransport` + manual WebSocket connection
2. Authentication via `?token=jwt_string` query parameter
3. Invalid token test: expect WS close code 1008
4. Message receipt test: publish to Redis pub/sub channel, assert client receives

**Alternatives Considered**:
- `websockets` library: Additional dependency, not needed
- Skip WebSocket tests: Not acceptable per spec FR-012

### RQ-5: How to test the webhook idempotency mechanism?

**Decision**: Send the same `event_id` twice. First call returns "accepted", second returns "already_processed".

**Rationale**: The webhook endpoint tracks event_ids in Redis with 24h TTL. Testing idempotency requires two sequential POST calls with the same payload.

**Key Detail**: The webhook uses `BackgroundTasks` for processing. In tests, background tasks run synchronously within the ASGI transport, so the idempotency key will be set before the second request.

### RQ-6: How to test voucher rate limiting without hitting real limits?

**Decision**: Pre-seed the Redis rate limit counter to just below the limit, then make one request to trigger 429.

**Rationale**: The voucher redeem endpoint checks `voucher_service.check_rate_limit()` which reads counters from Redis keys `memora:voucher_fail:player:{id}` and `memora:voucher_fail:ip:{ip}`. Pre-seeding these counters avoids needing 5+ sequential requests.

**Implementation**:
```
# Seed counter at limit
await redis_client.set(f"memora:voucher_fail:player:{player_id}", "5", ex=3600)
# Next redeem attempt → 429
```

### RQ-7: How to handle profile endpoint's 6 routes in one test file?

**Decision**: Organize as multiple test classes within `test_profile_endpoints.py`, one per route group.

**Rationale**: Profile has 6 routes (hero, stats, mastery, activity, avatar update, logout). Using one class per route keeps the file organized without splitting into multiple files (the test plan specifies one file).

**Structure**:
- `TestProfileHero` (1 test: get hero)
- `TestProfileStats` (1 test: get stats)
- `TestProfileAvatar` (2 tests: update success, invalid avatar)
- `TestProfileLogout` (1 test: logout success)
- `TestProfileAuth` (1 test: unauthenticated → 401)

### RQ-8: What mock return values are needed for each service?

**Decision**: Document the minimum mock return structure per endpoint.

| Endpoint | Service Method | Mock Return Value |
|----------|---------------|-------------------|
| Catalog GET | `catalog_service.get_player_catalog()` | `{"products": [{"product_grant_id": "...", "bundle_name": "...", "price": 100, "subjects": []}]}` |
| Purchase POST | `purchase_service.submit_purchase()` | `{"message": "Purchase request submitted successfully"}` |
| Plans GET | `plan_service.get_manifest()` | `{"schema_version": 1, "plan_id": "...", "title": "...", "subjects": [...]}` |
| Profile GET | `profile_page_service.get_hero()` | `{"display_name": "...", "avatar": "...", "level": 1, ...}` |
| Leaderboard GET | `leaderboard_service.get_top()` | `[{"player_id": "...", "xp": 100}]` |
| Reviews GET / | `review_service.get_overview()` | `{"subjects": [{"subject_id": "...", "due_count": 5}]}` |
| Reviews GET /{s} | `review_service.get_due_items()` | `{"items": [...], "has_more": false}` |
| Reviews POST | `review_service.submit_reviews()` | `{"processed": 3, "remaining_due": 2}` |
| Settings GET | `settings_service.get_gamification_settings()` | `{"base_lesson_xp": 100, "replay_xp": 25, ...}` |
| Subscriptions GET | `access_service.get_player_grants()` | `["SUB-MATH", "TRK-SCI"]` |
| Voucher preview | `voucher_service.preview()` | `{"face_value": "50 EGP", "grants": [...]}` |
| Voucher redeem | `voucher_service.redeem()` | `{"status": "success", "transaction_id": "TXN-001"}` |
| Webhook POST | FrappeClient.get_grant_keys() | `["SUB-MATH"]` |
| Notifications WS | ConnectionManager + pubsub | Connection established, message received |

**Note**: These are mock patterns for service-level mocking. The actual endpoint handler code processes these values and builds HTTP responses. Tests validate the HTTP response shape, not the service internals.
