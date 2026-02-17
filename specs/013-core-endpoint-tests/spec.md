# Feature Specification: Core Endpoint Tests

**Feature Branch**: `013-core-endpoint-tests`
**Created**: 2026-02-17
**Status**: Draft
**Input**: User description: "Phase 5: Core Endpoint Tests (~60 tests, 6 files) from FASTAPI_TEST_PLAN.md"

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Health Check Verification (Priority: P1)

A developer or operations team member needs confidence that the system health endpoints accurately report service availability and dependency status, enabling reliable monitoring and automated deployment checks.

**Why this priority**: Health checks are the foundation of deployment pipelines, load balancer routing, and incident response. Without verified health endpoints, automated deployments cannot safely proceed.

**Independent Test**: Can be fully tested by issuing unauthenticated HTTP requests to health endpoints and verifying response codes and body content. Delivers confidence in operational readiness reporting.

**Acceptance Scenarios**:

1. **Given** the service is running, **When** a GET request is sent to the liveness endpoint, **Then** a 200 response is returned with status "alive" and no authentication is required.
2. **Given** the backing data store is connected, **When** a GET request is sent to the readiness endpoint, **Then** a 200 response is returned with dependency status "ok".
3. **Given** the backing data store is unreachable, **When** a GET request is sent to the readiness endpoint, **Then** a 503 response is returned indicating the dependency is unreachable.

---

### User Story 2 - Authentication Flow Verification (Priority: P1)

A developer needs assurance that all 10 authentication routes behave correctly: player login, admin login, token refresh, registration (3-step), and password reset (3-step). These must enforce rate limiting, device tracking, anti-enumeration patterns, and single-session semantics.

**Why this priority**: Authentication is the security perimeter of the entire system. Bugs here expose all user data and game state. Every other authenticated endpoint depends on correct auth behavior.

**Independent Test**: Can be fully tested by sending HTTP requests with various credential payloads and verifying status codes, token structure, rate limit headers, and session state. Delivers confidence that the auth perimeter is correct.

**Acceptance Scenarios**:

1. **Given** valid player credentials, **When** a login request is sent with a device ID header, **Then** tokens and profile are returned with 200.
2. **Given** invalid credentials, **When** a login request is sent, **Then** a 401 response is returned.
3. **Given** a missing device ID header, **When** a player login request is sent, **Then** a 400 response is returned.
4. **Given** a valid refresh token, **When** a refresh request is sent, **Then** new access and refresh tokens are returned with 200.
5. **Given** a refresh token with a mismatched session family, **When** a refresh request is sent, **Then** a 401 response is returned indicating the session was superseded.
6. **Given** a registration pending ID and correct OTP, **When** a verify request is sent, **Then** the player is created and tokens are returned.
7. **Given** a password reset flow, **When** a reset is requested for any phone number, **Then** the response is always 200 regardless of whether the phone exists (anti-enumeration).
8. **Given** a valid single-use reset token, **When** the confirm endpoint is called, **Then** the password is reset. A second call with the same token returns 401.
9. **Given** excessive login attempts, **When** the rate limit is exceeded, **Then** a 429 response is returned with a Retry-After header.

---

### User Story 3 - Game Session Lifecycle Verification (Priority: P1)

A developer needs assurance that the session start/end lifecycle correctly enforces access control (free vs. paid content), awards XP, detects replays, updates streaks, refreshes leaderboards, and manages stats caching.

**Why this priority**: Session completion is the core game loop. It orchestrates progress tracking, XP economy, streaks, and leaderboards. Incorrect behavior here directly impacts player experience and data integrity.

**Independent Test**: Can be fully tested by starting sessions with various access states, completing them with different stage results, and verifying XP awards, replay detection, streak updates, and stat changes. Delivers confidence in the core game loop.

**Acceptance Scenarios**:

1. **Given** an authenticated player with access to a subject, **When** a session start request is sent, **Then** a 200 response is returned with a session ID.
2. **Given** an authenticated player without access to a paid subject, **When** a session start request is sent, **Then** a 403 response is returned with code "NO_ACCESS".
3. **Given** free content in a subject, **When** a session start request is sent without explicit access grants, **Then** a 200 response is returned (free content bypass).
4. **Given** an active session, **When** an end request is sent, **Then** a response includes XP awarded, replay status, and streak info.
5. **Given** a previously completed lesson, **When** the session is ended, **Then** the response indicates is_replay=True with reduced XP.
6. **Given** no active session, **When** a session end request is sent, **Then** a 403 response is returned.
7. **Given** a non-existent subject, **When** a session start request is sent, **Then** a 404 response is returned.
8. **Given** no authentication, **When** any session endpoint is called, **Then** a 401 response is returned.

---

### User Story 4 - Progress Retrieval Verification (Priority: P2)

A developer needs assurance that the 6 progress endpoints correctly return subject summaries, track listings, unit details, and lesson completion statuses while enforcing access control for paid content and allowing free content access.

**Why this priority**: Progress tracking is how players see their advancement. Incorrect progress data or access violations degrade the learning experience and trust in the platform.

**Independent Test**: Can be fully tested by seeding completion bitmaps and hierarchy data, then requesting progress at each level (summary, subject, track, unit, topic/lessons) and verifying computed stats and access enforcement. Delivers confidence in the progress display pipeline.

**Acceptance Scenarios**:

1. **Given** an authenticated player with access, **When** the progress summary is requested, **Then** a list of subjects with completion percentages is returned.
2. **Given** a specific subject ID, **When** subject progress is requested, **Then** track-level breakdown with completed/total counts is returned.
3. **Given** a non-existent subject, **When** progress is requested, **Then** a 404 response is returned.
4. **Given** a paid subject without grants and without free content, **When** progress is requested, **Then** a 403 response is returned.
5. **Given** a subject with free content but no explicit grant, **When** progress is requested, **Then** access is allowed (free content bypass).
6. **Given** no authentication, **When** any progress endpoint is called, **Then** a 401 response is returned.

---

### User Story 5 - Wallet Retrieval Verification (Priority: P2)

A developer needs assurance that wallet endpoints return correct XP and streak data for the authenticated player, restrict admin-only access to player lookups, and auto-hydrate from the source of truth on cache miss.

**Why this priority**: The wallet drives the XP economy and streak display. Admin lookup enables support operations. Both must return correct data and enforce role-based access.

**Independent Test**: Can be fully tested by requesting wallet data as a player and admin, verifying response structure, and checking that non-admin users are blocked from the admin lookup endpoint. Delivers confidence in XP/streak data accuracy and access control.

**Acceptance Scenarios**:

1. **Given** an authenticated player, **When** the wallet endpoint is called, **Then** a 200 response with XP and streak values is returned.
2. **Given** a new player with no wallet data, **When** the wallet endpoint is called, **Then** default values (xp=0, streak=0) are returned.
3. **Given** an admin user, **When** the admin wallet lookup endpoint is called with a player ID, **Then** that player's wallet data is returned.
4. **Given** a non-admin user, **When** the admin wallet lookup endpoint is called, **Then** a 403 response is returned.

---

### User Story 6 - Access Grant Management Verification (Priority: P2)

A developer needs assurance that admin-only access management endpoints correctly grant, revoke, and list content access keys while enforcing admin authorization.

**Why this priority**: Access grants control which content players can access. Incorrect grant behavior could expose paid content or lock out paying users. Admin-only enforcement prevents unauthorized access manipulation.

**Independent Test**: Can be fully tested by calling grant/revoke/list endpoints as admin and non-admin users, verifying idempotent grant behavior and correct revocation. Delivers confidence in the access control administrative interface.

**Acceptance Scenarios**:

1. **Given** an admin user, **When** access keys are granted for a player, **Then** the count of newly added keys is returned.
2. **Given** an admin user granting the same key twice, **When** the second grant is processed, **Then** the returned count is 0 (idempotent).
3. **Given** an admin user, **When** access keys are revoked, **Then** the count of removed keys is returned.
4. **Given** an admin user, **When** grants are listed for a player, **Then** all current grant keys and their count are returned.
5. **Given** a non-admin user, **When** any access management endpoint is called, **Then** a 403 response is returned.
6. **Given** an admin user, **When** grant or revoke is called with an empty key list, **Then** a 400 response is returned.

---

### Edge Cases

- What happens when the backing data store connection is lost mid-request during a session end (partial state)?
- How does the system handle concurrent session starts for the same player (race condition)?
- What happens when a JWT token is syntactically valid but contains unexpected claim types?
- How does the system respond when a refresh token's family ID no longer exists in the session store?
- What happens when wallet hydration fails during a session end (XP computation on empty state)?
- How does the progress endpoint handle a hierarchy cache miss when the external API is also unavailable?
- What happens when device registration hits the maximum device limit during player login?

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: Test suite MUST verify all 6 core endpoint files produce correct HTTP status codes for both success and error paths
- **FR-002**: Test suite MUST verify that unauthenticated requests to protected endpoints return 401 status
- **FR-003**: Test suite MUST verify that non-admin requests to admin-only endpoints return 403 status
- **FR-004**: Test suite MUST verify health endpoints are publicly accessible without authentication
- **FR-005**: Test suite MUST verify player login enforces device ID header requirement (400 on missing)
- **FR-006**: Test suite MUST verify rate limiting returns 429 with Retry-After header when exceeded
- **FR-007**: Test suite MUST verify token refresh validates session family ID (401 on mismatch)
- **FR-008**: Test suite MUST verify registration flow: initiate, verify OTP, and resend OTP
- **FR-009**: Test suite MUST verify password reset flow: request (anti-enumeration), verify, confirm (single-use token)
- **FR-010**: Test suite MUST verify session start enforces access control (403 for paid content without grant, 200 for free content bypass)
- **FR-011**: Test suite MUST verify session end returns XP awarded, replay detection, and streak info
- **FR-012**: Test suite MUST verify progress endpoints enforce access control with free content bypass
- **FR-013**: Test suite MUST verify wallet endpoint returns default values for new players
- **FR-014**: Test suite MUST verify access grant/revoke idempotency and empty-key-list validation
- **FR-015**: Test suite MUST use real Redis with prefix isolation (never FLUSHDB) on the shared Frappe Redis instance
- **FR-016**: Test suite MUST mock the external Frappe API boundary (FrappeClient) for all tests
- **FR-017**: Test suite MUST produce approximately 60 tests across 6 test files matching the test plan structure
- **FR-018**: Test suite MUST be runnable via `python3 -m pytest fastapi_app/tests/ -v` alongside existing tests from prior phases

### Key Entities

- **Test File**: A Python test module containing related endpoint tests (one per endpoint module: health, auth, sessions, progress, wallet, access)
- **Test Fixture**: Reusable test setup component (authed_client, admin_client, app_client, mock_frappe) from the existing conftest.py infrastructure
- **Endpoint Route**: An HTTP route handler with method, path, authentication requirements, and expected behavior
- **Access Key**: A content identifier (e.g., SUB-{id}, TRK-{id}) used to grant/revoke player access to subjects or tracks

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: All ~60 endpoint tests pass consistently when run via the standard test command
- **SC-002**: Each of the 6 test files covers all routes defined in its corresponding endpoint module
- **SC-003**: Test suite completes execution in under 30 seconds total
- **SC-004**: Zero false positives: tests fail only when actual endpoint behavior changes
- **SC-005**: 100% of protected endpoints have at least one unauthenticated access test
- **SC-006**: 100% of admin-only endpoints have at least one non-admin access test
- **SC-007**: Auth endpoint tests cover all 10 routes with at least one success and one failure case each
- **SC-008**: Session endpoint tests verify the complete start-to-end lifecycle including XP, replay, and streak
- **SC-009**: Existing tests from prior phases (1-4) continue to pass without modification

### Assumptions

- The existing conftest.py infrastructure (from Phase 1) provides all necessary fixtures: `app_client`, `authed_client`, `admin_client`, `mock_frappe`, `redis_client`, `test_prefix`, and token factories
- The `authed_client` fixture correctly seeds Redis session state so that `get_current_user` dependency validation passes through the ASGI transport
- Frappe API calls are fully mocked via `mock_frappe.call` — no real Frappe server is required
- Redis is available at `redis://127.0.0.1:13000` and shared with Frappe (prefix isolation is mandatory)
- All endpoint routes are registered under `/api/v1/` prefix as defined in the router configuration
- Session validation in `get_current_user` uses the same Redis client injected via dependency overrides, so session seeding with the test Redis client works correctly for auth validation
