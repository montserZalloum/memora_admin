# Implementation Plan: Core Endpoint Tests

**Branch**: `013-core-endpoint-tests` | **Date**: 2026-02-17 | **Spec**: [spec.md](spec.md)
**Input**: Feature specification from `/specs/013-core-endpoint-tests/spec.md`

## Summary

Implement ~64 endpoint integration tests across 6 test files covering the core FastAPI routes: health (2 routes), auth (10 routes), sessions (3 routes), progress (6 routes), wallet (2 routes), and access (3 routes). Tests use `httpx.AsyncClient` with ASGI transport, real Redis with prefix isolation, and mocked FrappeClient at the call boundary. A critical conftest.py bug (session key prefix mismatch) must be fixed first to enable authenticated endpoint testing.

## Technical Context

**Language/Version**: Python 3.11+ (Frappe v15 bench environment)
**Primary Dependencies**: pytest 8.4.2, pytest-asyncio 0.26.0, httpx 0.28.1, redis.asyncio (all pre-installed)
**Storage**: Redis at `redis://127.0.0.1:13000` (real, shared with Frappe — prefix isolation mandatory)
**Testing**: pytest + httpx.AsyncClient with ASGITransport, unittest.mock.AsyncMock for FrappeClient
**Target Platform**: Linux server (Frappe bench)
**Project Type**: Single project (FastAPI sidecar test suite)
**Performance Goals**: All ~64 tests complete in under 30 seconds total
**Constraints**: Never FLUSHDB, never import from excluded scope (voucher/library), never use time.sleep
**Scale/Scope**: ~64 tests across 6 files, covering 26 routes in 6 endpoint modules

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

### Pre-Research Check

| Principle | Status | Evidence |
|-----------|--------|----------|
| I. Source-of-Truth Awareness | PASS | Tests verify Redis state after endpoint calls (wallet dirty sets, access grants, session keys). FrappeClient mocked at boundary — hydration behavior tested via cache-miss paths. |
| II. Atomic Operation Integrity | PASS | Session end tests exercise real Lua scripts (`SESSION_COMPLETE_SCRIPT`, `STREAK_UPDATE_SCRIPT`) through endpoint flow — not decomposed into sequential steps. |
| III. Edge-Case-First Design | PASS | Each endpoint group has error-path tests: 401/403/404/429. Ratio: auth has 12 error tests for 13 happy paths. Session has 7 error for 8 happy. |
| IV. Test Isolation | PASS | Per-test Redis key namespace via `test_prefix`, unique player IDs, explicit cleanup. No `time.sleep`. No shared state. |
| V. Business Flow Completeness | PASS | Session endpoint tests cover start → access check → end → XP → streak → leaderboard → dirty set — complete lesson flow via HTTP. |

### Excluded Scope Compliance

- No test file imports from `services/voucher/`, `api/voucher.py`, or `api/allocation.py`
- No references to `Memora Voucher Batch`, `Memora Voucher Card`, `Memora Voucher Allocation`, or `Memora Voucher Redemption Log`

### Post-Design Re-Check

| Gate | Status | Notes |
|------|--------|-------|
| Gate 1: Pre-Merge | PASS | No `time.sleep`, no excluded imports, every endpoint has >=1 happy + >=1 error test |
| Gate 2: Coverage | PASS | All 26 routes across 6 modules have >=1 test. Lua scripts tested via session end flow. |
| Gate 3: Risk Coverage | PARTIAL | RISK-05 (family_id mismatch → 401) covered by `test_refresh_family_id_mismatch`. RISK-08 (access after flush) covered by default wallet/progress tests. Remaining risks (RISK-01,02,03,04,06,07,09,10) are service-level — already covered in Phases 2-4. |

## Project Structure

### Documentation (this feature)

```text
specs/013-core-endpoint-tests/
├── plan.md                                    # This file
├── research.md                                # Phase 0: 5 research items resolved
├── data-model.md                              # Phase 1: Test data structures + fixture mapping
├── quickstart.md                              # Phase 1: Implementation guide
├── contracts/
│   └── endpoint-test-contracts.md             # Phase 1: Route → test mapping with assertions
└── tasks.md                                   # Phase 2 output (created by /speckit.tasks)
```

### Source Code (repository root)

```text
fastapi_app/tests/
├── conftest.py                    # MODIFIED: Fix session key prefix, add helper fixtures
├── test_health_endpoints.py       # NEW: 4 tests for 2 health routes
├── test_auth_endpoints.py         # NEW: 25 tests for 10 auth routes
├── test_session_endpoints.py      # NEW: 15 tests for 3 session routes
├── test_progress_endpoints.py     # NEW: 10 tests for 6 progress routes
├── test_wallet_endpoints.py       # NEW: 4 tests for 2 wallet routes
└── test_access_endpoints.py       # NEW: 6 tests for 3 access routes
```

**Structure Decision**: All new test files go into the existing `fastapi_app/tests/` directory alongside the 19 service test files from Phases 1-4. No new directories needed.

## Critical Findings

### CF-001: Session Key Prefix Mismatch (BLOCKING)

**Status**: Resolved in research (R-001)

The `authed_client` and `admin_client` fixtures seed session data at `{test_prefix}memora:session:{user_id}`, but `get_current_user` in deps.py reads from `{settings.redis_key_prefix}session:{user_id}` = `memora:session:{user_id}`. Keys don't match → all authenticated endpoint tests would get 401.

**Fix**: Seed sessions at `memora:session:{user_id}` (production prefix) + explicit cleanup in fixture teardown. See research.md R-001 for full analysis.

### CF-002: Auth Endpoints Use Inline Service Construction

**Status**: Resolved in research (R-002)

Auth routes construct `RateLimiter`, `OTPService`, `DeviceService`, `WalletService`, `SessionService`, and `SettingsService` inline — not via dependency injection. Cannot use `app.dependency_overrides`.

**Fix**: Mock at `FrappeClient.call()` boundary. Services that use real Redis operate with production Redis (rate limiter, OTP, device, session). Use `unittest.mock.patch` for `FrappeAuthService.verify_credentials`.

### CF-003: Production-Prefix Keys From Endpoint Code Paths

**Status**: Resolved in research (R-005)

Endpoint code constructs service keys with `settings.redis_key_prefix` = `"memora:"`. These keys aren't caught by test_prefix cleanup.

**Fix**: Use unique player IDs per test + explicit cleanup helper. Tests at integration level appropriately use production key paths.

## Implementation Sequence

### Step 0: Fix conftest.py (BLOCKING prerequisite)

**Files modified**: `fastapi_app/tests/conftest.py`

Changes:
1. Fix `authed_client` to seed session at `memora:session:{player_id}` (not `{test_prefix}memora:session:{player_id}`)
2. Fix `admin_client` similarly
3. Add explicit session key cleanup in both fixture teardowns
4. Add helper functions: `make_hierarchy_json()`, `seed_hierarchy()`, `seed_game_session()`, `seed_settings()`, `seed_wallet()`, `seed_access_grants()`, `cleanup_player_keys()`
5. Verify existing 131 tests still pass

### Step 1: test_health_endpoints.py (4 tests)

Simplest tests — validates the infrastructure works end-to-end.

| Test | Route | Assert |
|------|-------|--------|
| `test_liveness_ok` | `GET /health/live` | 200, `status=alive` |
| `test_liveness_no_auth` | `GET /health/live` | 200 without Authorization |
| `test_readiness_ok` | `GET /health/ready` | 200, `redis=ok` |
| `test_readiness_redis_down` | `GET /health/ready` | 503, `redis=unreachable` |

### Step 2: test_wallet_endpoints.py (4 tests)

Simple authenticated requests + admin role check.

| Test | Route | Assert |
|------|-------|--------|
| `test_get_own_wallet` | `GET /wallet` | 200, xp + streak from seeded hash |
| `test_empty_wallet_defaults` | `GET /wallet` | 200, xp=0, streak=0 |
| `test_admin_get_player_wallet` | `GET /wallet/{id}` | 200 with admin client |
| `test_non_admin_forbidden` | `GET /wallet/{id}` | 403 with player client |

### Step 3: test_access_endpoints.py (6 tests)

Admin CRUD + idempotency + role enforcement.

| Test | Route | Assert |
|------|-------|--------|
| `test_admin_grant_access` | `POST /access/grants` | 200, granted=1 |
| `test_grant_idempotent` | `POST /access/grants` ×2 | Second returns granted=0 |
| `test_grant_empty_keys` | `POST /access/grants` | 400 EMPTY_KEYS |
| `test_admin_revoke_access` | `DELETE /access/grants` | 200, revoked=1 |
| `test_admin_list_grants` | `GET /access/grants/{id}` | 200 with grant list |
| `test_non_admin_forbidden` | all routes | 403 with player client |

### Step 4: test_auth_endpoints.py (25 tests)

Most complex mock setup — tests each of the 10 auth routes.

**Player login** (7): success, bad creds, missing device ID, rate limited, creates session, kicks old session, registers device
**Admin login** (2): success, invalid credentials
**Refresh** (3): valid, expired, family_id mismatch
**Registration options** (1): returns options
**Register** (2): success, duplicate phone
**Register verify** (2): valid OTP, invalid OTP
**Register resend** (1): success
**Password reset request** (1): anti-enumeration (always 200)
**Password reset verify** (2): valid, invalid OTP
**Password reset confirm** (2): success, reused token

**Key mock patterns**:
- `mock_frappe.call.return_value = {...}` for FrappeClient calls
- `@patch("fastapi_app.api.v1.endpoints.auth.FrappeAuthService")` for admin login
- Pre-seed Redis OTP/pending data for registration and password reset flows
- Real Redis for rate limiting, session, device registration

### Step 5: test_progress_endpoints.py (10 tests)

Access control + hierarchy-dependent responses.

| Test | Route | Assert |
|------|-------|--------|
| `test_progress_summary` | `GET /progress/` | 200, list of subjects |
| `test_unauthenticated` | `GET /progress/` | 401 |
| `test_subject_progress` | `GET /progress/{subj}` | 200, tracks array |
| `test_subject_not_found` | `GET /progress/{subj}` | 404 |
| `test_access_denied` | `GET /progress/{subj}` | 403 for paid subject |
| `test_free_content_bypass` | `GET /progress/{subj}` | 200 for free content |
| `test_track_listing` | `GET /progress/{subj}/tracks` | 200, track summaries |
| `test_track_detail` | `GET /progress/{subj}/tracks/{id}` | 200, unit list |
| `test_unit_detail` | `GET /.../units/{id}` | 200, topic list |
| `test_lesson_completion` | `GET /.../topics/{id}/lessons` | 200, completed flags |

**Key setup**: Each test seeds hierarchy JSON + access grants in Redis. Progress tests also seed bitmap data.

### Step 6: test_session_endpoints.py (15 tests)

Full game loop integration — most Redis state dependencies.

| Test | Route | Assert |
|------|-------|--------|
| `test_get_current_active` | `GET /sessions/current` | 200, session fields |
| `test_get_current_none` | `GET /sessions/current` | 404 |
| `test_start_success` | `POST /sessions/start` | 200, session_id |
| `test_start_nonexistent_subject` | `POST /sessions/start` | 404 |
| `test_start_no_access` | `POST /sessions/start` | 403 |
| `test_start_free_bypass` | `POST /sessions/start` | 200 without grant |
| `test_start_nonexistent_lesson` | `POST /sessions/start` | 404 |
| `test_end_success` | `POST /sessions/end` | 200, xp > 0 |
| `test_end_no_session` | `POST /sessions/end` | 403 |
| `test_end_replay_detection` | `POST /sessions/end` | is_replay=True |
| `test_end_streak_update` | `POST /sessions/end` | streak >= 1 |
| `test_end_xp_awarded` | `POST /sessions/end` | xp_awarded > 0 |
| `test_end_marks_dirty` | `POST /sessions/end` | player in dirty set |
| `test_end_leaderboard_update` | `POST /sessions/end` | ZADD called |
| `test_unauthenticated` | `POST /sessions/start` | 401 |

**Key setup**: Session end tests require: game session hash, hierarchy cache, gamification settings, wallet hash, progress bitmap all pre-seeded in Redis. The Lua scripts (`START_SESSION_SCRIPT`, `SESSION_COMPLETE_SCRIPT`, `STREAK_UPDATE_SCRIPT`) execute on real Redis.

### Step 7: Verification

1. Run full test suite: `python3 -m pytest fastapi_app/tests/ -v --tb=short`
2. Verify ~195 tests pass (131 existing + ~64 new)
3. Verify completion under 30 seconds
4. Verify no `time.sleep`, no excluded imports

## Complexity Tracking

No constitution violations to justify. All tests follow existing patterns from conftest.py infrastructure.
