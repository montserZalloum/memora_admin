# Endpoint Test Contracts

**Feature**: 014-remaining-endpoint-tests
**Date**: 2026-02-17

## Overview

Each contract defines: the HTTP request, expected response, and mock setup. These contracts serve as the blueprint for test implementation.

---

## 1. Catalog Endpoints (`test_catalog_endpoints.py`)

### Contract 1.1: GET /api/v1/catalog/ (authenticated)
- **Request**: `GET /api/v1/catalog/` with Bearer token
- **Mock**: `CatalogService.get_player_catalog()` returns `CatalogResponse(products=[...])`
- **Expected**: 200, body contains `products` array

### Contract 1.2: GET /api/v1/catalog/ (empty catalog)
- **Request**: `GET /api/v1/catalog/` with Bearer token (player has no plan)
- **Mock**: `CatalogService.get_player_catalog()` returns `CatalogResponse(products=[])`
- **Expected**: 200, `products` is empty array

### Contract 1.3: GET /api/v1/catalog/ (unauthenticated)
- **Request**: `GET /api/v1/catalog/` without auth header
- **Expected**: 401

---

## 2. Purchase Endpoints (`test_purchase_endpoints.py`)

### Contract 2.1: POST /api/v1/purchase/ (success)
- **Request**: `POST /api/v1/purchase/` with `{product_grant_id, payment_method}`
- **Mock**: `PurchaseService.submit_purchase()` succeeds
- **Expected**: 201, `message` field present

### Contract 2.2: POST /api/v1/purchase/ (duplicate)
- **Request**: Same product_grant_id submitted twice
- **Mock**: `PurchaseService.submit_purchase()` raises 409
- **Expected**: 409

### Contract 2.3: POST /api/v1/purchase/ (unauthenticated)
- **Request**: `POST /api/v1/purchase/` without auth
- **Expected**: 401

### Contract 2.4: POST /api/v1/purchase/ (invalid payload)
- **Request**: `POST /api/v1/purchase/` with empty/missing fields
- **Expected**: 422

---

## 3. Plans Endpoints (`test_plans_endpoints.py`)

### Contract 3.1: GET /api/v1/plans/{plan_id}/manifest (success)
- **Request**: `GET /api/v1/plans/PLAN-001/manifest` (no auth needed)
- **Mock**: `PlanService.get_manifest()` returns `PlanManifest`
- **Expected**: 200, body contains `plan_id`, `subjects` array

### Contract 3.2: GET /api/v1/plans/{plan_id}/manifest (not found)
- **Request**: `GET /api/v1/plans/NONEXISTENT/manifest`
- **Mock**: `PlanService.get_manifest()` returns None / raises 404
- **Expected**: 404

### Contract 3.3: GET /api/v1/plans/{plan_id}/manifest (no auth needed)
- **Request**: `GET /api/v1/plans/PLAN-001/manifest` without Bearer token
- **Mock**: Same as 3.1
- **Expected**: 200 (public endpoint, not 401)

---

## 4. Profile Endpoints (`test_profile_endpoints.py`)

### Contract 4.1: GET /api/v1/profile/ (hero section)
- **Request**: `GET /api/v1/profile/` with Bearer token
- **Mock**: `ProfilePageService.get_hero()` returns hero data
- **Expected**: 200, body contains `display_name`, `avatar`, `level`, `current_xp`

### Contract 4.2: GET /api/v1/profile/stats
- **Request**: `GET /api/v1/profile/stats` with Bearer token
- **Mock**: `ProfilePageService.get_stats()` returns stats data
- **Expected**: 200, body contains `streak`, `items_learned`, `total_xp`

### Contract 4.3: PUT /api/v1/profile/avatar (success)
- **Request**: `PUT /api/v1/profile/avatar` with `{avatar: "avatar_02"}`
- **Mock**: `ProfilePageService.update_avatar()` succeeds
- **Expected**: 200, body contains `avatar`, `success: true`

### Contract 4.4: PUT /api/v1/profile/avatar (invalid)
- **Request**: `PUT /api/v1/profile/avatar` with `{avatar: "INVALID"}`
- **Mock**: `ProfilePageService.update_avatar()` raises ValueError/400
- **Expected**: 400

### Contract 4.5: POST /api/v1/profile/logout
- **Request**: `POST /api/v1/profile/logout` with Bearer token
- **Mock**: `ProfilePageService.logout()` succeeds
- **Expected**: 200, body contains `success: true`

### Contract 4.6: GET /api/v1/profile/ (unauthenticated)
- **Request**: `GET /api/v1/profile/` without auth
- **Expected**: 401

---

## 5. Leaderboard Endpoints (`test_leaderboard_endpoints.py`)

### Contract 5.1: GET /api/v1/leaderboard/daily (top list)
- **Request**: `GET /api/v1/leaderboard/daily?limit=10` with Bearer token
- **Mock**: `LeaderboardService.get_top()` returns ranked entries
- **Expected**: 200, body contains `entries` array, `total_players`

### Contract 5.2: GET /api/v1/leaderboard/daily/me (my rank)
- **Request**: `GET /api/v1/leaderboard/daily/me` with Bearer token
- **Mock**: `LeaderboardService.get_my_rank()` returns rank + neighbors
- **Expected**: 200, body contains `rank`, `xp`, `neighbors`

### Contract 5.3: GET /api/v1/leaderboard/daily (empty)
- **Request**: `GET /api/v1/leaderboard/daily` with Bearer token
- **Mock**: `LeaderboardService.get_top()` returns empty list
- **Expected**: 200, `entries` is empty array

### Contract 5.4: GET /api/v1/leaderboard/{invalid_type}
- **Request**: `GET /api/v1/leaderboard/invalid_type`
- **Expected**: 422 (path param validation fails for non-Literal type)

### Contract 5.5: GET /api/v1/leaderboard/daily (unauthenticated)
- **Request**: `GET /api/v1/leaderboard/daily` without auth
- **Expected**: 401

---

## 6. Review Endpoints (`test_review_endpoints.py`)

### Contract 6.1: GET /api/v1/reviews/ (overview)
- **Request**: `GET /api/v1/reviews/` with Bearer token
- **Mock**: `ReviewService.get_overview()` returns subject review counts
- **Expected**: 200, body contains `subjects` array

### Contract 6.2: GET /api/v1/reviews/{subject} (due items)
- **Request**: `GET /api/v1/reviews/SUB-MATH` with Bearer token
- **Mock**: `ReviewService.get_due_items()` returns items list
- **Expected**: 200, body contains `items` array, `has_more`

### Contract 6.3: POST /api/v1/reviews/{subject}/submit (success)
- **Request**: `POST /api/v1/reviews/SUB-MATH/submit` with `{items: [...]}`
- **Mock**: `ReviewService.submit_reviews()` succeeds, `WalletService.award_xp()` called
- **Expected**: 200, body contains `processed`, `xp_awarded`

### Contract 6.4: GET /api/v1/reviews/ (unauthenticated)
- **Request**: `GET /api/v1/reviews/` without auth
- **Expected**: 401

### Contract 6.5: POST /api/v1/reviews/{subject}/submit (empty items)
- **Request**: `POST /api/v1/reviews/SUB-MATH/submit` with `{items: []}`
- **Expected**: 422 (min_length=1 validation)

---

## 7. Settings Endpoints (`test_settings_endpoints.py`)

### Contract 7.1: GET /api/v1/settings/gamification (success)
- **Request**: `GET /api/v1/settings/gamification` (no auth)
- **Mock**: `SettingsService.get_gamification_settings()` returns settings
- **Expected**: 200, body contains `base_lesson_xp`, `replay_xp`, etc.

### Contract 7.2: GET /api/v1/settings/gamification (public)
- **Request**: `GET /api/v1/settings/gamification` without Bearer token
- **Expected**: 200 (not 401 -- public endpoint)

---

## 8. Subscription Endpoints (`test_subscription_endpoints.py`)

### Contract 8.1: GET /api/v1/subscriptions/ (success)
- **Request**: `GET /api/v1/subscriptions/` with Bearer token
- **Mock**: `AccessService.get_player_grants()` returns grant list, `get_plan_free_subjects()` returns plan subjects
- **Expected**: 200, body contains `grants` (sorted array), `plan_subjects` (sorted array)

### Contract 8.2: GET /api/v1/subscriptions/ (unauthenticated)
- **Request**: `GET /api/v1/subscriptions/` without auth
- **Expected**: 401

---

## 9. Voucher Endpoints (`test_voucher_endpoints.py`)

### Contract 9.1: POST /api/v1/voucher/preview (success)
- **Request**: `POST /api/v1/voucher/preview` with `{pin: "VALID123"}`
- **Mock**: `VoucherService.preview()` returns grant options
- **Expected**: 200, body contains `face_value`, `grants` array

### Contract 9.2: POST /api/v1/voucher/preview (invalid PIN)
- **Request**: `POST /api/v1/voucher/preview` with `{pin: "BAD"}`
- **Mock**: `VoucherService.preview()` raises error with code INVALID_PIN
- **Expected**: 404, body contains `error: "INVALID_PIN"`

### Contract 9.3: POST /api/v1/voucher/redeem (success)
- **Request**: `POST /api/v1/voucher/redeem` with `{pin: "VALID123", grant_id: "GRNT-001"}`
- **Mock**: `VoucherService.check_rate_limit()` returns None, `redeem()` succeeds
- **Expected**: 200, body contains `status`, `transaction_id`

### Contract 9.4: POST /api/v1/voucher/redeem (rate limited)
- **Request**: `POST /api/v1/voucher/redeem` with valid payload
- **Mock**: `VoucherService.check_rate_limit()` returns retry_after seconds
- **Expected**: 429, body contains `error: "RATE_LIMITED"`, `retry_after`

---

## 10. Webhook Endpoints (`test_webhook_endpoints.py`)

### Contract 10.1: POST /api/v1/webhooks/payment (accepted)
- **Request**: `POST /api/v1/webhooks/payment` with full WebhookPayload
- **Mock**: Redis SET for idempotency tracking
- **Expected**: 200, body contains `status: "accepted"`

### Contract 10.2: POST /api/v1/webhooks/payment (duplicate event_id)
- **Request**: Same event_id sent twice
- **Expected**: 200, second response contains `status: "already_processed"`

### Contract 10.3: POST /api/v1/webhooks/payment (invalid payload)
- **Request**: Missing required fields
- **Expected**: 422

### Contract 10.4: POST /api/v1/webhooks/payment (no auth needed)
- **Request**: `POST /api/v1/webhooks/payment` without Bearer token
- **Expected**: 200 (webhook is public -- external payment provider)

---

## 11. Notification Endpoints (`test_notification_endpoints.py`)

### Contract 11.1: WS /api/v1/notifications/ws (valid JWT)
- **Request**: WebSocket connect with `?token=valid_jwt`
- **Expected**: Connection established (no close frame)

### Contract 11.2: WS /api/v1/notifications/ws (invalid JWT)
- **Request**: WebSocket connect with `?token=invalid_token`
- **Expected**: Connection closed with code 1008

### Contract 11.3: WS /api/v1/notifications/ws (receive message)
- **Request**: Connected WebSocket, publish to `memora:notify:{user_id}`
- **Expected**: Client receives the published message text
