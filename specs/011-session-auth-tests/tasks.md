# Tasks: Session + Auth Service Tests

**Input**: Design documents from `/specs/011-session-auth-tests/`
**Prerequisites**: plan.md (required), spec.md (required), research.md, data-model.md, contracts/test-contracts.md, quickstart.md

**Tests**: This IS a test-only feature — all tasks create test files. No source code modifications.

**Organization**: Tasks are grouped by user story (one test file per story). All stories are independent and can be implemented in any order after Phase 1.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependencies)
- **[Story]**: Which user story this task belongs to (e.g., US1, US2, US3)
- Include exact file paths in descriptions

## Path Conventions

- **Test files**: `fastapi_app/tests/test_*.py` (existing directory)
- **Services under test**: `fastapi_app/services/*.py` (READ ONLY)
- **Existing fixtures**: `fastapi_app/tests/conftest.py` (READ ONLY)
- **Constants**: `fastapi_app/core/constants.py` (READ ONLY)
- **Contracts**: `specs/011-session-auth-tests/contracts/test-contracts.md`

---

## Phase 1: Setup (Verify Environment)

**Purpose**: Confirm test infrastructure and service imports work before writing tests

- [x] T001 Verify existing conftest.py fixtures (redis_client, test_prefix, cleanup_keys, mock_frappe) are compatible with Phase 3 services by importing all 5 service classes and confirming Redis connectivity at `redis://127.0.0.1:13000`

**Checkpoint**: All service classes importable, Redis reachable, conftest.py fixtures confirmed

---

## Phase 2: User Story 1 — Game Session Lifecycle Tests (Priority: P1)

**Goal**: Create 8 tests verifying GameSessionService manages the full session lifecycle including Lua script-driven atomic operations (START_SESSION_SCRIPT, SESSION_COMPLETE_SCRIPT)

**Independent Test**: `python3 -m pytest fastapi_app/tests/test_game_session_service.py -v` — all 8 tests pass

### Implementation

- [x] T002 [US1] Create `fastapi_app/tests/test_game_session_service.py` with module constants (TEST_USER, TEST_SUBJECT, TEST_VERSION, TEST_LESSON, TEST_DEVICE), `game_session_service` fixture constructing `GameSessionService(redis_client, key_prefix=test_prefix)`, and autouse `cleanup_global_keys` fixture that removes DIRTY_PROGRESS_KEY members and drains INTERACTION_BUFFER_KEY after each test
- [x] T003 [US1] Implement `TestStartSession` and `TestGetSession` classes with 5 tests: start creates hash with TTL=3600 (TC-GS-01), start force-closes existing session atomically (TC-GS-02), get returns GameSession model (TC-GS-03), get returns None when no session (TC-GS-04), end returns data and deletes hash (TC-GS-05) — all verify Redis state directly via `redis_client.hgetall()` and `redis_client.ttl()` in `fastapi_app/tests/test_game_session_service.py`
- [x] T004 [US1] Implement `TestCompleteSession` class with 3 tests: first completion sets progress bit and marks dirty set (TC-GS-06), replay detection returns is_replay=True (TC-GS-07), interaction buffer receives pushed JSON strings (TC-GS-08) — verify via `redis_client.getbit()`, `redis_client.sismember(DIRTY_PROGRESS_KEY, ...)`, and `redis_client.lrange(INTERACTION_BUFFER_KEY, ...)` in `fastapi_app/tests/test_game_session_service.py`
- [x] T005 [US1] Run `python3 -m pytest fastapi_app/tests/test_game_session_service.py -v` and verify all 8 tests pass with zero failures

**Checkpoint**: Game session lifecycle fully tested — Lua scripts verified against real Redis

---

## Phase 3: User Story 2 — OTP Verification Tests (Priority: P1)

**Goal**: Create 12 tests verifying OTPService handles registration, verification, rate limiting, cooldown, password reset, and single-use token consumption

**Independent Test**: `python3 -m pytest fastapi_app/tests/test_otp_service.py -v` — all 12 tests pass

### Implementation

- [x] T006 [US2] Create `fastapi_app/tests/test_otp_service.py` with module constants (TEST_MOBILE, TEST_IP, TEST_PASSWORD, TEST_PLAN), `otp_service` fixture constructing `OTPService(redis_client, key_prefix=test_prefix, otp_provider=StaticOTPProvider())` with mock FrappeClient, and helper function to clear phone reservation between rate-limit test iterations
- [x] T007 [US2] Implement `TestRegistrationFlow` class with 6 tests: create pending returns pending_id with Redis state (TC-OTP-01), verify correct OTP returns data and cleans up (TC-OTP-02), verify wrong OTP increments attempts (TC-OTP-03), max attempts exhausted deletes pending (TC-OTP-04), resend cooldown blocks rapid resend with HTTPException 429 (TC-OTP-05), expired/missing pending raises HTTPException 401 (TC-OTP-12) in `fastapi_app/tests/test_otp_service.py`
- [x] T008 [US2] Implement `TestOTPRateLimits` class with 2 tests: phone rate limit blocks after PHONE_LIMIT=3 requests (TC-OTP-06), IP rate limit blocks after IP_LIMIT=10 requests (TC-OTP-07) — must clear phone_reserved keys between iterations to allow repeated create_pending_registration calls in `fastapi_app/tests/test_otp_service.py`
- [x] T009 [US2] Implement `TestPasswordReset` class with 4 tests: create_password_reset stores OTP in Redis (TC-OTP-08), anti-enumeration silently skips when phone_exists=False (TC-OTP-09), verify_password_reset_otp returns single-use token with 900s TTL (TC-OTP-10), validate_reset_token consumed on first use and second call raises 401 (TC-OTP-11) in `fastapi_app/tests/test_otp_service.py`
- [x] T010 [US2] Run `python3 -m pytest fastapi_app/tests/test_otp_service.py -v` and verify all 12 tests pass with zero failures

**Checkpoint**: OTP security model fully tested — rate limits, cooldowns, single-use tokens verified

---

## Phase 4: User Story 3 — Session Management Tests (Priority: P2)

**Goal**: Create 5 tests verifying SessionService manages authentication sessions with family_id-based supersession detection and JSON format storage

**Independent Test**: `python3 -m pytest fastapi_app/tests/test_session_service.py -v` — all 5 tests pass

### Implementation

- [x] T011 [US3] Create `fastapi_app/tests/test_session_service.py` with module constants (TEST_USER, TEST_PLAN), `session_service` fixture constructing `SessionService(redis_client, key_prefix=test_prefix)`, and implement all 5 tests in `TestSessionManagement` class: create stores JSON `{fid, plan}` with 30-day TTL (TC-SS-01), validate matching family_id returns (True, plan) (TC-SS-02), validate mismatched returns (False, None) (TC-SS-03), invalidate deletes key (TC-SS-04), create overwrites previous session (TC-SS-05) — verify Redis state via `redis_client.get()` and `json.loads()` in `fastapi_app/tests/test_session_service.py`
- [x] T012 [US3] Run `python3 -m pytest fastapi_app/tests/test_session_service.py -v` and verify all 5 tests pass with zero failures

**Checkpoint**: Session management tested — family_id supersession and JSON format verified

---

## Phase 5: User Story 4 — Rate Limiter Tests (Priority: P2)

**Goal**: Create 6 tests verifying RateLimiter enforces per-IP and per-account sliding window limits using atomic Lua scripts

**Independent Test**: `python3 -m pytest fastapi_app/tests/test_rate_limiter.py -v` — all 6 tests pass

### Implementation

- [x] T013 [US4] Create `fastapi_app/tests/test_rate_limiter.py` with module constants (TEST_IP, TEST_ACCOUNT), `rate_limiter` fixture constructing `RateLimiter(redis_client, key_prefix=test_prefix)`, and implement all 6 tests: first request allowed returns (True, 0, "") (TC-RL-01), IP limit exceeded after 10 requests returns (False, retry_after, "ip") (TC-RL-02), account limit exceeded after 5 requests returns (False, retry_after, "account") (TC-RL-03), get_remaining returns correct ip/account remaining counts (TC-RL-04), account normalized to lowercase shares counter (TC-RL-05), no account skips account check (TC-RL-06) in `fastapi_app/tests/test_rate_limiter.py`
- [x] T014 [US4] Run `python3 -m pytest fastapi_app/tests/test_rate_limiter.py -v` and verify all 6 tests pass with zero failures

**Checkpoint**: Rate limiter tested — sliding window, IP/account limits, lowercase normalization verified

---

## Phase 6: User Story 5 — Device Registration Tests (Priority: P2)

**Goal**: Create 8 tests verifying DeviceService manages multi-device registration with fingerprint matching via REGISTER_DEVICE_SCRIPT Lua script (4 code paths)

**Independent Test**: `python3 -m pytest fastapi_app/tests/test_device_service.py -v` — all 8 tests pass

### Implementation

- [x] T015 [US5] Create `fastapi_app/tests/test_device_service.py` with module constants (TEST_USER, TEST_DEVICE_ID, TEST_USER_AGENT with realistic iPhone UA string, MAX_DEVICES=3), `device_service` fixture constructing `DeviceService(redis_client, key_prefix=test_prefix)`, and a second unique UA string constant for fingerprint-mismatch scenarios
- [x] T016 [US5] Implement `TestRegisterDevice` class with 4 tests covering all Lua script paths: new device returns status="new" with 6 hash fields (TC-DS-01), existing device updates last_login returns status="existing" (TC-DS-02), different device_id with matching fingerprint replaces old device returns status="fingerprint_match" (TC-DS-03), max_devices exceeded with unique fingerprint returns success=False status="limit_exceeded" (TC-DS-04) in `fastapi_app/tests/test_device_service.py`
- [x] T017 [US5] Implement `TestDeviceManagement` class with 4 tests: get_devices returns list of DeviceInfo objects (TC-DS-05), remove_device deletes all 6 hash fields (TC-DS-06), validate_device returns True for registered device (TC-DS-07), validate_device returns False for unknown device (TC-DS-08) in `fastapi_app/tests/test_device_service.py`
- [x] T018 [US5] Run `python3 -m pytest fastapi_app/tests/test_device_service.py -v` and verify all 8 tests pass with zero failures

**Checkpoint**: Device registration tested — all 4 Lua script code paths and management operations verified

---

## Phase 7: Polish & Cross-Cutting Concerns

**Purpose**: Full suite validation, regression check, and isolation verification

- [x] T019 Run full Phase 3 suite (`python3 -m pytest fastapi_app/tests/test_game_session_service.py fastapi_app/tests/test_otp_service.py fastapi_app/tests/test_session_service.py fastapi_app/tests/test_rate_limiter.py fastapi_app/tests/test_device_service.py -v`) and verify all 39 tests pass in under 10 seconds
- [x] T020 Run full FastAPI test suite (`python3 -m pytest fastapi_app/tests/ -v`) to verify Phase 1+2+3 coexistence with zero regressions in existing tests
- [x] T021 Run each Phase 3 test file independently (5 separate pytest invocations) to verify test isolation — each file must produce identical results whether run alone or as part of the full suite

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: No dependencies — start immediately
- **US1 (Phase 2)**: Depends on Phase 1 only
- **US2 (Phase 3)**: Depends on Phase 1 only
- **US3 (Phase 4)**: Depends on Phase 1 only
- **US4 (Phase 5)**: Depends on Phase 1 only
- **US5 (Phase 6)**: Depends on Phase 1 only
- **Polish (Phase 7)**: Depends on ALL story phases (2–6) being complete

### User Story Dependencies

- **US1 (Game Session)**: Independent — no dependency on other stories
- **US2 (OTP)**: Independent — no dependency on other stories
- **US3 (Session)**: Independent — no dependency on other stories
- **US4 (Rate Limiter)**: Independent — no dependency on other stories
- **US5 (Device)**: Independent — no dependency on other stories

All 5 stories can execute in parallel after Phase 1 since each creates a separate file with no shared state.

### Within Each User Story

1. Create file with fixtures and constants
2. Implement test classes (sequentially within the same file)
3. Verify all tests pass
4. Story complete — move to next or work in parallel

### Parallel Opportunities

After Phase 1 completes, ALL five stories can run simultaneously:

```
Phase 1 (Setup) ──┬── Phase 2 (US1: Game Session) ──┐
                   ├── Phase 3 (US2: OTP)            ├── Phase 7 (Polish)
                   ├── Phase 4 (US3: Session)         │
                   ├── Phase 5 (US4: Rate Limiter)    │
                   └── Phase 6 (US5: Device)     ─────┘
```

---

## Parallel Example: All P2 Stories

```bash
# After Phase 1 completes, launch all P2 stories in parallel:
Agent A: "Create test_session_service.py with all 5 tests (T011)"
Agent B: "Create test_rate_limiter.py with all 6 tests (T013)"
Agent C: "Create test_device_service.py with fixtures + registration tests (T015, T016)"
```

---

## Implementation Strategy

### MVP First (P1 Stories Only)

1. Complete Phase 1: Setup verification
2. Complete Phase 2: US1 — Game Session (8 tests)
3. Complete Phase 3: US2 — OTP (12 tests)
4. **STOP and VALIDATE**: 20 tests passing, core gameplay + auth security covered
5. Proceed to P2 stories

### Incremental Delivery

1. Phase 1 → Environment ready
2. Phase 2 (US1) → 8 tests, game session lifecycle confidence
3. Phase 3 (US2) → +12 tests, OTP security confidence
4. Phase 4 (US3) → +5 tests, session management confidence
5. Phase 5 (US4) → +6 tests, rate limiting confidence
6. Phase 6 (US5) → +8 tests, device management confidence
7. Phase 7 → All 39 tests validated, suite regression-free

### Key References

| Task Group | Contract Reference | Data Model Reference |
|------------|-------------------|---------------------|
| US1 (Game Session) | TC-GS-01 through TC-GS-08 | §1 Game Session Hash, §10 Progress Bitmap |
| US2 (OTP) | TC-OTP-01 through TC-OTP-12 | §3 OTP Pending, §4 Phone Lock, §5 Reset State, §6 Reset Token, §8 Resend Cooldown |
| US3 (Session) | TC-SS-01 through TC-SS-05 | §2 Authentication Session |
| US4 (Rate Limiter) | TC-RL-01 through TC-RL-06 | §7 Rate Limit Counters |
| US5 (Device) | TC-DS-01 through TC-DS-08 | §9 Device Registry |

---

## Notes

- All services accept `key_prefix` parameter — pass `test_prefix` for isolation
- `DIRTY_PROGRESS_KEY` and `INTERACTION_BUFFER_KEY` are hardcoded global constants (not prefixed) — require explicit cleanup in game session tests
- OTP is always `"1111"` via `StaticOTPProvider` — no real SMS
- Rate limit Lua script sets TTL only on first INCR (count==1) — sliding window from first request
- Device Lua script has 4 branches: existing, fingerprint_match, new, limit_exceeded
- No source code modifications — all 5 service files are READ ONLY
- Must not modify existing conftest.py or Phase 1/2 test files
