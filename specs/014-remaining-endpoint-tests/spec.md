# Feature Specification: Remaining Endpoint Tests

**Feature Branch**: `014-remaining-endpoint-tests`
**Created**: 2026-02-17
**Status**: Draft
**Input**: User description: "Phase 6: Remaining Endpoint Tests (~45 tests, 11 files) from FASTAPI_TEST_PLAN.md"

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Data-Retrieval Endpoint Verification (Priority: P1)

A developer runs the test suite and confirms that all read-only data endpoints (catalog, plans, profile, leaderboard, settings, subscriptions) return correct responses for authenticated users, handle missing data gracefully, and reject unauthenticated requests.

**Why this priority**: Read endpoints are the most frequently called routes in the mobile app. Verifying they return correct data structures and enforce authentication is foundational to API reliability.

**Independent Test**: Can be fully tested by running `pytest fastapi_app/tests/test_catalog_endpoints.py test_plans_endpoints.py test_profile_endpoints.py test_leaderboard_endpoints.py test_settings_endpoints.py test_subscription_endpoints.py -v` and verifying all assertions pass against mocked service responses.

**Acceptance Scenarios**:

1. **Given** a player is authenticated with a valid JWT, **When** they request any data endpoint (catalog, plans, profile, leaderboard, settings, subscriptions), **Then** the system returns 200 with the expected response schema
2. **Given** no authentication token is provided, **When** requesting a protected data endpoint, **Then** the system returns 401 Unauthorized
3. **Given** a player requests a resource that does not exist (e.g., nonexistent plan ID or player ID), **When** the request is processed, **Then** the system returns 404 Not Found
4. **Given** an empty dataset (no leaderboard entries, no catalog products), **When** the endpoint is called, **Then** the system returns 200 with an empty collection (not an error)

---

### User Story 2 - Transactional Endpoint Verification (Priority: P1)

A developer runs the test suite and confirms that state-changing endpoints (purchase, voucher redeem, review submit, webhook, profile update) correctly process valid requests, reject invalid payloads, handle duplicates, and enforce rate limits where applicable.

**Why this priority**: Transactional endpoints modify player state (subscriptions, XP, profile). Bugs here cause data corruption or financial impact. Equal priority with read endpoints.

**Independent Test**: Can be fully tested by running `pytest fastapi_app/tests/test_purchase_endpoints.py test_voucher_endpoints.py test_review_endpoints.py test_webhook_endpoints.py -v` and verifying all state-change operations produce correct outcomes.

**Acceptance Scenarios**:

1. **Given** a valid purchase request from an authenticated player, **When** submitted, **Then** the system returns 201 Created and delegates to the purchase service
2. **Given** a duplicate purchase request for the same product, **When** submitted, **Then** the system returns 409 Conflict
3. **Given** a valid voucher PIN, **When** previewed, **Then** the system returns 200 with available grants; **When** redeemed with a valid grant selection, **Then** the system returns 200 with a transaction ID
4. **Given** repeated failed voucher redemption attempts, **When** the rate limit is exceeded, **Then** the system returns 429 Too Many Requests
5. **Given** a valid payment webhook payload, **When** received, **Then** the system returns 200 "accepted" and processes in background; **When** the same event_id is sent again, **Then** the system returns 200 "already_processed" (idempotent)
6. **Given** an invalid webhook payload or missing required fields, **When** received, **Then** the system returns 422 Unprocessable Entity

---

### User Story 3 - WebSocket Notification Endpoint Verification (Priority: P2)

A developer runs the test suite and confirms that the WebSocket notification endpoint correctly authenticates connections via JWT query parameter, rejects invalid tokens, and can receive published messages.

**Why this priority**: WebSocket testing requires a different testing approach (connection lifecycle vs HTTP request/response). Lower priority because notifications are supplementary to core game flow.

**Independent Test**: Can be fully tested by running `pytest fastapi_app/tests/test_notification_endpoints.py -v` and verifying WebSocket connection establishment, authentication rejection, and message delivery.

**Acceptance Scenarios**:

1. **Given** a valid JWT token provided as a query parameter, **When** connecting to the WebSocket endpoint, **Then** the connection is established successfully
2. **Given** an invalid or expired JWT token, **When** attempting WebSocket connection, **Then** the connection is rejected with close code 1008
3. **Given** an established WebSocket connection, **When** a message is published to the player's notification channel, **Then** the message is received by the connected client

---

### User Story 4 - Public Endpoint Verification (Priority: P2)

A developer runs the test suite and confirms that public (unauthenticated) endpoints -- plan manifest and gamification settings -- are accessible without authentication and return correct cached data.

**Why this priority**: These endpoints serve the mobile app's initial load before login. They must work without auth, but are simpler to test than authenticated flows.

**Independent Test**: Can be fully tested by running `pytest fastapi_app/tests/test_plans_endpoints.py test_settings_endpoints.py -v` using `app_client` (no auth) and verifying 200 responses.

**Acceptance Scenarios**:

1. **Given** no authentication header, **When** requesting plan manifest or gamification settings, **Then** the system returns 200 with correct data (no 401)
2. **Given** a nonexistent plan ID, **When** requesting its manifest, **Then** the system returns 404

---

### Edge Cases

- What happens when the catalog service returns an empty product list for a player with no plan? (Returns 200 with empty products array)
- How does the voucher endpoint handle an invalid PIN format (too short/long)? (Returns 422 validation error)
- What happens when a WebSocket client disconnects mid-session? (Unsubscribes from pub/sub channel gracefully, no errors)
- How does the webhook endpoint handle concurrent duplicate event_ids? (Idempotent -- only one processes, other returns "already_processed")
- What happens when the review submit request contains zero items or exceeds max batch size? (Returns 422 for empty, accepts up to 10 items)
- How does the profile update endpoint handle invalid avatar selection? (Returns 400)
- What happens when leaderboard is requested with an invalid type? (Returns 422 validation error)

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: Test suite MUST cover all 11 remaining endpoint groups with at least one happy-path and one error-path test per route
- **FR-002**: Each test file MUST follow the established pattern from Phase 5 tests (class-based organization, `@pytest.mark.asyncio`, fixture-based setup)
- **FR-003**: All tests MUST use the existing `conftest.py` fixtures (`app_client`, `authed_client`, `admin_client`, `mock_frappe`, `redis_client`) without modifying them
- **FR-004**: Tests MUST mock the Frappe boundary (`FrappeClient.call()`) and MUST NOT require a running Frappe instance
- **FR-005**: Tests MUST use real Redis with prefix isolation (never FLUSHDB) and clean up test keys after each test via the `cleanup_keys` autouse fixture
- **FR-006**: Tests MUST verify correct HTTP status codes (200, 201, 400, 401, 404, 409, 410, 422, 429, 503) for each scenario
- **FR-007**: Tests MUST validate response body schema (field presence and types) for successful responses
- **FR-008**: Authentication tests MUST verify that protected endpoints return 401 when called without a valid JWT
- **FR-009**: Public endpoints (plan manifest, gamification settings) MUST be tested without authentication headers
- **FR-010**: Rate-limited endpoints (voucher redeem) MUST include a test that triggers the rate limit and verifies 429 response with retry_after
- **FR-011**: Idempotency tests (webhook) MUST verify that duplicate requests produce the same response without double-processing
- **FR-012**: WebSocket notification tests MUST verify connection establishment, authentication rejection, and message receipt
- **FR-013**: All tests MUST pass when run together as a full suite (`python3 -m pytest fastapi_app/tests/ -v`) without interference between test files
- **FR-014**: Test suite MUST produce approximately 41-45 individual test cases across all 11 files

### Key Entities

- **Endpoint Test File**: One test file per endpoint group (11 files total), each containing a test class with async test methods
- **Test Fixture**: Shared setup functions from `conftest.py` that provide test clients, Redis connections, and mock services
- **Mock Service Response**: Predefined return values for `FrappeClient.call()` that simulate Frappe API responses without requiring the actual backend

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: All 41-45 test cases pass when run via `python3 -m pytest fastapi_app/tests/ -v --tb=short`
- **SC-002**: No test requires a running Frappe instance or external HTTP calls -- all external dependencies are mocked
- **SC-003**: Full test suite (Phases 1-6) completes in under 60 seconds total
- **SC-004**: Every endpoint route registered in the router has at least one test covering its happy path
- **SC-005**: Every authenticated endpoint has a test verifying 401 rejection without a token
- **SC-006**: Zero test isolation failures -- tests can run in any order without affecting each other
- **SC-007**: No Redis keys leak between tests -- all test-prefixed keys are cleaned up after each test

### Assumptions

- The existing `conftest.py` fixtures from Phase 5 (foundation phase) are stable and sufficient for Phase 6 tests
- The FastAPI app and all endpoint modules are importable without a running Frappe instance
- Redis at `redis://127.0.0.1:13000` is available during test execution
- The Phase 5 test patterns (class-based, async, fixture-driven) are the standard to follow
- WebSocket testing is supported via httpx's WebSocket capabilities or the FastAPI TestClient's WebSocket support
- All service dependencies are properly registered in `deps.py` and can be overridden via `app.dependency_overrides`
- The test plan's test count of ~41-45 is approximate; actual count may vary slightly based on implementation discoveries
