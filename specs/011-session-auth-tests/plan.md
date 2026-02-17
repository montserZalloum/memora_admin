# Implementation Plan: Session + Auth Service Tests

**Branch**: `011-session-auth-tests` | **Date**: 2026-02-17 | **Spec**: [spec.md](spec.md)
**Input**: Feature specification from `/specs/011-session-auth-tests/spec.md`

## Summary

Create 5 test files (~39 tests) covering the session and authentication service layer: GameSessionService (Lua script-driven session lifecycle), OTPService (registration/reset flows with rate limiting), SessionService (family-ID based auth sessions), RateLimiter (sliding window Lua script), and DeviceService (multi-device registration with fingerprint matching). All tests use real Redis at `redis://127.0.0.1:13000` with per-test prefix isolation, exercising actual Lua scripts against the production Redis version.

## Technical Context

**Language/Version**: Python 3.11+ (Frappe v15 bench environment)
**Primary Dependencies**: pytest 8.4.2, pytest-asyncio 0.26.0, redis.asyncio, unittest.mock.AsyncMock, user-agents (for DeviceService fingerprinting)
**Storage**: Redis at `redis://127.0.0.1:13000` (real, prefix-isolated), MariaDB via mocked FrappeClient
**Testing**: pytest + pytest-asyncio, async test functions, real Redis with prefix isolation
**Target Platform**: Linux server (Frappe bench)
**Project Type**: Single project (test-only feature, no new source code)
**Performance Goals**: Full Phase 3 suite completes in <10 seconds
**Constraints**: Must not modify existing conftest.py or Phase 1/2 test files; must coexist with production data on shared Redis
**Scale/Scope**: 5 test files, ~39 tests, exercising 5 service classes and 4 Lua scripts

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

### Pre-Research Gate (PASS)

| Principle | Status | Evidence |
|-----------|--------|----------|
| **I. Source-of-Truth Awareness** | PASS | Tests verify Redis state directly after service calls; OTP/session data checked via `redis_client.get()`. Dirty set membership verified for `complete_session`. FrappeClient boundary is mocked (no MariaDB writes in these services). |
| **II. Atomic Operation Integrity** | PASS | All 4 Lua scripts (START_SESSION, SESSION_COMPLETE, RATE_LIMIT, REGISTER_DEVICE) tested through service methods against real Redis. No decomposition into sequential Redis commands. Return values (is_replay, status codes) explicitly asserted. |
| **III. Edge-Case-First Design** | PASS | Edge cases proportional to happy paths: expired OTP, max attempts, cooldown, duplicate phone, no session, replay detection, fingerprint match, device limit exceeded, legacy session format. |
| **IV. Test Isolation** | PASS | Per-test prefix via `test_prefix` fixture. Service `key_prefix` overridden. `cleanup_keys` autouse fixture scans and deletes. Global keys (dirty set, interaction buffer) cleaned by additional autouse fixtures. No shared state between tests. |
| **V. Business Flow Completeness** | N/A | This phase tests individual services. End-to-end flows (registration → OTP → verify → login) are endpoint-level tests for a future phase. Each service's complete lifecycle is covered. |

### Post-Design Gate (PASS)

| Gate | Status | Notes |
|------|--------|-------|
| **Gate 1: Pre-Merge** | PASS | No `time.sleep()` used. No imports from excluded scope (Voucher/Library). |
| **Gate 2: Coverage** | PASS | 100% of public methods covered. All 4 Lua scripts have dedicated tests. |
| **Gate 3: Risk Coverage** | PARTIAL | RISK-02 (SESSION_COMPLETE failure), RISK-05 (family_id mismatch), RISK-10 (OTP rate limit) covered. RISK-01/03/06/07/08 are for WalletService/AccessService/ProgressService (covered in Phase 2). RISK-04/09 are for future phases. |

### Excluded Scope Compliance

No imports from: `services/voucher/`, `api/voucher.py`, `api/allocation.py`. No references to: `Memora Voucher Batch`, `Memora Voucher Card`, `Memora Voucher Allocation`, `Memora Voucher Redemption Log`.

## Project Structure

### Documentation (this feature)

```text
specs/011-session-auth-tests/
├── plan.md              # This file
├── spec.md              # Feature specification
├── research.md          # Phase 0: Service implementation research
├── data-model.md        # Phase 1: Redis structures and service interfaces
├── quickstart.md        # Phase 1: Test execution guide
├── contracts/
│   └── test-contracts.md # Phase 1: Per-test behavioral contracts
└── tasks.md             # Phase 2 output (NOT created by /speckit.plan)
```

### Source Code (repository root)

```text
fastapi_app/
├── services/                    # Services under test (READ ONLY)
│   ├── game_session.py          # GameSessionService + 2 Lua scripts
│   ├── session.py               # SessionService (auth sessions)
│   ├── otp.py                   # OTPService + rate limit Lua script
│   ├── rate_limit.py            # RateLimiter + rate limit Lua script
│   └── device.py                # DeviceService + register device Lua script
├── models/                      # Models under test (READ ONLY)
│   ├── game_session.py          # GameSession, StageResult, etc.
│   └── device.py                # DeviceInfo, DeviceRegistrationResult
├── core/
│   └── constants.py             # GAME_SESSION_TTL, DIRTY_PROGRESS_KEY, etc.
└── tests/                       # Test files (NEW)
    ├── conftest.py              # Existing fixtures (UNCHANGED)
    ├── test_game_session_service.py   # NEW: ~8 tests
    ├── test_otp_service.py            # NEW: ~12 tests
    ├── test_session_service.py        # NEW: ~5 tests
    ├── test_rate_limiter.py           # NEW: ~6 tests
    └── test_device_service.py         # NEW: ~8 tests
```

**Structure Decision**: Tests added to existing `fastapi_app/tests/` directory alongside Phase 1/2 test files. No new directories needed. No source code modifications.

## Implementation Design

### Test File Design

Each test file follows the established Phase 2 pattern:

1. **Module-level constants** for test data (player IDs, subject IDs, etc.)
2. **Service fixture** constructing the service with `test_prefix` for key isolation
3. **Additional autouse fixtures** for global key cleanup where needed
4. **Test classes** grouping related scenarios (e.g., `TestStartSession`, `TestCompleteSession`)
5. **Direct Redis verification** after service calls to assert state

### Global Key Cleanup Strategy

`DIRTY_PROGRESS_KEY` ("memora:dirty:progress") and `INTERACTION_BUFFER_KEY` ("memora:buffer:interactions") are hardcoded constants that don't use the test prefix. Tests that call `complete_session` need additional autouse cleanup:

```python
@pytest.fixture(autouse=True)
async def cleanup_global_keys(redis_client):
    yield
    await redis_client.srem(DIRTY_PROGRESS_KEY, f"{TEST_USER}:{TEST_SUBJECT}:v{TEST_VERSION}")
    # Pop any interactions pushed during test
    while await redis_client.lpop(INTERACTION_BUFFER_KEY):
        pass
```

### Lua Script Testing Strategy

Lua scripts are tested **through service methods** (not raw EVALSHA):

| Lua Script | Service Method | Test File | Key Assertions |
|------------|---------------|-----------|----------------|
| `START_SESSION_SCRIPT` | `GameSessionService.start_session()` | test_game_session_service.py | Hash created, TTL set, force-close old session |
| `SESSION_COMPLETE_SCRIPT` | `GameSessionService.complete_session()` | test_game_session_service.py | Session deleted, bit set, dirty added, interactions buffered, is_replay correct |
| `RATE_LIMIT_SCRIPT` (rate_limit.py) | `RateLimiter.check_rate_limit()` | test_rate_limiter.py | Counter increments, TTL set on first, limits enforced |
| `RATE_LIMIT_SCRIPT` (otp.py) | `OTPService._check_otp_rate_limit()` | test_otp_service.py | Phone/IP limits, cooldown enforcement |
| `REGISTER_DEVICE_SCRIPT` | `DeviceService.register_device()` | test_device_service.py | All 4 paths: existing, fingerprint_match, new, limit_exceeded |

### Test Count Breakdown

| File | Tests | Coverage |
|------|-------|----------|
| `test_game_session_service.py` | 8 | start, force-close, get, get-none, end, complete-first, complete-replay, interactions |
| `test_otp_service.py` | 12 | create-pending, verify-correct, verify-wrong, max-attempts, cooldown, phone-limit, ip-limit, create-reset, anti-enum, verify-reset, token-single-use, expired-pending |
| `test_session_service.py` | 5 | create, validate-match, validate-mismatch, invalidate, overwrite |
| `test_rate_limiter.py` | 6 | first-allowed, ip-exceeded, account-exceeded, get-remaining, lowercase-normalize, no-account |
| `test_device_service.py` | 8 | new-device, existing-device, fingerprint-match, limit-exceeded, get-devices, remove-device, validate-true, validate-false |
| **Total** | **39** | |

## Complexity Tracking

> No constitution violations. No complexity justifications needed.

| Violation | Why Needed | Simpler Alternative Rejected Because |
|-----------|------------|-------------------------------------|
| (none) | — | — |
