# Tasks: Core Endpoint Tests

**Input**: Design documents from `/specs/013-core-endpoint-tests/`
**Prerequisites**: plan.md, spec.md, research.md, data-model.md, contracts/endpoint-test-contracts.md, quickstart.md

**Tests**: This feature IS a test suite — all implementation tasks produce test code.

**Organization**: Tasks grouped by user story. Each story produces one test file and can be implemented/verified independently after Phase 2 completes.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependencies)
- **[Story]**: US1–US6 mapping to spec.md user stories

## Path Conventions

- All test files: `fastapi_app/tests/`
- Shared fixtures: `fastapi_app/tests/conftest.py`
- Endpoint modules under test: `fastapi_app/api/v1/endpoints/`

---

## Phase 1: Setup

**Purpose**: No setup needed — project structure, dependencies, and test infrastructure (conftest.py, 131 passing service tests) already exist from Phases 1–4.

*Skipped.*

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Fix the session key prefix mismatch bug (CF-001) and add Redis seeding helpers required by ALL endpoint test files.

**⚠️ CRITICAL**: No endpoint test file can work until this phase is complete. The `authed_client` and `admin_client` fixtures currently seed sessions at the wrong Redis key, causing all authenticated requests to return 401.

- [x] T001 Fix session key prefix mismatch in `authed_client` and `admin_client` fixtures in `fastapi_app/tests/conftest.py`
  - Change `authed_client` (line 269): seed at `memora:session:{player_id}` instead of `{test_prefix}memora:session:{player_id}`
  - Change `admin_client` (line 313): seed at `memora:session:{email}` instead of `{test_prefix}memora:session:{email}`
  - Add explicit `await redis_client.delete(session_key)` in both fixture teardowns (after the yield, before header cleanup)
  - Use unique player IDs per fixture invocation: `player_id = f"PLAYER-TEST-{uuid4().hex[:8]}"` instead of hardcoded `"PLAYER-TEST-001"`
  - Reference: research.md R-001, R-005

- [x] T002 Add Redis seeding helper functions to `fastapi_app/tests/conftest.py`
  - `make_hierarchy_json(subject_id, has_free_content=False, lesson_count=1, **overrides)` → returns dict matching data-model.md MinimalHierarchy schema
  - `seed_hierarchy(redis, subject_id, hierarchy_json=None, **overrides)` → `redis.set("memora:hierarchy:{subject_id}", json.dumps(data), ex=3600)`
  - `seed_game_session(redis, user_id, lesson_id, subject_id, **overrides)` → `redis.hset("memora:gamesession:{user_id}", mapping={...})`
  - `seed_settings(redis)` → `redis.set("memora:settings:gamification", json.dumps(data))` with defaults from data-model.md GamificationSettings
  - `seed_wallet(redis, player_id, xp=0, streak=0)` → `redis.hset("memora:wallet:{player_id}", mapping={...})`
  - `seed_access_grants(redis, player_id, keys)` → `redis.sadd("memora:access:{player_id}", *keys)`
  - `cleanup_player_keys(redis, player_id)` → SCAN+DELETE all `memora:*{player_id}*` keys (session, wallet, access, progress, stats, gamesession)
  - All helpers are plain async functions (not fixtures) — importable by test files
  - Reference: data-model.md, research.md R-003

- [x] T003 Verify existing 131 tests still pass after conftest.py changes by running `python3 -m pytest fastapi_app/tests/ -v --tb=short`
  - All 131 service tests from Phases 1–4 must pass
  - Zero regressions from session key fix and helper additions

**Checkpoint**: Foundation ready — all endpoint test files can now be implemented. Phases 3–8 can proceed in parallel (different files, no cross-dependencies).

---

## Phase 3: User Story 1 — Health Check Verification (Priority: P1) 🎯 MVP

**Goal**: Verify that health endpoints accurately report service availability without requiring authentication.

**Independent Test**: `python3 -m pytest fastapi_app/tests/test_health_endpoints.py -v`

### Implementation

- [x] T004 [P] [US1] Create `fastapi_app/tests/test_health_endpoints.py` with 4 tests
  - `test_liveness_ok`: GET `/api/v1/health/live` → 200, body has `status="alive"` and `api_version="v1"`
  - `test_liveness_no_auth_required`: GET `/api/v1/health/live` without Authorization header → 200
  - `test_readiness_ok`: GET `/api/v1/health/ready` → 200, `dependencies.redis="ok"`
  - `test_readiness_redis_down`: Mock `redis.ping()` to raise `ConnectionError` → 503, `dependencies.redis="unreachable"`
  - Uses: `app_client` fixture (no auth needed)
  - Reference: contracts/endpoint-test-contracts.md §1

**Checkpoint**: Health endpoint tests pass. Infrastructure validated end-to-end via httpx + ASGI transport.

---

## Phase 4: User Story 5 — Wallet Retrieval Verification (Priority: P2)

**Goal**: Verify wallet endpoints return correct XP/streak data and enforce admin-only access for player lookups.

**Independent Test**: `python3 -m pytest fastapi_app/tests/test_wallet_endpoints.py -v`

### Implementation

- [x] T005 [P] [US5] Create `fastapi_app/tests/test_wallet_endpoints.py` with 4 tests
  - `test_get_own_wallet`: Seed wallet hash via `seed_wallet(redis, player_id, xp=150, streak=3)` → GET `/api/v1/wallet` → 200, verify xp=150, streak=3
  - `test_empty_wallet_defaults`: No wallet seeded → GET `/api/v1/wallet` → 200, xp=0, streak=0 (default hydration)
  - `test_admin_get_player_wallet`: Seed wallet for target player → admin GET `/api/v1/wallet/{player_id}` → 200
  - `test_non_admin_forbidden`: Player client GET `/api/v1/wallet/{player_id}` → 403
  - Uses: `authed_client`, `admin_client`, `redis_client`, `seed_wallet`, `cleanup_player_keys`
  - Cleanup: Each test cleans up `memora:wallet:{player_id}` keys in teardown
  - Reference: contracts/endpoint-test-contracts.md §5

**Checkpoint**: Wallet tests pass. Authenticated request pattern and admin role enforcement validated.

---

## Phase 5: User Story 6 — Access Grant Management Verification (Priority: P2)

**Goal**: Verify admin-only access CRUD endpoints handle grants, revocations, idempotency, and empty key validation.

**Independent Test**: `python3 -m pytest fastapi_app/tests/test_access_endpoints.py -v`

### Implementation

- [x] T006 [P] [US6] Create `fastapi_app/tests/test_access_endpoints.py` with 6 tests
  - `test_admin_grant_access`: Admin POST `/api/v1/access/grants` with `{"player_id": "...", "content_keys": ["SUB-MATH"]}` → 200, `granted=1`
  - `test_grant_idempotent`: Grant same key twice → second returns `granted=0`
  - `test_grant_empty_keys`: Admin POST with empty `content_keys: []` → 400
  - `test_admin_revoke_access`: Grant then DELETE `/api/v1/access/grants` → 200, `revoked=1`
  - `test_admin_list_grants`: Grant keys then GET `/api/v1/access/grants/{player_id}` → 200 with grants list and count
  - `test_non_admin_forbidden`: Player client calls all 3 routes → 403 for each
  - Uses: `admin_client`, `authed_client`, `redis_client`, `cleanup_player_keys`
  - Cleanup: Each test cleans up `memora:access:{player_id}` keys
  - Reference: contracts/endpoint-test-contracts.md §6

**Checkpoint**: Access grant tests pass. Admin CRUD pattern and idempotency validated.

---

## Phase 6: User Story 2 — Authentication Flow Verification (Priority: P1)

**Goal**: Verify all 10 auth routes: player login, admin login, token refresh, registration (3-step), and password reset (3-step) with rate limiting, device tracking, and anti-enumeration.

**Independent Test**: `python3 -m pytest fastapi_app/tests/test_auth_endpoints.py -v`

### Implementation

- [x] T007 [US2] Create `fastapi_app/tests/test_auth_endpoints.py` with player login tests (7 tests)
  - File setup: imports, `pytestmark = pytest.mark.asyncio`, helper constants
  - `test_player_login_success`: Mock `get_frappe_client` → mock `.call()` returns player profile dict → POST `/api/v1/auth/player/login` with `X-Device-ID` header → 200, verify `access_token`, `refresh_token`, `profile`
  - `test_player_login_bad_credentials`: Mock `.call()` raises `FrappeAPIError` → 401
  - `test_player_login_missing_device_id`: No `X-Device-ID` header → 400 `DEVICE_ID_REQUIRED`
  - `test_player_login_rate_limited`: Exhaust rate limit by sending many requests → 429 + `Retry-After` header
  - `test_player_login_creates_session`: After login, verify Redis session key `memora:session:{player_id}` exists with `fid`
  - `test_player_login_kicks_old_session`: Login twice, verify old session's `fid` replaced by new one
  - `test_player_login_registers_device`: After login, verify device hash exists in Redis
  - Mock pattern: `@patch("fastapi_app.api.v1.endpoints.auth.get_frappe_client")` → return AsyncMock with `.call()` configured
  - Cleanup: Rate limit keys (`memora:rate:*`), session keys, device keys for test player
  - Reference: contracts/endpoint-test-contracts.md §2 (POST /auth/player/login), research.md R-002

- [x] T008 [US2] Add admin login and token refresh tests (5 tests) to `fastapi_app/tests/test_auth_endpoints.py`
  - `test_admin_login_success`: `@patch("fastapi_app.api.v1.endpoints.auth.FrappeAuthService")` → mock `.verify_credentials()` returns `(FrappeUser, {})` → POST `/api/v1/auth/admin/login` → 200 + tokens
  - `test_admin_login_invalid_credentials`: Mock `.verify_credentials()` returns `(None, None)` → 401
  - `test_refresh_valid_token`: Create refresh token + seed Redis session with matching `fid` → POST `/api/v1/auth/refresh` → 200 + new tokens
  - `test_refresh_expired_token`: Create token with negative expiry → 401
  - `test_refresh_family_id_mismatch`: Seed Redis session with different `fid` than token → 401
  - Mock pattern: `@patch("fastapi_app.api.v1.endpoints.auth.FrappeAuthService")` for admin, real Redis session for refresh
  - Reference: contracts/endpoint-test-contracts.md §2 (POST /auth/admin/login, POST /auth/refresh)

- [x] T009 [US2] Add registration flow tests (6 tests) to `fastapi_app/tests/test_auth_endpoints.py`
  - `test_registration_options`: Mock `get_frappe_client` → `.call()` returns options dict → GET `/api/v1/auth/registration-options` → 200
  - `test_register_success`: Mock `.call()` for `check_phone_exists` → `{exists: false}` → POST `/api/v1/auth/player/register` → 200, verify `pending_id`
  - `test_register_duplicate_phone`: Mock `check_phone_exists` → `{exists: true}` → 409
  - `test_register_verify_valid_otp`: Pre-seed OTP + pending registration in Redis → mock `register_player` → POST `/api/v1/auth/player/register/verify` with `X-Device-ID` → 200 + tokens
  - `test_register_verify_invalid_otp`: Pre-seed pending, send wrong OTP → error response
  - `test_register_resend`: Pre-seed pending registration → POST `/api/v1/auth/player/register/resend` → 200
  - Redis seeding: OTP keys at `memora:otp:{pending_id}`, pending at `memora:pending_reg:{pending_id}`
  - Cleanup: OTP keys, pending registration keys, session keys if created
  - Reference: contracts/endpoint-test-contracts.md §2 (registration routes), research.md R-002

- [x] T010 [US2] Add password reset flow tests (5 tests) to `fastapi_app/tests/test_auth_endpoints.py`
  - `test_password_reset_request_anti_enumeration`: Mock `check_phone_exists` for both existing and non-existing phone → POST `/api/v1/auth/player/password-reset/request` → both return 200 (anti-enumeration)
  - `test_password_reset_verify_valid`: Pre-seed reset OTP in Redis → POST `/api/v1/auth/player/password-reset/verify` → 200 + `reset_token`
  - `test_password_reset_verify_invalid`: Wrong OTP → error
  - `test_password_reset_confirm_success`: Pre-seed reset token in Redis, mock Frappe calls → POST `/api/v1/auth/player/password-reset/confirm` → 200
  - `test_password_reset_confirm_reused_token`: Use token once (success), use again → 401 (single-use enforcement)
  - Redis seeding: Reset OTP at `memora:reset_otp:{mobile}`, reset token at `memora:reset_token:{token}`
  - Cleanup: All reset-related Redis keys
  - Reference: contracts/endpoint-test-contracts.md §2 (password reset routes), research.md R-002

**Checkpoint**: All ~23 auth tests pass. All 10 auth routes verified with success + error paths.

---

## Phase 7: User Story 4 — Progress Retrieval Verification (Priority: P2)

**Goal**: Verify 6 progress endpoints return correct subject summaries, track listings, unit details, and lesson completion while enforcing access control with free content bypass.

**Independent Test**: `python3 -m pytest fastapi_app/tests/test_progress_endpoints.py -v`

### Implementation

- [x] T011 [US4] Create `fastapi_app/tests/test_progress_endpoints.py` with summary and auth tests (2 tests)
  - File setup: imports, `pytestmark = pytest.mark.asyncio`, import helpers from conftest
  - `test_progress_summary`: Seed access grants + hierarchy for 1 subject → GET `/api/v1/progress/` → 200, verify list with `subject_id`, `percentage`, `completed`, `total`
  - `test_unauthenticated`: No Bearer token → GET `/api/v1/progress/` → 401
  - Uses: `authed_client`, `app_client`, `redis_client`, `seed_hierarchy`, `seed_access_grants`
  - Reference: contracts/endpoint-test-contracts.md §4

- [x] T012 [US4] Add subject progress and access control tests (4 tests) to `fastapi_app/tests/test_progress_endpoints.py`
  - `test_subject_progress`: Seed hierarchy + access grant → GET `/api/v1/progress/{subject_id}` → 200, verify tracks array
  - `test_subject_not_found`: No hierarchy seeded → GET `/api/v1/progress/SUB-NONEXIST` → 404
  - `test_access_denied`: Seed hierarchy (no free content) + no access grant → GET → 403 `NO_ACCESS`
  - `test_free_content_bypass`: Seed hierarchy with `has_free_content=True` + no access grant → GET → 200
  - Each test seeds `memora:hierarchy:{subject_id}` and optionally `memora:access:{player_id}`
  - Reference: contracts/endpoint-test-contracts.md §4, research.md R-004

- [x] T013 [US4] Add track, unit, and lesson detail tests (4 tests) to `fastapi_app/tests/test_progress_endpoints.py`
  - `test_track_listing`: Seed hierarchy + access → GET `/api/v1/progress/{subject}/tracks` → 200, verify list with `track_id`
  - `test_track_detail`: Seed hierarchy + access → GET `/api/v1/progress/{subject}/tracks/{track_id}` → 200, verify units list
  - `test_unit_detail`: Seed hierarchy + access → GET `/api/v1/progress/{subject}/tracks/{track_id}/units/{unit_id}` → 200, verify topics list
  - `test_lesson_completion`: Seed hierarchy + access + progress bitmap → GET `/api/v1/progress/{subject}/topics/{topic_id}/lessons` → 200, verify lessons with `completed` flags
  - Progress bitmap: `redis.setbit(f"memora:progress:{player_id}:{subject_id}:v1", bit_index, 1)` for completed lessons
  - Cleanup: hierarchy, access, progress bitmap keys
  - Reference: contracts/endpoint-test-contracts.md §4

**Checkpoint**: All 10 progress tests pass. Access control, free content bypass, and drill-down hierarchy verified.

---

## Phase 8: User Story 3 — Game Session Lifecycle Verification (Priority: P1)

**Goal**: Verify session start/end lifecycle: access control, XP awards, replay detection, streak updates, leaderboard writes, and dirty set marking.

**Independent Test**: `python3 -m pytest fastapi_app/tests/test_session_endpoints.py -v`

### Implementation

- [x] T014 [US3] Create `fastapi_app/tests/test_session_endpoints.py` with get current and unauthenticated tests (3 tests)
  - File setup: imports, `pytestmark = pytest.mark.asyncio`, import helpers from conftest
  - `test_get_current_active`: Seed game session hash via `seed_game_session()` → GET `/api/v1/sessions/current` → 200, verify `session_id`, `lesson_id`, `subject_id`
  - `test_get_current_none`: No game session → GET `/api/v1/sessions/current` → 404 `NO_ACTIVE_SESSION`
  - `test_unauthenticated`: No Bearer → POST `/api/v1/sessions/start` → 401
  - Uses: `authed_client`, `app_client`, `redis_client`, `seed_game_session`, `cleanup_player_keys`
  - Reference: contracts/endpoint-test-contracts.md §3 (GET /sessions/current)

- [x] T015 [US3] Add session start tests (5 tests) to `fastapi_app/tests/test_session_endpoints.py`
  - `test_start_success`: Seed hierarchy + access grant → POST `/api/v1/sessions/start` with `{"lesson_id": "...", "subject_id": "..."}` → 200, verify `session_id`
  - `test_start_nonexistent_subject`: No hierarchy → POST → 404
  - `test_start_no_access`: Seed hierarchy (no free content) + no grant → POST → 403 `NO_ACCESS`
  - `test_start_free_bypass`: Seed hierarchy with free content + no grant → POST → 200
  - `test_start_nonexistent_lesson`: Seed hierarchy but use non-existent lesson_id → POST → 404
  - Each test seeds `memora:hierarchy:{subject_id}` and optionally `memora:access:{player_id}`
  - Mock: `mock_frappe.call` for any FrappeClient calls during start flow
  - Cleanup: game session keys, hierarchy keys, access keys
  - Reference: contracts/endpoint-test-contracts.md §3 (POST /sessions/start), research.md R-004

- [x] T016 [US3] Add session end tests (7 tests) to `fastapi_app/tests/test_session_endpoints.py`
  - Full state seeding required for each end test: game session hash + hierarchy + gamification settings + wallet hash + optionally progress bitmap
  - `test_end_success`: Seed full state → POST `/api/v1/sessions/end` with stages array → 200, verify `xp_awarded > 0`
  - `test_end_no_session`: No game session → POST → 403
  - `test_end_replay_detection`: Set completion bit before ending → 200, verify `is_replay=True`
  - `test_end_streak_update`: Complete session → verify `streak >= 1` in response
  - `test_end_xp_awarded`: Fresh completion → verify `xp_awarded > 0`
  - `test_end_marks_dirty`: After end → verify player ID in `memora:dirty:wallets` set
  - `test_end_leaderboard_update`: After end → verify ZADD to leaderboard sorted set (`memora:leaderboard:*`)
  - Lua scripts (`SESSION_COMPLETE_SCRIPT`, `STREAK_UPDATE_SCRIPT`) execute on real Redis — this is the key integration value
  - Cleanup: game session, hierarchy, settings, wallet, progress, dirty set, leaderboard, stats keys
  - Reference: contracts/endpoint-test-contracts.md §3 (POST /sessions/end), research.md R-003

**Checkpoint**: All 15 session tests pass. Full game loop verified: start → access check → end → XP → replay → streak → leaderboard → dirty.

---

## Phase 9: Polish & Cross-Cutting Concerns

**Purpose**: Final validation across all test files.

- [x] T017 Run full test suite `python3 -m pytest fastapi_app/tests/ -v --tb=short` and verify all ~195 tests pass (131 existing + ~64 new)
- [x] T018 Verify compliance: no `time.sleep` in endpoint tests, no imports from excluded scope (voucher/allocation), total runtime under 30 seconds

---

## Dependencies & Execution Order

### Phase Dependencies

- **Phase 2 (Foundational)**: No dependencies — starts immediately. **BLOCKS all Phases 3–8.**
- **Phase 3 (US1 Health)**: Depends on Phase 2 only
- **Phase 4 (US5 Wallet)**: Depends on Phase 2 only
- **Phase 5 (US6 Access)**: Depends on Phase 2 only
- **Phase 6 (US2 Auth)**: Depends on Phase 2 only
- **Phase 7 (US4 Progress)**: Depends on Phase 2 only
- **Phase 8 (US3 Session)**: Depends on Phase 2 only
- **Phase 9 (Polish)**: Depends on all Phases 3–8

### User Story Independence

All 6 user story phases (3–8) are **fully independent** — each produces a separate test file with no imports from other endpoint test files. They can be implemented in any order or in parallel.

### Recommended Implementation Order (within sequential execution)

If implementing sequentially, follow this order (simple → complex) as recommended by plan.md:

1. Phase 3: Health (4 tests) — validates httpx + ASGI infrastructure
2. Phase 4: Wallet (4 tests) — validates authenticated request pattern
3. Phase 5: Access (6 tests) — validates admin CRUD pattern
4. Phase 6: Auth (23 tests) — complex mocking patterns
5. Phase 7: Progress (10 tests) — hierarchy + access control
6. Phase 8: Session (15 tests) — full game loop, Lua scripts

### Within Each User Story Phase

- File creation + imports first (T007 creates the file, T008–T010 add to it)
- Simpler tests before complex tests
- Happy path before error paths

### Parallel Opportunities

- **After Phase 2**: Phases 3–8 can ALL start in parallel (6 independent files)
- **Within Phase 6 (Auth)**: T007 must complete first (creates file), T008–T010 can then proceed sequentially
- **Within Phase 7 (Progress)**: T011 first (creates file), T012–T013 sequentially
- **Within Phase 8 (Session)**: T014 first (creates file), T015–T016 sequentially
- **Phases 3, 4, 5**: Single-task phases, trivially parallelizable with each other

---

## Parallel Example: All Simple Stories

```bash
# After Phase 2 completes, launch all single-file stories together:
Task: T004 "Create test_health_endpoints.py" (fastapi_app/tests/test_health_endpoints.py)
Task: T005 "Create test_wallet_endpoints.py" (fastapi_app/tests/test_wallet_endpoints.py)
Task: T006 "Create test_access_endpoints.py" (fastapi_app/tests/test_access_endpoints.py)
# These produce 14 tests in parallel across 3 files
```

---

## Implementation Strategy

### MVP First (Phase 2 + Phase 3 Only)

1. Complete Phase 2: Fix conftest.py + add helpers
2. Complete Phase 3: Health endpoint tests (4 tests)
3. **STOP and VALIDATE**: Run `python3 -m pytest fastapi_app/tests/ -v` — all 135 tests pass
4. This proves the entire httpx + ASGI + dependency override infrastructure works

### Incremental Delivery

1. Phase 2 (Foundational) → conftest fixed, helpers ready
2. Phase 3 (Health) → 4 tests, infrastructure proven ✅
3. Phase 4 (Wallet) + Phase 5 (Access) → +10 tests, auth + admin patterns proven ✅
4. Phase 6 (Auth) → +23 tests, all auth routes verified ✅
5. Phase 7 (Progress) → +10 tests, hierarchy + access control verified ✅
6. Phase 8 (Session) → +15 tests, full game loop verified ✅
7. Phase 9 (Polish) → full suite verification ✅

### Complexity Ramp

Each phase builds on mock/seed patterns from the previous:
- **Health**: No auth, no mocks → simplest
- **Wallet**: Auth only, seed Redis hash → introduces `authed_client`
- **Access**: Admin auth, Redis set operations → introduces `admin_client`
- **Auth**: `unittest.mock.patch`, multiple mock patterns → most diverse mocking
- **Progress**: Hierarchy JSON seeding, access control matrix → combines patterns
- **Session**: Full state seeding, Lua scripts, end-to-end orchestration → most complex

---

## Notes

- [P] tasks = different files, no dependencies on incomplete tasks
- [Story] label maps to user stories from spec.md (US1–US6)
- All tests use real Redis at `redis://127.0.0.1:13000` with prefix isolation (never FLUSHDB)
- FrappeClient is always mocked at the `.call()` boundary — no real Frappe server
- Auth endpoints use inline service construction → mock via `unittest.mock.patch`, not `app.dependency_overrides`
- Each test file must clean up production-prefix Redis keys it creates (unique player IDs + explicit teardown)
- Commit after each phase or logical group
