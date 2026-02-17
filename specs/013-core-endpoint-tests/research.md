# Research: Core Endpoint Tests

**Feature Branch**: `013-core-endpoint-tests`
**Date**: 2026-02-17

## R-001: Session Key Prefix Mismatch in authed_client Fixture

### Problem

The `authed_client` fixture (conftest.py:269) seeds session data at:
```
{test_prefix}memora:session:{player_id}
→ "test:xxxxxxxx:memora:session:PLAYER-TEST-001"
```

But `get_current_user` (deps.py:102) reads from:
```
{settings.redis_key_prefix}session:{token_payload.sub}
→ "memora:session:PLAYER-TEST-001"
```

Since `settings.redis_key_prefix = "memora:"` (config.py:21) and is NOT overridden to include `test_prefix`, these keys **never match**. This means authenticated endpoint tests would always get 401.

### Decision: Seed session at production prefix, clean up explicitly

**Rationale**: The simplest fix that requires no changes to the app code or settings infrastructure. The `get_current_user` dependency reads from `memora:session:{user_id}` — we must seed there.

**Implementation**:
1. Change `authed_client` to seed at `memora:session:{player_id}` (no test_prefix)
2. Change `admin_client` to seed at `memora:session:{email}` (no test_prefix)
3. Add explicit cleanup in fixture teardown: `await redis_client.delete(session_key)`
4. The `cleanup_keys` autouse fixture still cleans test-prefixed service keys

**Alternatives Considered**:
- Override `get_settings()` per-test to include test_prefix → too invasive, breaks services that construct their own key prefixes
- Override `get_current_user` entirely → defeats the purpose of testing real auth flow
- Use a custom settings with dynamic prefix → requires settings to be non-cached (conflicts with `@lru_cache`)

### Impact

This fix is **required** before any endpoint test can work. All 6 test files depend on authenticated requests.

---

## R-002: Auth Endpoint Mocking Strategy

### Problem

The 10 auth routes create service instances **inline** (not via dependency injection):
- `auth.py:123`: `RateLimiter(redis)` — inline construction
- `auth.py:140`: `frappe_client = await get_frappe_client()` — uses global singleton
- `auth.py:162-168`: `SettingsService(redis, frappe_client)` inline
- `auth.py:168`: `DeviceService(redis, key_prefix=...)` inline
- `auth.py:187`: `WalletService(redis, ...)` inline
- `auth.py:195`: `SessionService(redis, ...)` inline
- `auth.py:445`: `OTPService(redis)` inline

These are NOT injectable via `app.dependency_overrides`.

### Decision: Mock at the `FrappeClient.call` boundary + use `unittest.mock.patch`

**Rationale**: The test plan mandates mocking at the `FrappeClient.call()` boundary (test plan §2). For inline services, `mock.patch` targets the specific method/function. Real Redis is used for rate limiter, OTP, device, session, and wallet (prefix-isolated).

**Implementation per auth route**:
| Route | Mock Strategy |
|-------|---------------|
| `POST /auth/player/login` | Mock `get_frappe_client` return → mock `.call()` for `verify_player_password`; real Redis for rate limit, session, device, wallet |
| `POST /auth/admin/login` | Mock `FrappeAuthService.verify_credentials` via `patch` |
| `POST /auth/refresh` | Seed Redis session, create real refresh token → test decode path |
| `GET /auth/registration-options` | Mock `get_frappe_client` → `.call()` return options |
| `POST /auth/player/register` | Mock `get_frappe_client` → `.call()` for `check_phone_exists`, OTPService uses real Redis |
| `POST /auth/player/register/verify` | Pre-seed OTP in Redis, mock `register_player` via `.call()` |
| `POST /auth/player/register/resend` | Pre-seed pending registration in Redis |
| `POST /auth/player/password-reset/request` | Mock `check_phone_exists`, real Redis for OTP storage |
| `POST /auth/player/password-reset/verify` | Pre-seed reset OTP in Redis |
| `POST /auth/player/password-reset/confirm` | Pre-seed reset token, mock `check_phone_exists` + `set_player_password` |

**Alternatives Considered**:
- Refactor auth.py to use dependency injection → out of scope for test feature, modifying production code
- Mock entire auth module → too broad, loses integration value
- Use `respx` for HTTP-level mocking → FrappeClient uses internal HTTP, respx adds unnecessary complexity

---

## R-003: Session Endpoint Service Dependencies

### Problem

The `end_session` endpoint (sessions.py:164) injects 9 dependencies and orchestrates a complex flow:
1. `GameSessionService.get_active_session()` → needs pre-seeded game session hash
2. `HierarchyService.get_hierarchy()` → needs mocked hierarchy data
3. `GameSessionService.complete_session()` → Lua script needs real Redis
4. `WalletService.update_streak()` → Lua script needs real Redis
5. `SettingsService.get_gamification_settings()` → needs mocked settings
6. `ProgressService.get_completed_bits()` → needs real Redis bitmap
7. `StatsService` → needs real Redis hash
8. `LeaderboardService.update_leaderboards()` → needs real Redis sorted sets
9. Direct Redis pipeline for XP/dirty/stats

### Decision: Seed Redis state + mock FrappeClient for hierarchy/settings hydration

**Rationale**: The Lua scripts and Redis pipelines ARE the behavior we're testing. Mock only the external boundary (FrappeClient). Pre-seed Redis with:
- Game session hash (`memora:gamesession:{user_id}`)
- Hierarchy JSON (`memora:hierarchy:{subject_id}`)
- Gamification settings (`memora:settings:gamification`)
- Optionally: wallet hash, progress bitmap, stats hash

**Implementation**:
1. Create a `seed_game_session()` helper that populates the Redis hash with session fields
2. Create a `seed_hierarchy()` helper that stores a minimal hierarchy JSON in Redis
3. Create a `seed_settings()` helper that stores gamification settings JSON in Redis
4. For end_session tests: also pre-seed wallet (for ensure_hydrated) and progress bitmap

**Alternatives Considered**:
- Mock all services → misses the integration value of testing the full orchestration
- Dependency-override each service → 9 overrides per test is fragile and unmaintainable

---

## R-004: Progress Endpoint Access Control Testing

### Problem

Progress endpoints use a three-level access check:
1. `check_access_with_plan()` — checks explicit grants OR plan free subjects
2. `hierarchy.has_any_free_content()` — fallback for subjects with free units/topics
3. Only if both fail → 403 NO_ACCESS

Testing free content bypass requires hierarchy data that signals `has_any_free_content() == True`.

### Decision: Use minimal hierarchy fixtures with controllable free_units/free_topics

**Rationale**: The hierarchy model's `has_any_free_content()` checks if `free_units` or `free_topics` arrays are non-empty. Tests can construct hierarchy JSON with or without these arrays.

**Implementation**:
- `make_hierarchy(has_free_content=True)` → includes `free_units: ["UNIT-001"]`
- `make_hierarchy(has_free_content=False)` → empty `free_units`/`free_topics`
- Seed into Redis at `memora:hierarchy:{subject_id}` with 1h TTL

---

## R-005: Services Constructing Keys Without Test Prefix

### Problem

When endpoint code constructs services inline (e.g., `WalletService(redis, key_prefix=settings.redis_key_prefix)`), the service uses `"memora:"` as prefix. But test cleanup only scans `{test_prefix}*`.

This means service-created Redis keys (wallet hashes, progress bitmaps, leaderboard sorted sets) will use `memora:` prefix and won't be auto-cleaned.

### Decision: Accept production-prefix keys + explicit cleanup in affected tests

**Rationale**: Endpoint tests exercise the real code path including real key construction. Trying to override key_prefix would require modifying production code. Instead:
1. Tests that seed production-prefix keys clean them up explicitly in teardown
2. Use unique player IDs per test (e.g., `PLAYER-TEST-{uuid}`) to avoid collisions
3. The service-level tests (Phases 1-4) already use test_prefix for isolation — endpoint tests operate at a higher integration level where production prefixes are appropriate

**Implementation**:
- `authed_client` uses unique player IDs: `PLAYER-TEST-{uuid4().hex[:8]}`
- Fixture teardown deletes known production-prefix keys created during test
- Helper function `cleanup_player_keys(redis, player_id)` deletes all `memora:*:{player_id}*` keys

**Alternatives Considered**:
- Override settings.redis_key_prefix per test → breaks service key lookups in deps.py
- Use SCAN with broader pattern → risk cleaning production data
- Override every service dependency → defeats integration testing purpose
