# Feature Specification: Session + Auth Service Tests

**Feature Branch**: `011-session-auth-tests`
**Created**: 2026-02-17
**Status**: Draft
**Input**: User description: "Phase 3: Session + Auth Services (~40 tests, 5 files) from FASTAPI_TEST_PLAN.md"

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Game Session Lifecycle Tests (Priority: P1)

A developer needs confidence that the game session service correctly manages the full lifecycle of a player's game session: starting a new session, retrieving an active session, force-closing an existing session when a new one starts, and completing a session with atomic progress tracking. These are the most complex service tests because they involve Lua scripts that atomically manipulate multiple Redis keys (session hash, progress bitmap, dirty set, interaction buffer).

**Why this priority**: Game sessions are the core gameplay loop. A bug in session start/complete directly causes data loss (XP not recorded, progress not tracked). The Lua scripts perform atomic multi-key operations that are impossible to test without real Redis.

**Independent Test**: Can be fully tested by creating a `test_game_session_service.py` file with 8 tests covering start, get, end, and complete operations against real Redis. Delivers confidence that Lua scripts work correctly with the Redis version in production.

**Acceptance Scenarios**:

1. **Given** no active session, **When** `start_session` is called, **Then** a Redis hash is created with session_id, lesson_id, subject_id, device_id, started_at fields and a 3600s TTL
2. **Given** an active session exists, **When** `start_session` is called again, **Then** the old session is atomically deleted and a new one created (Lua script guarantees no race condition)
3. **Given** an active session, **When** `get_active_session` is called, **Then** a GameSession model is returned with all fields populated
4. **Given** no active session, **When** `get_active_session` is called, **Then** None is returned
5. **Given** an active session, **When** `end_session` is called, **Then** the session data is returned and the hash is deleted
6. **Given** an active session, **When** `complete_session` is called for a never-completed lesson, **Then** the progress bit is set, is_replay=False, dirty set is updated, and interactions are buffered
7. **Given** an active session, **When** `complete_session` is called for an already-completed lesson, **Then** is_replay=True is returned
8. **Given** an active session, **When** `complete_session` pushes interaction JSONs, **Then** the interaction buffer list contains the pushed items

---

### User Story 2 - OTP Verification Tests (Priority: P1)

A developer needs to verify that the OTP service correctly handles the registration and password-reset flows, including rate limiting, attempt tracking, cooldown enforcement, and single-use reset token consumption. OTP is the authentication gateway — bugs here lock users out or allow bypass.

**Why this priority**: OTP is the sole authentication mechanism for player registration and password reset. Rate limiting protects against brute force. Single-use tokens prevent replay attacks. These are security-critical behaviors.

**Independent Test**: Can be fully tested by creating a `test_otp_service.py` file with 12 tests covering registration pending creation, OTP verification (correct/wrong/expired/max attempts), resend with cooldown, password reset flow, and rate limiting. Delivers confidence in the authentication security model.

**Acceptance Scenarios**:

1. **Given** valid registration data, **When** `create_pending_registration` is called, **Then** a pending_id is returned and registration state is stored in Redis with a 5-minute TTL
2. **Given** a pending registration, **When** `verify_registration_otp` is called with the correct OTP, **Then** registration data is returned and all pending Redis keys are cleaned up
3. **Given** a pending registration, **When** `verify_registration_otp` is called with a wrong OTP, **Then** False is returned and the attempt counter is incremented
4. **Given** a pending registration with max attempts exhausted, **When** `verify_registration_otp` is called, **Then** the pending data is deleted (locked out)
5. **Given** a pending registration within the cooldown window, **When** `resend_registration_otp` is called, **Then** a rate-limit error is raised
6. **Given** a valid phone number, **When** `create_password_reset` is called, **Then** a reset OTP is stored in Redis
7. **Given** a verified reset OTP, **When** `verify_password_reset_otp` is called, **Then** a single-use reset token is returned with a 15-minute TTL
8. **Given** a single-use reset token, **When** `validate_reset_token` is called twice, **Then** the first call succeeds and the second call fails (token consumed)
9. **Given** a phone number that has exceeded the per-phone rate limit, **When** `create_pending_registration` is called, **Then** a rate-limit error is raised
10. **Given** an IP that has exceeded the per-IP rate limit, **When** `create_pending_registration` is called, **Then** a rate-limit error is raised

---

### User Story 3 - Session Management Tests (Priority: P2)

A developer needs to verify that the session service correctly manages user authentication sessions: creating sessions with family IDs, validating sessions, invalidating (logout), and overwriting sessions on re-login. Sessions are the foundation of the JWT family-ID security model.

**Why this priority**: Session management underpins all authenticated API calls. The family_id mechanism detects session supersession (login from another device). Less complex than game sessions or OTP but foundational.

**Independent Test**: Can be fully tested by creating a `test_session_service.py` file with 5 tests covering create, validate (match/mismatch), invalidate, and overwrite. Delivers confidence in the session management layer.

**Acceptance Scenarios**:

1. **Given** a user ID and plan, **When** `create_session` is called, **Then** a family_id UUID is returned and a JSON object `{fid, plan}` is stored in Redis with a 30-day TTL
2. **Given** an existing session, **When** `validate_session` is called with the matching family_id, **Then** `(True, plan_id)` is returned
3. **Given** an existing session, **When** `validate_session` is called with a different family_id, **Then** `(False, None)` is returned
4. **Given** an existing session, **When** `invalidate_session` is called, **Then** the Redis key is deleted and True is returned
5. **Given** an existing session, **When** `create_session` is called again for the same user, **Then** the old family_id is replaced with a new one

---

### User Story 4 - Rate Limiter Tests (Priority: P2)

A developer needs to verify that the rate limiter correctly enforces per-IP and per-account limits using atomic Lua scripts, with proper sliding window expiry. The rate limiter protects login, registration, and other sensitive endpoints from abuse.

**Why this priority**: Rate limiting is a shared security primitive used by auth endpoints, OTP, and voucher redemption. Bugs here either block legitimate users or allow brute-force attacks.

**Independent Test**: Can be fully tested by creating a `test_rate_limiter.py` file with 6 tests covering first request, IP limit exceeded, account limit exceeded, retry-after TTL, remaining counts, and window expiry. Delivers confidence in the rate-limiting security layer.

**Acceptance Scenarios**:

1. **Given** no prior requests, **When** `check_rate_limit` is called, **Then** `(True, 0, "")` is returned (allowed)
2. **Given** 10 prior requests from the same IP (the default IP limit), **When** `check_rate_limit` is called, **Then** `(False, retry_after, "ip")` is returned
3. **Given** 5 prior requests for the same account (the default account limit), **When** `check_rate_limit` is called, **Then** `(False, retry_after, "account")` is returned
4. **Given** a rate-limited state, **When** `get_remaining` is called, **Then** correct remaining counts are returned for both IP and account
5. **Given** a rate-limited state, **When** the sliding window TTL expires, **Then** the counter resets and requests are allowed again

---

### User Story 5 - Device Registration Tests (Priority: P2)

A developer needs to verify that the device service correctly manages multi-device registration with fingerprint matching, device limits, and oldest-device replacement. The Lua script handles atomic registration with complex branching logic.

**Why this priority**: Device management controls how many devices a player can use simultaneously. The Lua script has 4 distinct code paths (existing, fingerprint match, new, limit exceeded) that all need verification.

**Independent Test**: Can be fully tested by creating a `test_device_service.py` file with 8 tests covering new device registration, fingerprint matching (reinstall detection), device limit enforcement, oldest-device replacement, device listing, removal, and validation. Delivers confidence in the multi-device security model.

**Acceptance Scenarios**:

1. **Given** no registered devices, **When** `register_device` is called, **Then** the device is stored in a Redis hash and result status is "new"
2. **Given** a registered device, **When** `register_device` is called with the same device_id, **Then** the existing device's last_login is updated and result status is "existing"
3. **Given** a registered device, **When** `register_device` is called with a different device_id but matching user-agent fingerprint, **Then** the old device is replaced and result status is "fingerprint_match"
4. **Given** max_devices already registered, **When** `register_device` is called with a new device and new fingerprint, **Then** result status is "limit_exceeded"
5. **Given** registered devices, **When** `get_devices` is called, **Then** a list of device info objects is returned with correct fields
6. **Given** a registered device, **When** `remove_device` is called, **Then** all device fields are deleted from the hash
7. **Given** a registered device, **When** `validate_device` is called with the known device_id, **Then** True is returned
8. **Given** no registered devices, **When** `validate_device` is called with an unknown device_id, **Then** False is returned

---

### Edge Cases

- What happens when Redis connection drops mid-Lua script execution? (Lua scripts are atomic — Redis either runs them fully or not at all; no partial state)
- What happens when the same OTP pending_id is verified concurrently from two requests? (Redis operations are single-threaded; the first wins, second sees consumed state)
- What happens when a device's user-agent string is empty or unparseable? (DeviceService fingerprint helper should produce a fallback fingerprint)
- What happens when `complete_session` is called but the session has already expired via TTL? (Lua script returns no-session indicator; service returns None)
- What happens when rate limit counters overflow? (Redis INCR handles 64-bit integers; practical overflow is impossible)
- What happens when the interaction buffer grows very large between flushes? (RPUSH is O(1); no concern for the service layer, flushing is handled by the sync task)

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: Test suite MUST create 5 new test files: `test_session_service.py`, `test_game_session_service.py`, `test_otp_service.py`, `test_rate_limiter.py`, `test_device_service.py`
- **FR-002**: All tests MUST use real Redis at `redis://127.0.0.1:13000` with per-test prefix isolation (not mocked Redis)
- **FR-003**: All tests MUST clean up their Redis keys after each test using the existing `cleanup_keys` auto-fixture from `conftest.py`
- **FR-004**: Tests MUST exercise actual Lua scripts (START_SESSION_SCRIPT, SESSION_COMPLETE_SCRIPT, RATE_LIMIT_SCRIPT, REGISTER_DEVICE_SCRIPT) against real Redis to verify atomic behavior
- **FR-005**: Tests MUST mock only the `FrappeClient` boundary (no real HTTP calls to Frappe)
- **FR-006**: Tests MUST verify correct Redis key patterns, TTL values, and data structures for each service
- **FR-007**: OTP tests MUST verify rate limiting per phone and per IP, attempt counting, cooldown enforcement, and single-use token consumption
- **FR-008**: Game session tests MUST verify that the `complete_session` Lua script atomically sets progress bits, marks dirty set, and pushes interactions
- **FR-009**: Rate limiter tests MUST verify the sliding window pattern where TTL is set only on the first increment (count == 1)
- **FR-010**: Device service tests MUST verify all 4 Lua script code paths: existing device, fingerprint match, new device, and limit exceeded
- **FR-011**: All tests MUST follow the existing test patterns established in Phase 2 (conftest fixtures, prefix isolation, async test functions)
- **FR-012**: Test suite MUST produce approximately 39 tests across the 5 files
- **FR-013**: All tests MUST pass when run via `python3 -m pytest fastapi_app/tests/ -v`
- **FR-014**: Session service tests MUST verify the JSON session format `{fid, plan}` and legacy plain-string format handling
- **FR-015**: OTP tests MUST use the `StaticOTPProvider` (always "1111") or a mock provider, never send real SMS

### Key Entities

- **Session**: A user's authentication session stored as JSON in Redis, containing a family_id (UUID for session supersession detection) and plan_id
- **Game Session**: An active gameplay session stored as a Redis hash with lesson/subject context, 1-hour TTL, tracking which lesson a player is currently playing
- **OTP Pending Registration**: Temporary registration state stored in Redis with OTP code, attempt counter, and 5-minute TTL
- **Rate Limit Counter**: An atomic counter per IP or account with a sliding window TTL, enforcing request limits
- **Device Registration**: A player's registered device stored in a Redis hash with fingerprint, name, platform, and push token fields
- **Reset Token**: A single-use token for password reset, stored in Redis with 15-minute TTL, consumed (deleted) on first use

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: All 39 tests pass on first run with zero flaky failures
- **SC-002**: Test execution completes in under 10 seconds for the full Phase 3 suite
- **SC-003**: Every Lua script code path is exercised by at least one test (START_SESSION, SESSION_COMPLETE, RATE_LIMIT, REGISTER_DEVICE, OTP rate limit)
- **SC-004**: 100% of public methods on all 5 services have at least one test
- **SC-005**: All existing Phase 1 and Phase 2 tests continue to pass without modification
- **SC-006**: Test isolation is verified — running any single test file in isolation produces the same results as running the full suite
- **SC-007**: No test leaves orphaned Redis keys after execution (verified by the auto-cleanup fixture)

## Assumptions

- The existing `conftest.py` fixtures (redis_client, test_prefix, cleanup_keys, mock_frappe, make_player_token, make_admin_token) are sufficient and do not need modification for Phase 3
- Redis at `redis://127.0.0.1:13000` is available and running during test execution
- The `user-agents` PyPI package (used by DeviceService for fingerprint generation) is already installed in the test environment
- The `StaticOTPProvider` (always "1111") is acceptable for tests; no real SMS gateway integration is needed
- Lua scripts are tested by running them through the service methods, not by testing the raw Lua code directly
- The `GAME_SESSION_TTL` constant (3600s) and rate limit defaults (IP: 10/min, Account: 5/min) match the values in the source code at the time of implementation
