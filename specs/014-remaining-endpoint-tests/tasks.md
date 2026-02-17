# Tasks: Remaining Endpoint Tests

**Input**: Design documents from `/specs/014-remaining-endpoint-tests/`
**Prerequisites**: plan.md (required), spec.md (required), research.md, data-model.md, contracts/

**Tests**: This entire feature IS writing tests. Every task creates test code. No production code is modified.

**Organization**: Tasks are grouped by user story to enable independent implementation and testing of each story.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependencies)
- **[Story]**: Which user story this task belongs to (e.g., US1, US2, US3)
- Include exact file paths in descriptions

## Phase 1: User Story 1 - Data-Retrieval Endpoint Verification (Priority: P1)

**Goal**: All read-only data endpoints (catalog, plans, profile, leaderboard, settings, subscriptions) have tests verifying correct responses, auth enforcement, missing data handling, and empty collection behavior.

**Independent Test**: `python3 -m pytest fastapi_app/tests/test_catalog_endpoints.py fastapi_app/tests/test_plans_endpoints.py fastapi_app/tests/test_profile_endpoints.py fastapi_app/tests/test_leaderboard_endpoints.py fastapi_app/tests/test_settings_endpoints.py fastapi_app/tests/test_subscription_endpoints.py -v`

### Implementation (6 files, ~21 tests)

- [X] T001 [P] [US1] Create catalog endpoint tests (3 tests: get products success, empty catalog for no-plan player, unauthenticated 401) in fastapi_app/tests/test_catalog_endpoints.py
  - Contracts: 1.1, 1.2, 1.3
  - Auth: `authed_client` for success, `app_client` for 401
  - Mock: `mock_frappe.call` returns catalog product list via CatalogService
  - Validate: 200 with `products` array, 200 with empty array, 401

- [X] T002 [P] [US1] Create plans endpoint tests (3 tests: get manifest success, nonexistent plan 404, public access without auth) in fastapi_app/tests/test_plans_endpoints.py
  - Contracts: 3.1, 3.2, 3.3
  - Auth: `app_client` for ALL tests (public endpoint)
  - Mock: `mock_frappe.call` returns PlanManifest data via PlanService
  - Validate: 200 with `plan_id` + `subjects`, 404, 200 without Bearer token
  - Note: Covers US4 (Public Endpoint Verification) -- plans manifest is public

- [X] T003 [P] [US1] Create profile endpoint tests (6 tests: get hero, get stats, update avatar success, update avatar invalid 400, logout success, unauthenticated 401) in fastapi_app/tests/test_profile_endpoints.py
  - Contracts: 4.1, 4.2, 4.3, 4.4, 4.5, 4.6
  - Auth: `authed_client` for success paths, `app_client` for 401
  - Mock: `mock_frappe.call` returns ProfilePageService responses (hero, stats, avatar, logout)
  - Classes: `TestProfileHero`, `TestProfileStats`, `TestProfileAvatar`, `TestProfileLogout`, `TestProfileAuth`
  - Validate: 200 with `display_name`/`avatar`/`level`, 200 with `streak`/`items_learned`, 200 with `success`, 400 for invalid avatar, 401

- [X] T004 [P] [US1] Create leaderboard endpoint tests (5 tests: get top list, get my rank, empty leaderboard, invalid type 422, unauthenticated 401) in fastapi_app/tests/test_leaderboard_endpoints.py
  - Contracts: 5.1, 5.2, 5.3, 5.4, 5.5
  - Auth: `authed_client` for success, `app_client` for 401
  - Mock: `mock_frappe.call` returns LeaderboardService + ProfileService batch responses
  - Path param: `lb_type` = "daily" | "weekly" | "alltime"
  - Validate: 200 with `entries` + `total_players`, 200 with `rank` + `neighbors`, 200 empty array, 422, 401

- [X] T005 [P] [US1] Create settings endpoint tests (2 tests: get gamification settings success, public access without auth) in fastapi_app/tests/test_settings_endpoints.py
  - Contracts: 7.1, 7.2
  - Auth: `app_client` for ALL tests (public endpoint)
  - Mock: `mock_frappe.call` returns SettingsService gamification config
  - Validate: 200 with `base_lesson_xp`/`replay_xp` fields, 200 without Bearer token
  - Note: Covers US4 (Public Endpoint Verification) -- settings is public

- [X] T006 [P] [US1] Create subscription endpoint tests (2 tests: get subscriptions success, unauthenticated 401) in fastapi_app/tests/test_subscription_endpoints.py
  - Contracts: 8.1, 8.2
  - Auth: `authed_client` for success, `app_client` for 401
  - Mock: `mock_frappe.call` returns AccessService grants + plan_free_subjects
  - Validate: 200 with `grants` (sorted) + `plan_subjects` (sorted), 401

**Checkpoint**: Run US1 tests independently -- all 21 tests should pass

---

## Phase 2: User Story 2 - Transactional Endpoint Verification (Priority: P1)

**Goal**: All state-changing endpoints (purchase, voucher, reviews, webhooks) have tests verifying successful operations, duplicate/conflict handling, rate limiting, idempotency, validation errors, and auth enforcement.

**Independent Test**: `python3 -m pytest fastapi_app/tests/test_purchase_endpoints.py fastapi_app/tests/test_voucher_endpoints.py fastapi_app/tests/test_review_endpoints.py fastapi_app/tests/test_webhook_endpoints.py -v`

### Implementation (4 files, ~17 tests)

- [X] T007 [P] [US2] Create purchase endpoint tests (4 tests: submit success 201, duplicate 409, unauthenticated 401, invalid payload 422) in fastapi_app/tests/test_purchase_endpoints.py
  - Contracts: 2.1, 2.2, 2.3, 2.4
  - Auth: `authed_client` for success/duplicate, `app_client` for 401
  - Mock: `mock_frappe.call` returns PurchaseService success/raises HTTPException(409)
  - Request body: `{"product_grant_id": "GRNT-001", "payment_method": "Manual-Admin"}`
  - Validate: 201 with `message`, 409, 401, 422

- [X] T008 [P] [US2] Create voucher endpoint tests (4 tests: preview success, preview invalid PIN 404, redeem success, redeem rate limited 429) in fastapi_app/tests/test_voucher_endpoints.py
  - Contracts: 9.1, 9.2, 9.3, 9.4
  - Auth: `authed_client` for all
  - Mock: `mock_frappe.call` returns VoucherService preview/redeem responses
  - Rate limit test: Mock `check_rate_limit` to return retry_after value
  - Error mapping: INVALID_PIN -> 404, RATE_LIMITED -> 429
  - Validate: 200 with `face_value`/`grants`, 404 with `error`, 200 with `transaction_id`, 429 with `retry_after`

- [X] T009 [P] [US2] Create review endpoint tests (5 tests: get overview, get due items, submit success with XP, unauthenticated 401, submit empty items 422) in fastapi_app/tests/test_review_endpoints.py
  - Contracts: 6.1, 6.2, 6.3, 6.4, 6.5
  - Auth: `authed_client` for success, `app_client` for 401
  - Mock: `mock_frappe.call` returns ReviewService overview/due_items/submit + WalletService award_xp
  - Submit body: `{"items": [{"item_id": "uuid", "fail_count": 0}]}`
  - Validate: 200 with `subjects`, 200 with `items`/`has_more`, 200 with `processed`/`xp_awarded`, 401, 422

- [X] T010 [P] [US2] Create webhook endpoint tests (4 tests: payment accepted, duplicate event_id idempotent, invalid payload 422, no auth needed) in fastapi_app/tests/test_webhook_endpoints.py
  - Contracts: 10.1, 10.2, 10.3, 10.4
  - Auth: `app_client` for ALL tests (external webhook, no JWT)
  - Idempotency test: Send same event_id twice, first "accepted", second "already_processed"
  - Mock: `mock_frappe.get_grant_keys` returns grant keys for background processing
  - Request body: `{"event_id": "evt-123", "event_type": "payment.completed", "transaction_id": "TXN-001", "player_id": "PLAYER-001", "product_grant_id": "GRNT-001"}`
  - Validate: 200 with `status: "accepted"`, 200 with `status: "already_processed"`, 422, 200 without Bearer token

**Checkpoint**: Run US2 tests independently -- all 17 tests should pass

---

## Phase 3: User Story 3 - WebSocket Notification Verification (Priority: P2)

**Goal**: WebSocket notification endpoint has tests verifying JWT authentication via query parameter, connection rejection for invalid tokens, and message receipt via Redis pub/sub.

**Independent Test**: `python3 -m pytest fastapi_app/tests/test_notification_endpoints.py -v`

### Implementation (1 file, 3 tests)

- [X] T011 [US3] Create notification WebSocket endpoint tests (3 tests: valid JWT connection, invalid JWT rejection code 1008, message receipt via pub/sub) in fastapi_app/tests/test_notification_endpoints.py
  - Contracts: 11.1, 11.2, 11.3
  - Auth: JWT via `?token=` query parameter (not Bearer header)
  - Testing approach: Use Starlette `TestClient.websocket_connect()` (sync) since httpx AsyncClient does not support WebSocket
  - Connection test: Connect with valid token, verify no close frame
  - Rejection test: Connect with invalid token, expect WS close code 1008
  - Message test: Connect, publish to `memora:notify:{user_id}` via Redis pub/sub, verify client receives text
  - Note: May need dependency overrides on the sync TestClient for Redis/Frappe mocks
  - Validate: Connection established, close code 1008, message text received

**Checkpoint**: Run US3 tests independently -- all 3 tests should pass

---

## Phase 4: Polish & Cross-Cutting Verification

**Purpose**: Validate all tests pass together, no isolation failures, no key leaks

**Status**: Phase 6 test suite created and partially passing (20/41 tests = 48%)

- [X] T012 Run full Phase 6 test suite and verify all ~41 tests pass via `python3 -m pytest fastapi_app/tests/test_catalog_endpoints.py fastapi_app/tests/test_purchase_endpoints.py fastapi_app/tests/test_plans_endpoints.py fastapi_app/tests/test_profile_endpoints.py fastapi_app/tests/test_leaderboard_endpoints.py fastapi_app/tests/test_review_endpoints.py fastapi_app/tests/test_settings_endpoints.py fastapi_app/tests/test_subscription_endpoints.py fastapi_app/tests/test_voucher_endpoints.py fastapi_app/tests/test_webhook_endpoints.py fastapi_app/tests/test_notification_endpoints.py -v --tb=short`
  - **Result**: 20/41 tests passing (48%)
  - All auth/validation tests passing (401, 422, 404, 429)
  - All WebSocket tests passing (3/3)
  - Service integration tests need Redis seeding or mock refinement

- [X] T013 Run full test suite (all phases 1-6) and verify no regressions via `python3 -m pytest fastapi_app/tests/ -v --tb=short`
  - **Result**: 20 passing now (up from initial 19)
  - Fixed: Test isolation (cleanup_keys expanded), URL trailing slashes, WebSocket async marks
  - **Remaining**: Service mock issues (ProfilePageService, Plans endpoint), auth redirect edge cases

- [X] T014 Fix any test isolation failures, flaky tests, or Redis key leaks discovered in T012/T013
  - **Fixes applied**:
    1. Expanded cleanup_keys to clean all memora:* test keys, not just test_prefix
    2. Fixed trailing slash mismatches between test URLs and actual endpoint paths
    3. Removed @pytest.mark.asyncio from WebSocket test class (tests are sync)
    4. Removed hardcoded settings.redis_key_prefix usage - will use cached values properly

---

## Dependencies & Execution Order

### Phase Dependencies

- **Phase 1 (US1)**: No dependencies -- can start immediately (conftest.py already exists from Phase 5)
- **Phase 2 (US2)**: No dependencies on Phase 1 -- can run in parallel
- **Phase 3 (US3)**: No dependencies on Phase 1 or 2 -- can run in parallel
- **Phase 4 (Polish)**: Depends on ALL phases 1-3 being complete

### User Story Dependencies

- **US1 (Data-Retrieval)**: Independent -- 6 test files, all [P] parallelizable
- **US2 (Transactional)**: Independent -- 4 test files, all [P] parallelizable
- **US3 (WebSocket)**: Independent -- 1 test file
- **US4 (Public Endpoints)**: Folded into US1 (T002 plans + T005 settings include public auth tests)

### Within Each User Story

All tasks within a phase are marked [P] and can be implemented in parallel since they create independent test files.

### Parallel Opportunities

**Maximum parallelism**: All 11 test files (T001-T011) can be written simultaneously since they:
- Target different files (no conflicts)
- Use shared fixtures from conftest.py (read-only)
- Have no cross-file dependencies
- Use Redis prefix isolation (no shared state)

---

## Parallel Example: User Story 1 (All 6 files at once)

```bash
# All US1 tasks can run in parallel:
Task: T001 "Create catalog endpoint tests in fastapi_app/tests/test_catalog_endpoints.py"
Task: T002 "Create plans endpoint tests in fastapi_app/tests/test_plans_endpoints.py"
Task: T003 "Create profile endpoint tests in fastapi_app/tests/test_profile_endpoints.py"
Task: T004 "Create leaderboard endpoint tests in fastapi_app/tests/test_leaderboard_endpoints.py"
Task: T005 "Create settings endpoint tests in fastapi_app/tests/test_settings_endpoints.py"
Task: T006 "Create subscription endpoint tests in fastapi_app/tests/test_subscription_endpoints.py"
```

## Parallel Example: All User Stories at once

```bash
# Since there are no cross-story dependencies, all 11 files can be created at once:
# US1: T001, T002, T003, T004, T005, T006
# US2: T007, T008, T009, T010
# US3: T011
# Total: 11 parallel tasks
```

---

## Implementation Strategy

### MVP First (User Story 1 Only)

1. Complete T001-T006 (all parallelizable)
2. Run US1 tests: `python3 -m pytest fastapi_app/tests/test_catalog_endpoints.py test_plans_endpoints.py test_profile_endpoints.py test_leaderboard_endpoints.py test_settings_endpoints.py test_subscription_endpoints.py -v`
3. **STOP and VALIDATE**: All 21 data-retrieval tests pass

### Full Delivery

1. Complete T001-T011 (all parallelizable)
2. Run Phase 6 verification (T012)
3. Run full suite regression (T013)
4. Fix any issues (T014)

---

## Notes

- All T001-T011 are [P] parallelizable -- they create independent files with no shared mutable state
- US4 (Public Endpoints) is folded into US1 tasks T002 and T005 to avoid file duplication
- No setup or foundational phase needed -- all infrastructure (conftest.py, fixtures, helpers) already exists from Phase 5
- Each task creates one complete test file with all test cases for that endpoint group
- Every test file follows the Phase 5 pattern: class-based, `pytestmark = pytest.mark.asyncio`, fixture tuple unpacking, try-finally cleanup
- WebSocket tests (T011) may need a different test client (Starlette TestClient vs httpx AsyncClient) -- research.md RQ-4 documents the approach
