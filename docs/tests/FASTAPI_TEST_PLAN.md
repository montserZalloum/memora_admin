# Memora FastAPI Automated Testing Plan

## Context

The Memora Admin project has **zero tests** for its FastAPI sidecar (24 services, 53+ endpoints, 9 Lua scripts) and **zero tests** for its 6 background sync tasks. The Frappe side already has 75+ voucher tests and 27+ DocType tests. This plan closes both gaps.

---

## Key Architectural Decisions

### 1. Two Parallel Test Suites (Not One)
- **FastAPI tests** → `fastapi_app/tests/` → run via `python -m pytest`
- **Frappe sync tests** → `memora_admin/memora_admin/tests/` → run via `bench run-tests`
- These share NO runtime infrastructure. FastAPI services never import `frappe`.

### 2. Real Redis with Prefix Isolation
- All tests use production Redis at `redis://127.0.0.1:13000`
- Each test run uses `key_prefix="test:{uuid}:"` — every service constructor already accepts `key_prefix`
- Cleanup: `SCAN + DEL` of test prefix keys after each test session
- **Never FLUSHDB** — the production Frappe cache shares this Redis instance

### 3. Mock Boundary = `FrappeClient.call()`
- FastAPI services call Frappe via HTTP (`FrappeClient.call("method_name", params)`)
- Tests mock this single method via `AsyncMock(spec=FrappeClient)`
- No need to bootstrap Frappe, no need to mock `frappe.db`

### 4. Settings Override
- `Settings` class uses pydantic-settings (env-based). Tests must override `get_settings()` with hardcoded values to avoid needing a real `.env` file
- Key settings to stub: `redis_url`, `jwt_secret`, `bitmap_json_path`, `frappe_url`, `frappe_site`, `voucher_hmac_secret`

### 5. Characterization Tests for FINDINGs
- FINDINGs 01-03 get tests that PASS asserting **current (buggy) behavior**
- Each is a living ticket: when the bug is fixed, flip the assertion
- FINDING-04 (OTP "1111") skipped — known dev behavior

---

## Test Directory Layout

```
fastapi_app/tests/                          # pytest + pytest-asyncio
  __init__.py
  conftest.py                               # Core fixtures (Redis, mock Frappe, JWT factories, app client)
  # Phase 1: Pure functions
  test_xp_calculation.py                    # calculate_xp_award, calculate_level
  # Phase 2: Core services
  test_access_service.py                    # AccessService
  test_progress_service.py                  # ProgressService
  test_wallet_service.py                    # WalletService (+ Lua streak script)
  # Phase 3: Session + Auth services
  test_session_service.py                   # SessionService
  test_game_session_service.py              # GameSessionService (+ Lua scripts)
  test_otp_service.py                       # OTPService (+ Lua scripts)
  test_rate_limiter.py                      # RateLimiter (+ Lua script)
  test_device_service.py                    # DeviceService (+ Lua script)
  # Phase 4: Remaining services
  test_voucher_service.py                   # VoucherService (HMAC + Lua rate limit scripts)
  test_leaderboard_service.py              # LeaderboardService
  test_stats_service.py                     # StatsService
  test_hierarchy_service.py                 # HierarchyService
  test_catalog_service.py                   # CatalogService
  test_profile_service.py                   # ProfileService + ProfilePageService
  test_plan_service.py                      # PlanService
  test_settings_service.py                  # SettingsService
  test_purchase_service.py                  # PurchaseService
  test_review_service.py                    # ReviewService
  # Phase 5: Endpoint tests - Core
  test_health_endpoints.py                  # GET /health/live, /health/ready
  test_auth_endpoints.py                    # 10 auth routes
  test_session_endpoints.py                 # 3 session routes
  test_progress_endpoints.py                # 6 progress routes
  test_wallet_endpoints.py                  # 2 wallet routes
  test_access_endpoints.py                  # Access admin routes
  # Phase 6: Endpoint tests - Remaining
  test_catalog_endpoints.py                 # 2 routes (products, plans)
  test_purchase_endpoints.py                # 1 route
  test_plans_endpoints.py                   # 1 route
  test_profile_endpoints.py                 # 3 routes (me, {player_id}, PATCH me)
  test_leaderboard_endpoints.py             # 3 routes (global, friends, seasonal)
  test_review_endpoints.py                  # Review routes
  test_settings_endpoints.py                # 1 route
  test_subscription_endpoints.py            # 1 route
  test_voucher_endpoints.py                 # 2 routes (preview, redeem)
  test_webhook_endpoints.py                 # Webhook routes
  test_notification_endpoints.py            # 1 WebSocket route
  # Phase 7: Characterization tests
  test_findings.py                          # FINDINGs 01-03

memora_admin/memora_admin/tests/            # FrappeTestCase (existing dir)
  # Phase 8: Sync task tests (NEW)
  test_sync_wallets.py                      # sync_dirty_wallets
  test_sync_progress.py                     # sync_dirty_progress
  test_flush_interactions.py                # flush_interaction_buffer
```

---

## Infrastructure: `conftest.py`

### Dependencies to Install

```bash
pip install pytest "pytest-asyncio>=0.23,<1.0" httpx
# redis.asyncio already installed as FastAPI dependency
```

### pyproject.toml Addition

```toml
[tool.pytest.ini_options]
testpaths = ["fastapi_app/tests"]
asyncio_mode = "auto"
markers = [
    "slow: marks tests as slow",
]
```

### Key Fixtures

```python
# --- Settings override ---
_test_settings = Settings(
    redis_url="redis://127.0.0.1:13000",
    jwt_secret="test-secret-key-for-unit-tests",
    jwt_algorithm="HS256",
    bitmap_json_path="/tmp/test-bitmaps",
    frappe_url="http://localhost:8000",
    frappe_site="test.local",
    frappe_api_key="test-key",
    frappe_api_secret="test-secret",
    voucher_hmac_secret="test-hmac-secret",
    jwt_access_token_expire_minutes=60,
    jwt_refresh_token_expire_days=30,
)
# Patch get_settings lru_cache before any module uses it

# --- Redis ---
@pytest.fixture
def test_prefix():
    """Unique key prefix for test isolation."""
    return f"test:{uuid4().hex[:8]}:"

@pytest.fixture
async def redis_client():
    """Real Redis client — tests use prefix isolation, never FLUSHDB."""
    client = redis.from_url("redis://127.0.0.1:13000", decode_responses=True)
    yield client
    await client.aclose()

@pytest.fixture(autouse=True)
async def cleanup_keys(redis_client, test_prefix):
    """Auto-cleanup test keys after each test via SCAN+DEL."""
    yield
    cursor = 0
    while True:
        cursor, keys = await redis_client.scan(cursor, match=f"{test_prefix}*", count=1000)
        if keys:
            await redis_client.delete(*keys)
        if cursor == 0:
            break

# --- Mock Frappe ---
@pytest.fixture
def mock_frappe():
    """Mock FrappeClient — set .call.return_value per test."""
    client = AsyncMock()
    client.call = AsyncMock(return_value=None)
    client.get_grant_keys = AsyncMock(return_value=[])
    client.create_subscription = AsyncMock(return_value={})
    client.close = AsyncMock()
    return client

# --- JWT factories ---
@pytest.fixture
def make_player_token():
    """Factory for valid player JWT access tokens.
    Returns (token_str, family_id) tuple.
    Uses create_access_token from fastapi_app.core.security.
    Claims: sub=player_id, plan=plan_id, name=display_name, fid=family_id, type=access
    """

@pytest.fixture
def make_admin_token():
    """Factory for valid admin JWT access tokens.
    Returns (token_str, family_id) tuple.
    Claims: sub=email, role="System Manager", fid=family_id, type=access
    """

# --- FastAPI test client ---
@pytest.fixture
async def app_client(redis_client, mock_frappe):
    """FastAPI AsyncClient with dependency overrides for Redis and Frappe.
    Overrides: get_redis -> redis_client, get_frappe_client -> mock_frappe
    Uses httpx.AsyncClient + ASGITransport(app=app)
    """

@pytest.fixture
async def authed_client(app_client, redis_client, make_player_token):
    """App client with pre-seeded player session in Redis.
    Seeds memora:session:{player_id} with JSON {fid: family_id}.
    Returns (client, token, player_id, family_id) tuple.
    """

@pytest.fixture
async def admin_client(app_client, redis_client, make_admin_token):
    """App client with pre-seeded admin session in Redis.
    Returns (client, token, email, family_id) tuple.
    """
```

---

## Phase 1: Foundation + Pure Functions (~15 tests, 3 files)

**Create:**
- `fastapi_app/tests/__init__.py`
- `fastapi_app/tests/conftest.py` — all fixtures above
- `fastapi_app/tests/test_xp_calculation.py`

**Modify:**
- `pyproject.toml` — add `[tool.pytest.ini_options]`

**Tests in `test_xp_calculation.py`:**

| # | Test | Target | Key Assertion |
|---|------|--------|---------------|
| 1 | `test_fresh_base_xp` | `calculate_xp_award` | `lesson_xp=0` uses `base_xp` |
| 2 | `test_fresh_lesson_xp_overrides` | `calculate_xp_award` | `lesson_xp > 0` overrides `base_xp` |
| 3 | `test_replay_fixed_amount` | `calculate_xp_award` | `is_replay=True` returns `replay_xp` |
| 4 | `test_replay_no_hearts_bonus` | `calculate_xp_award` | replay ignores `hearts_remaining` |
| 5 | `test_hearts_bonus_added` | `calculate_xp_award` | `hearts_remaining * xp_per_heart` added |
| 6 | `test_streak_multiplier_linear` | `calculate_xp_award` | `streak=10, cap=50` → 1.10x |
| 7 | `test_streak_multiplier_capped` | `calculate_xp_award` | `streak=100, cap=50` → 1.50x |
| 8 | `test_streak_zero_no_multiplier` | `calculate_xp_award` | `streak=0` → 1.0x |
| 9 | `test_result_floored` | `calculate_xp_award` | `int()` truncation, not rounding |
| 10 | `test_zero_base_zero_lesson` | `calculate_xp_award` | `base_xp=0, lesson_xp=0` → 0 |
| 11 | `test_replay_streak_multiplier_applied` | `calculate_xp_award` | streak multiplier applies to replay too |
| 12 | `test_level_zero_xp` | `calculate_level` | 0 XP → Level 1 "Beginner" |
| 13 | `test_level_boundary` | `calculate_level` | 100 XP → Level 2 "Learner" |
| 14 | `test_level_max` | `calculate_level` | 11000+ XP → Level 15 "Transcendent", xp_to_next=0 |
| 15 | `test_level_mid` | `calculate_level` | 500 XP → Level 3 "Explorer", xp_in_level=200, xp_to_next=100 |

**Source files:**
- `calculate_xp_award` → `fastapi_app/services/wallet.py:35-67`
- `calculate_level` → `fastapi_app/core/constants.py:61-81`

**Verify:** `python3 -m pytest fastapi_app/tests/ -v`

---

## Phase 2: Core Service Tests (~30 tests, 3 files)

### `test_access_service.py` (~11 tests)

**Service:** `fastapi_app/services/access.py` — `AccessService`
**Constructor:** `AccessService(redis_client, key_prefix="memora:", frappe_client=None)`
**Redis keys:** `{prefix}access:{player_id}` (set), `{prefix}plan:{plan_id}:free_subjects` (set)

| # | Test | Method | Key Assertion |
|---|------|--------|---------------|
| 1 | `test_grant_access_sadd` | `grant_access` | Keys added to Redis set, returns count of NEW keys |
| 2 | `test_revoke_access_srem` | `revoke_access` | Keys removed from Redis set |
| 3 | `test_check_access_granted_true` | `check_access` | SISMEMBER returns True after grant |
| 4 | `test_check_access_ungranted_false` | `check_access` | Returns False for missing key |
| 5 | `test_grant_idempotent` | `grant_access` | Re-granting same key returns 0 new |
| 6 | `test_check_with_plan_explicit_first` | `check_access_with_plan` | Explicit grant short-circuits (no plan check) |
| 7 | `test_check_with_plan_fallback` | `check_access_with_plan` | Falls back to plan free subjects set |
| 8 | `test_check_with_plan_track_key_no_plan` | `check_access_with_plan` | `TRK-` keys skip plan check (line 241: only `SUB-` triggers plan fallback) |
| 9 | `test_hydration_skips_when_exists` | `ensure_hydrated` | No Frappe call if Redis set already exists |
| 10 | `test_hydration_calls_frappe` | `ensure_hydrated` | Calls `memora_admin.api.subscriptions.get_player_access_keys` when set missing, SADDs result |
| 11 | `test_hydration_no_client_logs_warning` | `ensure_hydrated` | `frappe_client=None` → logs `access_hydration_skipped`, no crash |

### `test_progress_service.py` (~8 tests)

**Service:** `fastapi_app/services/progress.py` — `ProgressService`
**Constructor:** `ProgressService(redis_client, key_prefix="memora:", frappe_client=None)`
**Redis keys:** `{prefix}progress:{user}:{subject}:v{ver}` (bitmap), `memora:dirty:progress` (set)

| # | Test | Method | Key Assertion |
|---|------|--------|---------------|
| 1 | `test_complete_first_time` | `complete_lesson` | SETBIT returns False (previous=0, not replay) |
| 2 | `test_complete_replay` | `complete_lesson` | SETBIT returns True (previous=1, is replay) |
| 3 | `test_complete_marks_dirty` | `complete_lesson` | `DIRTY_PROGRESS_KEY` set contains `"user:subject:v1"` |
| 4 | `test_is_complete_true` | `is_complete` | GETBIT returns True after SETBIT |
| 5 | `test_is_complete_false` | `is_complete` | GETBIT returns False for unset bit |
| 6 | `test_get_completed_count` | `get_completed_count` | BITCOUNT accuracy (set 3 bits → count=3) |
| 7 | `test_hydration_from_hex` | `ensure_hydrated` | Mock Frappe returns `{passed_lessons_bitset: hex}`, SETRANGE restores bitmap |
| 8 | `test_hydration_no_client_skips` | `ensure_hydrated` | `frappe_client=None` → logs warning, skips |

### `test_wallet_service.py` (~12 tests)

**Service:** `fastapi_app/services/wallet.py` — `WalletService`
**Constructor:** `WalletService(redis_client, key_prefix="memora:", frappe_client=None)`
**Redis keys:** `{prefix}wallet:{player_id}` (hash: xp, streak, streak_date), `memora:dirty:wallets` (set)
**Lua script:** `STREAK_UPDATE_SCRIPT` — atomic streak update with date comparison

| # | Test | Method | Key Assertion |
|---|------|--------|---------------|
| 1 | `test_award_xp_increment` | `award_xp` | HINCRBY returns new total |
| 2 | `test_award_xp_marks_dirty` | `award_xp` | Player added to `DIRTY_WALLETS_KEY` set |
| 3 | `test_get_wallet_defaults` | `get_wallet` | Empty wallet → `{xp: 0, streak: 0}` |
| 4 | `test_get_wallet_hydrates` | `get_wallet` | Calls Frappe `memora_admin.api.wallet.get_player_wallet` when Redis empty, seeds hash |
| 5 | `test_streak_first_completion` | `update_streak` | Lua sets streak=1, was_updated=True |
| 6 | `test_streak_consecutive` | `update_streak` | Pre-seed streak_date=yesterday → streak increments |
| 7 | `test_streak_missed_day` | `update_streak` | Pre-seed streak_date=2 days ago → resets to 1 |
| 8 | `test_streak_same_day` | `update_streak` | Pre-seed streak_date=today → no change, was_updated=False |
| 9 | `test_streak_replay_no_change` | `update_streak` | `is_replay=True` → Lua returns current streak, was_updated=False |
| 10 | `test_streak_marks_dirty` | `update_streak` | Dirty set updated only when was_updated=True |
| 11 | `test_hydration_seeds_redis` | `ensure_hydrated` | MariaDB values `{total_xp, current_streak}` written to hash |
| 12 | `test_hydration_skips_existing` | `ensure_hydrated` | No Frappe call if hash already exists |

**Note on streak tests:** The Lua script reads `ARGV[1]=today`, `ARGV[2]=yesterday`, `ARGV[3]=is_replay`. Tests must pre-seed the wallet hash with specific `streak_date` values to control the Lua script's branching. Use `wallet.py:get_amman_today()` and `get_amman_yesterday()` for date values.

---

## Phase 3: Session + Auth Services (~40 tests, 5 files)

### `test_session_service.py` (~5 tests)

**Service:** `fastapi_app/services/session.py` — `SessionService`
**Redis keys:** `{prefix}session:{user_id}` (string: JSON `{fid, plan_id}`)

| # | Test | Method | Key Assertion |
|---|------|--------|---------------|
| 1 | `test_create_session` | `create_session` | Returns family_id, stores JSON in Redis with TTL |
| 2 | `test_validate_session_matching` | `validate_session` | Returns `(True, None)` when fid matches |
| 3 | `test_validate_session_mismatched` | `validate_session` | Returns `(False, current_fid)` when fid differs |
| 4 | `test_invalidate_session` | `invalidate_session` | DELetes key, returns True |
| 5 | `test_overwrite_replaces_fid` | `create_session` | Second create replaces first family_id |

### `test_game_session_service.py` (~8 tests)

**Service:** `fastapi_app/services/game_session.py` — `GameSessionService`
**Redis keys:** `{prefix}gamesession:{user_id}` (hash), TTL=3600s
**Lua scripts:** `START_SESSION_SCRIPT`, `SESSION_COMPLETE_SCRIPT`

| # | Test | Method | Key Assertion |
|---|------|--------|---------------|
| 1 | `test_start_creates_hash` | `start_session` | Redis hash created with lesson_id, subject_id, started_at |
| 2 | `test_start_force_closes_existing` | `start_session` | Previous session DELeted, new one created (Lua atomic) |
| 3 | `test_get_active_session` | `get_active_session` | Returns GameSession model with correct fields |
| 4 | `test_get_no_active_session` | `get_active_session` | Returns None when no hash |
| 5 | `test_end_session_sets_bit` | `end_session` | SETBIT on progress bitmap, marks dirty |
| 6 | `test_end_session_replay_detection` | `end_session` | Returns is_replay=True when bit was already set |
| 7 | `test_end_session_pushes_interactions` | `end_session` | RPUSH to interaction buffer key |
| 8 | `test_session_ttl_expiry` | `start_session` | Hash has GAME_SESSION_TTL (3600s) TTL set |

### `test_otp_service.py` (~12 tests)

**Service:** `fastapi_app/services/otp.py` — `OTPService`
**Lua scripts:** OTP creation and verification scripts

| # | Test | Method | Key Assertion |
|---|------|--------|---------------|
| 1 | `test_create_pending_registration` | `create_pending` | Returns pending_id, stores phone+data in Redis |
| 2 | `test_verify_correct_otp` | `verify_otp` | Returns True, cleans up pending data |
| 3 | `test_verify_wrong_otp` | `verify_otp` | Returns False, increments attempt counter |
| 4 | `test_verify_max_attempts` | `verify_otp` | After N failures, pending data deleted |
| 5 | `test_verify_expired` | `verify_otp` | Returns False after TTL expiry |
| 6 | `test_resend_otp` | `resend_otp` | Generates new OTP, resets attempt counter |
| 7 | `test_resend_cooldown` | `resend_otp` | Returns error if within cooldown period |
| 8 | `test_create_reset_request` | `create_reset` | Stores reset token for phone |
| 9 | `test_verify_reset_otp` | `verify_reset` | Returns single-use reset token |
| 10 | `test_validate_reset_token_single_use` | `validate_reset_token` | Token consumed on first use, invalid on second |
| 11 | `test_phone_rate_limit` | `create_pending` | Rate-limited per phone number |
| 12 | `test_ip_rate_limit` | `create_pending` | Rate-limited per IP address |

### `test_rate_limiter.py` (~6 tests)

**Service:** `fastapi_app/services/rate_limit.py` — `RateLimiter`
**Lua script:** `RATE_LIMIT_SCRIPT` — atomic INCR with conditional EXPIRE
**Redis keys:** `{prefix}ratelimit:ip:{ip}`, `{prefix}ratelimit:account:{account}`
**Limits:** IP: 10/min, Account: 5/min

| # | Test | Method | Key Assertion |
|---|------|--------|---------------|
| 1 | `test_first_request_allowed` | `check_rate_limit` | Returns `(True, 0, "")` |
| 2 | `test_ip_limit_exceeded` | `check_rate_limit` | After 10 calls, returns `(False, retry_after, "ip")` |
| 3 | `test_account_limit_exceeded` | `check_rate_limit` | After 5 calls, returns `(False, retry_after, "account")` |
| 4 | `test_retry_after_ttl` | `check_rate_limit` | retry_after matches key TTL |
| 5 | `test_remaining_counts` | `get_remaining` | Returns correct remaining for IP and account |
| 6 | `test_window_expiry` | `check_rate_limit` | After TTL expires, counter resets (use short TTL override or wait) |

### `test_device_service.py` (~8 tests)

**Service:** `fastapi_app/services/device.py` — `DeviceService`
**Lua script:** `REGISTER_DEVICE_SCRIPT` — atomic device registration with fingerprint matching and limit check
**Redis keys:** `{prefix}devices:{user_id}` (hash)

| # | Test | Method | Key Assertion |
|---|------|--------|---------------|
| 1 | `test_register_new_device` | `register_device` | Returns success, device stored in hash |
| 2 | `test_register_fingerprint_match` | `register_device` | Same user_agent → reuses existing device UUID |
| 3 | `test_register_limit_exceeded` | `register_device` | After max_devices, returns limit_exceeded |
| 4 | `test_register_replaces_oldest` | `register_device` | When limit hit but new fingerprint, oldest UUID replaced |
| 5 | `test_get_devices` | `get_devices` | Returns list of device info dicts |
| 6 | `test_remove_device` | `remove_device` | Device UUID removed from hash |
| 7 | `test_validate_device_valid` | `validate_device` | Known device_id returns True |
| 8 | `test_validate_device_invalid` | `validate_device` | Unknown device_id returns False |

---

## Phase 4: Remaining Service Tests (~30 tests, 10 files)

### `test_voucher_service.py` (~6 tests)

**Service:** `fastapi_app/services/voucher.py` — `VoucherService`
**Constructor:** `VoucherService(redis_client, frappe_client, hmac_secret)`
**Lua scripts:** `CHECK_LIMIT_SCRIPT`, `INCREMENT_SCRIPT`
**Redis keys:** `{prefix}voucher_fail:player:{id}`, `{prefix}voucher_fail:ip:{ip}` (1h TTL)

| # | Test | Method | Key Assertion |
|---|------|--------|---------------|
| 1 | `test_hmac_computation` | `_compute_hmac` | Deterministic HMAC-SHA256 output |
| 2 | `test_rate_limit_under` | `check_rate_limit` | Returns None when under limit |
| 3 | `test_rate_limit_exceeded_player` | `check_rate_limit` | Returns retry_after after 5 player failures |
| 4 | `test_rate_limit_exceeded_ip` | `check_rate_limit` | Returns retry_after after 20 IP failures |
| 5 | `test_preview_delegates_to_frappe` | `preview` | Calls `frappe.call("memora_admin.memora_admin.api.voucher.preview_voucher", ...)` |
| 6 | `test_redeem_delegates_to_frappe` | `redeem` | Calls Frappe redeem method, records failure on error |

### `test_leaderboard_service.py` (~4 tests)

**Service:** `fastapi_app/services/leaderboard.py` — `LeaderboardService`
**Redis keys:** `{prefix}lb:alltime`, `{prefix}lb:daily:{date}`, `{prefix}lb:weekly:{week}`

| # | Test | Method | Key Assertion |
|---|------|--------|---------------|
| 1 | `test_update_leaderboards` | `update_leaderboards` | ZADD to daily/weekly/alltime sorted sets |
| 2 | `test_get_leaderboard` | `get_leaderboard` | ZREVRANGE returns ranked list |
| 3 | `test_get_rank` | `get_rank` | ZREVRANK returns 0-indexed rank |
| 4 | `test_empty_leaderboard` | `get_leaderboard` | Returns empty list when no entries |

### `test_stats_service.py` (~4 tests)

**Service:** `fastapi_app/services/stats.py` — `StatsService`
**Redis keys:** `{prefix}stats:{user}:{subject}:v{ver}` (hash, 1h TTL)
**Pure function:** `compute_stats_from_hierarchy(hierarchy, completed_bits)` → dict

| # | Test | Method | Key Assertion |
|---|------|--------|---------------|
| 1 | `test_get_stats_cache_hit` | `get_stats` | Returns cached hash when exists |
| 2 | `test_get_stats_cache_miss` | `get_stats` | Returns None when key missing |
| 3 | `test_set_stats` | `set_stats` | HSET with mapping + EXPIRE 3600s |
| 4 | `test_compute_stats_from_hierarchy` | `compute_stats_from_hierarchy` | Pure function: builds correct counts dict |

### `test_hierarchy_service.py` (~3 tests)

**Service:** `fastapi_app/services/hierarchy.py` — `HierarchyService`
**Redis keys:** `{prefix}hierarchy:{subject}` (string: JSON, 1h TTL), `{prefix}subjects_with_free_content` (set)

| # | Test | Method | Key Assertion |
|---|------|--------|---------------|
| 1 | `test_get_hierarchy_cache_hit` | `get_hierarchy` | Returns cached JSON, no Frappe call |
| 2 | `test_get_hierarchy_cache_miss` | `get_hierarchy` | Calls Frappe, caches result with TTL |
| 3 | `test_invalidate` | `invalidate` | DELetes cache key for subject |

### `test_catalog_service.py` (~2 tests)

**Service:** `fastapi_app/services/catalog.py` — `CatalogService`

| # | Test | Method | Key Assertion |
|---|------|--------|---------------|
| 1 | `test_get_catalog_cache_hit` | `get_catalog` | Returns cached data, no Frappe call |
| 2 | `test_get_catalog_cache_miss` | `get_catalog` | Calls Frappe, caches result |

### `test_profile_service.py` (~3 tests)

**Service:** `fastapi_app/services/profile.py` — `ProfileService`

| # | Test | Method | Key Assertion |
|---|------|--------|---------------|
| 1 | `test_get_profile_cache_hit` | `get_profile` | Returns cached profile |
| 2 | `test_get_profile_cache_miss` | `get_profile` | Fetches from Frappe, caches |
| 3 | `test_invalidate` | `invalidate` | DELetes profile cache key |

### `test_plan_service.py` (~2 tests)

**Service:** `fastapi_app/services/plan.py` — `PlanService`
**Redis keys:** `{prefix}plan:{plan_id}:manifest` (string: JSON, 1h TTL)

| # | Test | Method | Key Assertion |
|---|------|--------|---------------|
| 1 | `test_get_manifest_cache_hit` | `get_manifest` | Returns cached manifest |
| 2 | `test_get_manifest_cache_miss` | `get_manifest` | Calls Frappe, caches with TTL |

### `test_settings_service.py` (~2 tests)

**Service:** `fastapi_app/services/settings.py` — `SettingsService`

| # | Test | Method | Key Assertion |
|---|------|--------|---------------|
| 1 | `test_get_settings_cache_hit` | `get_gamification_settings` | Returns cached settings |
| 2 | `test_get_settings_cache_miss` | `get_gamification_settings` | Fetches from Frappe, caches |

### `test_purchase_service.py` (~2 tests)

**Service:** `fastapi_app/services/purchase.py` — `PurchaseService`

| # | Test | Method | Key Assertion |
|---|------|--------|---------------|
| 1 | `test_submit_purchase` | `submit_purchase` | Delegates to Frappe |
| 2 | `test_submit_duplicate` | `submit_purchase` | Duplicate check prevents re-submission |

### `test_review_service.py` (~2 tests)

**Service:** `fastapi_app/services/review.py` — `ReviewService`

| # | Test | Method | Key Assertion |
|---|------|--------|---------------|
| 1 | `test_get_due_items` | `get_due_items` | Returns items from Frappe |
| 2 | `test_submit_review` | `submit_review` | Delegates to Frappe |

---

## Phase 5: Core Endpoint Tests (~60 tests, 6 files)

All endpoint tests use `httpx.AsyncClient` with `ASGITransport` and the `app_client`/`authed_client`/`admin_client` fixtures from conftest.

### `test_health_endpoints.py` (~4 tests)

**Routes:** `fastapi_app/api/v1/endpoints/health.py`

| # | Test | Route | Key Assertion |
|---|------|-------|---------------|
| 1 | `test_liveness_ok` | `GET /api/v1/health/live` | 200 `{"status": "alive", "api_version": "v1"}` |
| 2 | `test_readiness_ok` | `GET /api/v1/health/ready` | 200 when Redis connected |
| 3 | `test_readiness_redis_down` | `GET /api/v1/health/ready` | 503 when Redis unreachable (mock Redis to raise) |
| 4 | `test_liveness_no_auth` | `GET /api/v1/health/live` | No auth header required |

### `test_auth_endpoints.py` (~25 tests)

**Routes:** `fastapi_app/api/v1/endpoints/auth.py` (731 lines, 10 routes)

| # | Test | Route | Key Assertion |
|---|------|-------|---------------|
| 1 | `test_player_login_success` | `POST /auth/player/login` | 200 with tokens + profile |
| 2 | `test_player_login_bad_creds` | `POST /auth/player/login` | 401 |
| 3 | `test_player_login_missing_device_id` | `POST /auth/player/login` | 400 or 422 (X-Device-ID header required) |
| 4 | `test_player_login_rate_limited` | `POST /auth/player/login` | 429 after rate limit |
| 5 | `test_admin_login_success` | `POST /auth/admin/login` | 200 with tokens |
| 6 | `test_admin_login_invalid_creds` | `POST /auth/admin/login` | 401 |
| 7 | `test_refresh_valid_token` | `POST /auth/refresh` | 200 new access+refresh tokens |
| 8 | `test_refresh_expired_token` | `POST /auth/refresh` | 401 |
| 9 | `test_refresh_family_id_mismatch` | `POST /auth/refresh` | 401 SESSION_SUPERSEDED |
| 10 | `test_registration_options` | `GET /auth/registration-options` | 200 returns seasons/plans/grades |
| 11 | `test_register_success` | `POST /auth/player/register` | 200 returns pending_id |
| 12 | `test_register_duplicate_phone` | `POST /auth/player/register` | 409 |
| 13 | `test_register_verify_valid` | `POST /auth/player/register/verify` | 200 with tokens + profile |
| 14 | `test_register_verify_invalid_otp` | `POST /auth/player/register/verify` | 401 |
| 15 | `test_register_verify_expired` | `POST /auth/player/register/verify` | 401 or 404 |
| 16 | `test_register_verify_max_attempts` | `POST /auth/player/register/verify` | Locked after N failures |
| 17 | `test_register_resend` | `POST /auth/player/register/resend` | 200 |
| 18 | `test_register_resend_expired` | `POST /auth/player/register/resend` | 404 or 400 |
| 19 | `test_password_reset_request` | `POST /auth/player/password-reset/request` | Always 200 (anti-enumeration) |
| 20 | `test_password_reset_verify_valid` | `POST /auth/player/password-reset/verify` | 200 returns reset_token |
| 21 | `test_password_reset_verify_invalid` | `POST /auth/player/password-reset/verify` | 401 |
| 22 | `test_password_reset_confirm_success` | `POST /auth/player/password-reset/confirm` | 200 |
| 23 | `test_password_reset_confirm_reused_token` | `POST /auth/player/password-reset/confirm` | 401 (single-use) |
| 24 | `test_login_creates_device` | `POST /auth/player/login` | Device registered via DeviceService |
| 25 | `test_login_kicks_old_session` | `POST /auth/player/login` | Old family_id replaced in Redis |

### `test_session_endpoints.py` (~15 tests)

**Routes:** `fastapi_app/api/v1/endpoints/sessions.py` (3 routes)

| # | Test | Route | Key Assertion |
|---|------|-------|---------------|
| 1 | `test_get_current_active` | `GET /sessions/current` | 200 with session data |
| 2 | `test_get_current_none` | `GET /sessions/current` | 404 no active session |
| 3 | `test_start_success` | `POST /sessions/start` | 200, session hash created |
| 4 | `test_start_nonexistent_subject` | `POST /sessions/start` | 404 |
| 5 | `test_start_no_access` | `POST /sessions/start` | 403 NO_ACCESS |
| 6 | `test_start_free_bypass` | `POST /sessions/start` | 200 for free content without grant |
| 7 | `test_end_success` | `POST /sessions/end` | 200 with XP award + streak |
| 8 | `test_end_no_session` | `POST /sessions/end` | 403 or 404 |
| 9 | `test_end_replay_detection` | `POST /sessions/end` | Response includes is_replay=True |
| 10 | `test_end_hearts_bonus` | `POST /sessions/end` | XP includes hearts_remaining bonus |
| 11 | `test_end_streak_update` | `POST /sessions/end` | Streak incremented/maintained |
| 12 | `test_end_leaderboard_update` | `POST /sessions/end` | Leaderboard ZADD called |
| 13 | `test_end_stats_cold_start` | `POST /sessions/end` | Stats computed from hierarchy when cache empty |
| 14 | `test_end_stats_existing` | `POST /sessions/end` | Stats HINCRBY when cache exists |
| 15 | `test_unauthenticated` | `POST /sessions/start` | 401 without bearer token |

### `test_progress_endpoints.py` (~10 tests)

**Routes:** `fastapi_app/api/v1/endpoints/progress.py` (6 routes)

| # | Test | Route | Key Assertion |
|---|------|-------|---------------|
| 1 | `test_progress_summary` | `GET /progress/` | 200, summary for accessible subjects |
| 2 | `test_subject_progress` | `GET /progress/{subject}` | 200, detailed breakdown |
| 3 | `test_subject_not_found` | `GET /progress/{subject}` | 404 |
| 4 | `test_track_listing` | `GET /progress/{subject}/tracks` | 200, track list |
| 5 | `test_track_detail` | `GET /progress/{subject}/tracks/{track_id}` | 200, track with units |
| 6 | `test_unit_detail` | `GET /progress/{subject}/tracks/{tid}/units/{uid}` | 200, unit with topics |
| 7 | `test_lesson_completion` | `GET /progress/{subject}/topics/{tid}/lessons` | 200, lesson statuses |
| 8 | `test_access_denied` | `GET /progress/{subject}` | 403 for paid subject without grant |
| 9 | `test_free_content_bypass` | `GET /progress/{subject}` | 200 for free content without grant |
| 10 | `test_unauthenticated` | `GET /progress/` | 401 |

### `test_wallet_endpoints.py` (~4 tests)

**Routes:** `fastapi_app/api/v1/endpoints/wallet.py` (2 routes)

| # | Test | Route | Key Assertion |
|---|------|-------|---------------|
| 1 | `test_get_own_wallet` | `GET /wallet` | 200, player's wallet data |
| 2 | `test_empty_wallet_defaults` | `GET /wallet` | 200, `{xp: 0, streak: 0}` for new player |
| 3 | `test_admin_get_player_wallet` | `GET /wallet/{player_id}` | 200, admin-only access |
| 4 | `test_non_admin_forbidden` | `GET /wallet/{player_id}` | 403 for non-admin |

### `test_access_endpoints.py` (~6 tests)

**Routes:** `fastapi_app/api/v1/endpoints/access.py` (admin-only routes)

| # | Test | Route | Key Assertion |
|---|------|-------|---------------|
| 1 | `test_admin_grant_access` | `POST /access/grants` | 200, keys added to set |
| 2 | `test_admin_revoke_access` | `DELETE /access/grants` | 200, keys removed |
| 3 | `test_admin_list_grants` | `GET /access/grants/{player_id}` | 200, returns set members |
| 4 | `test_non_admin_grant_forbidden` | `POST /access/grants` | 403 |
| 5 | `test_non_admin_revoke_forbidden` | `DELETE /access/grants` | 403 |
| 6 | `test_non_admin_list_forbidden` | `GET /access/grants/{player_id}` | 403 |

---

## Phase 6: Remaining Endpoint Tests (~45 tests, 11 files)

### `test_catalog_endpoints.py` (~3 tests)

| # | Test | Route | Key Assertion |
|---|------|-------|---------------|
| 1 | `test_get_products` | `GET /catalog/products` | 200, product list |
| 2 | `test_get_plans` | `GET /catalog/plans` | 200, plan options |
| 3 | `test_unauthenticated` | `GET /catalog/products` | 401 |

### `test_purchase_endpoints.py` (~4 tests)

| # | Test | Route | Key Assertion |
|---|------|-------|---------------|
| 1 | `test_submit_success` | `POST /purchase/` | 200 |
| 2 | `test_submit_duplicate` | `POST /purchase/` | 409 |
| 3 | `test_unauthenticated` | `POST /purchase/` | 401 |
| 4 | `test_invalid_payload` | `POST /purchase/` | 422 |

### `test_plans_endpoints.py` (~3 tests)

| # | Test | Route | Key Assertion |
|---|------|-------|---------------|
| 1 | `test_get_plan` | `GET /plans/{plan_id}` | 200, plan details |
| 2 | `test_nonexistent_plan` | `GET /plans/{plan_id}` | 404 |
| 3 | `test_unauthenticated` | `GET /plans/{plan_id}` | 401 |

### `test_profile_endpoints.py` (~6 tests)

| # | Test | Route | Key Assertion |
|---|------|-------|---------------|
| 1 | `test_get_my_profile` | `GET /profile/me` | 200, player's own profile |
| 2 | `test_get_player_profile` | `GET /profile/{player_id}` | 200, public profile |
| 3 | `test_update_profile` | `PATCH /profile/me` | 200, profile updated |
| 4 | `test_unauthenticated` | `GET /profile/me` | 401 |
| 5 | `test_nonexistent_player` | `GET /profile/{player_id}` | 404 |
| 6 | `test_update_invalid_data` | `PATCH /profile/me` | 422 |

### `test_leaderboard_endpoints.py` (~5 tests)

| # | Test | Route | Key Assertion |
|---|------|-------|---------------|
| 1 | `test_global_leaderboard` | `GET /leaderboard/global` | 200, ranked list |
| 2 | `test_friends_leaderboard` | `GET /leaderboard/friends` | 200 |
| 3 | `test_seasonal_leaderboard` | `GET /leaderboard/seasonal` | 200 |
| 4 | `test_empty_leaderboard` | `GET /leaderboard/global` | 200, empty list |
| 5 | `test_unauthenticated` | `GET /leaderboard/global` | 401 |

### `test_review_endpoints.py` (~5 tests)

| # | Test | Route | Key Assertion |
|---|------|-------|---------------|
| 1 | `test_get_reviews_overview` | `GET /reviews` | 200 |
| 2 | `test_get_due_items` | `GET /reviews/{subject}` | 200, due items list |
| 3 | `test_submit_review` | `POST /reviews/{subject}/submit` | 200 with XP award |
| 4 | `test_unauthenticated` | `GET /reviews` | 401 |
| 5 | `test_nonexistent_subject` | `GET /reviews/{subject}` | 404 |

### `test_settings_endpoints.py` (~2 tests)

| # | Test | Route | Key Assertion |
|---|------|-------|---------------|
| 1 | `test_get_gamification_settings` | `GET /settings/gamification` | 200, cached settings |
| 2 | `test_unauthenticated` | `GET /settings/gamification` | 401 |

### `test_subscription_endpoints.py` (~2 tests)

| # | Test | Route | Key Assertion |
|---|------|-------|---------------|
| 1 | `test_get_subscriptions` | `GET /subscriptions` | 200, active subscriptions |
| 2 | `test_unauthenticated` | `GET /subscriptions` | 401 |

### `test_voucher_endpoints.py` (~4 tests)

| # | Test | Route | Key Assertion |
|---|------|-------|---------------|
| 1 | `test_preview_success` | `POST /voucher/preview` | 200, grant details |
| 2 | `test_preview_invalid_pin` | `POST /voucher/preview` | 404 or 400 |
| 3 | `test_redeem_success` | `POST /voucher/redeem` | 200, subscription created |
| 4 | `test_redeem_rate_limited` | `POST /voucher/redeem` | 429 after failures |

### `test_webhook_endpoints.py` (~4 tests)

| # | Test | Route | Key Assertion |
|---|------|-------|---------------|
| 1 | `test_payment_webhook_valid` | `POST /webhooks/payment` | 200, processed |
| 2 | `test_payment_webhook_invalid_signature` | `POST /webhooks/payment` | 401 or 403 |
| 3 | `test_payment_webhook_duplicate` | `POST /webhooks/payment` | Idempotent (no double-process) |
| 4 | `test_payment_webhook_invalid_payload` | `POST /webhooks/payment` | 400 or 422 |

### `test_notification_endpoints.py` (~3 tests)

| # | Test | Route | Key Assertion |
|---|------|-------|---------------|
| 1 | `test_ws_connect_valid_jwt` | `WS /ws/v1/notifications/{session_id}` | Connection established |
| 2 | `test_ws_connect_invalid_jwt` | `WS /ws/v1/notifications/{session_id}` | Connection rejected |
| 3 | `test_ws_receive_message` | `WS /ws/v1/notifications/{session_id}` | Published message received |

---

## Phase 7: Characterization Tests (~6 tests, 1 file)

### `fastapi_app/tests/test_findings.py`

These tests document **known bugs** by asserting current (buggy) behavior. When the bug is fixed, flip the assertion.

```python
class TestXPHydrationFailure:
    """FINDING-01: XP resets to 0 when hydration fails during completion.

    Severity: CRITICAL
    Location: fastapi_app/api/v1/endpoints/sessions.py ~line 303

    Current behavior: ensure_hydrated() swallows the error,
    HINCRBY proceeds on empty key, XP starts from 0.

    Expected behavior: completion should be queued for retry,
    not proceed with zero-base HINCRBY.
    """
    # test_current_behavior_xp_resets:
    #   Setup: Mock frappe.call to raise ConnectionError
    #   Action: award_xp(player_id, 50)
    #   Assert: new_xp == 50 (BUG: should be old_xp + 50)

class TestInteractionBufferLtrimRisk:
    """FINDING-02: LTRIM off-by-one on partial insert failure.

    Severity: MEDIUM
    Location: memora_admin/tasks/sync.py ~line 342-349

    Current behavior: LTRIM uses `inserted` count which may differ from
    actual items processed if some failed. Failed items at head are trimmed
    based on `inserted` count, which could skip items.

    Expected behavior: Use actual count of successfully processed items
    for LTRIM boundary.
    """
    # test_partial_insert_ltrim_boundary:
    #   Setup: Buffer with 5 items, make item 2 fail
    #   Action: flush_interaction_buffer()
    #   Assert: LTRIM boundary based on inserted count

class TestStatsDoubleCounting:
    """FINDING-03: Stats double-count on cold start race condition.

    Severity: LOW
    Location: fastapi_app/api/v1/endpoints/sessions.py

    Current behavior: If two concurrent session completions both trigger
    cold-start stats computation, the HINCRBY from both hits an
    already-accurate stats hash, double-counting.

    Expected behavior: Use SETNX or Lua for stats initialization
    to prevent race condition.
    """
    # test_concurrent_cold_start_race:
    #   Setup: No stats cache, hierarchy mock
    #   Action: Two concurrent end_session calls
    #   Assert: Stats may show double counts (document the race)
```

---

## Phase 8: Frappe Sync Task Tests (~19 tests, 3 files)

These run under `bench run-tests` (FrappeTestCase), not pytest.

### `memora_admin/memora_admin/tests/test_sync_wallets.py` (~8 tests)

**Task:** `memora_admin/tasks/sync.py:sync_dirty_wallets()`
**Flow:** Redis dirty set → HGETALL wallet hash → UPDATE Player Wallet in MariaDB → SREM from dirty

| # | Test | Key Assertion |
|---|------|---------------|
| 1 | `test_happy_path` | Dirty player → wallet fields updated in DB → removed from dirty set |
| 2 | `test_multiple_dirty` | 3 dirty players → all synced correctly |
| 3 | `test_empty_dirty_set` | No dirty players → no-op, no errors |
| 4 | `test_missing_wallet_record` | Player in dirty set but no DB record → warning logged, removed from dirty |
| 5 | `test_redis_wallet_missing` | Player in dirty set but no Redis hash → removed from dirty |
| 6 | `test_partial_failure` | 3 dirty, 1 DB error → 2 succeed, 1 remains in dirty |
| 7 | `test_dirty_flag_cleared` | After sync, `dirty_flag=0` in DB record |
| 8 | `test_sync_log_created` | Memora Sync Log doc created with `sync_type="Wallet"` |

### `memora_admin/memora_admin/tests/test_sync_progress.py` (~5 tests)

**Task:** `memora_admin/tasks/sync.py:sync_dirty_progress()`
**Flow:** Redis dirty set → GET bitmap → hex encode → UPSERT Structure Progress → SREM

| # | Test | Key Assertion |
|---|------|---------------|
| 1 | `test_bitmap_to_hex_upsert` | Bitmap bits → hex string → stored in Structure Progress doc |
| 2 | `test_new_record_created` | No existing doc → INSERT new Structure Progress |
| 3 | `test_existing_record_updated` | Existing doc → UPDATE passed_lessons_bitset |
| 4 | `test_invalid_dirty_member_format` | Malformed `"user:subject"` (missing version) → skipped, logged |
| 5 | `test_empty_bitmap` | All-zero bitmap → stores empty hex, completion=0% |

### `memora_admin/memora_admin/tests/test_flush_interactions.py` (~6 tests)

**Task:** `memora_admin/tasks/sync.py:flush_interaction_buffer()`
**Flow:** LRANGE buffer → JSON parse → INSERT Interaction Log docs → LTRIM

| # | Test | Key Assertion |
|---|------|---------------|
| 1 | `test_happy_path` | JSON items → Interaction Log docs created → buffer trimmed |
| 2 | `test_empty_buffer` | No items → no-op |
| 3 | `test_invalid_json_skipped` | Non-JSON item → skipped, others processed |
| 4 | `test_missing_fields_skipped` | Item without `player` or `lesson` → skipped |
| 5 | `test_batch_size_cap` | Buffer with 1500 items → only first 1000 processed (BATCH_SIZE) |
| 6 | `test_partial_failure_retry` | 3 items, 1 fails → 2 inserted, failed remains for retry |

---

## Summary

| Phase | Tests | Files | Focus |
|-------|-------|-------|-------|
| 1: Foundation | ~15 | 3 | Infrastructure + `calculate_xp_award` + `calculate_level` |
| 2: Core Services | ~31 | 3 | AccessService, ProgressService, WalletService |
| 3: Session + Auth | ~39 | 5 | SessionService, GameSessionService, OTPService, RateLimiter, DeviceService |
| 4: Remaining Services | ~25 | 10 | Voucher, Leaderboard, Stats, Hierarchy, Catalog, Profile, Plan, Settings, Purchase, Review |
| 5: Core Endpoints | ~64 | 6 | Health, Auth, Sessions, Progress, Wallet, Access |
| 6: Remaining Endpoints | ~41 | 11 | All other endpoints |
| 7: Characterization | ~6 | 1 | FINDINGs 01-03 (bug documentation) |
| 8: Frappe Sync | ~19 | 3 | sync_wallets, sync_progress, flush_interactions |
| **Total** | **~240** | **42** | |

---

## Critical Files Reference

| File | Role in Tests |
|------|---------------|
| `pyproject.toml` | Add `[tool.pytest.ini_options]` section |
| `fastapi_app/api/deps.py` | Override `get_redis`, `get_frappe_client` via `app.dependency_overrides` |
| `fastapi_app/core/security.py` | `create_access_token`, `create_refresh_token` for auth fixtures |
| `fastapi_app/core/config.py` | `Settings` class — override `get_settings()` in conftest to avoid needing .env |
| `fastapi_app/core/constants.py` | `DIRTY_*` keys, `GAME_SESSION_TTL`, `calculate_level`, `LEVEL_THRESHOLDS` |
| `fastapi_app/services/wallet.py` | `calculate_xp_award` (pure function), `STREAK_UPDATE_SCRIPT` (Lua) |
| `fastapi_app/services/game_session.py` | `START_SESSION_SCRIPT`, `SESSION_COMPLETE_SCRIPT` (Lua) |
| `fastapi_app/services/device.py` | `REGISTER_DEVICE_SCRIPT` (Lua) |
| `fastapi_app/services/rate_limit.py` | `RATE_LIMIT_SCRIPT` (Lua) |
| `fastapi_app/services/voucher.py` | `CHECK_LIMIT_SCRIPT`, `INCREMENT_SCRIPT` (Lua) |
| `fastapi_app/services/otp.py` | OTP Lua scripts |
| `fastapi_app/services/frappe_client.py` | `FrappeClient` — mock boundary for all tests |
| `fastapi_app/models/auth.py` | `TokenPayload` — decoded JWT model |
| `memora_admin/tasks/sync.py` | `sync_dirty_progress`, `sync_dirty_wallets`, `flush_interaction_buffer` |

---

## Verification Commands

```bash
# FastAPI tests (Phases 1-7)
cd /home/corex/aurevia-bench/apps/memora_admin
python3 -m pytest fastapi_app/tests/ -v --tb=short

# Run specific phase
python3 -m pytest fastapi_app/tests/test_xp_calculation.py -v        # Phase 1
python3 -m pytest fastapi_app/tests/test_access_service.py -v        # Phase 2
python3 -m pytest fastapi_app/tests/ -k "endpoint" -v                # Phases 5-6

# Frappe sync tests (Phase 8)
cd /home/corex/aurevia-bench
bench --site x.conanacademy.com run-tests --app memora_admin --module memora_admin.tests.test_sync_wallets
bench --site x.conanacademy.com run-tests --app memora_admin --module memora_admin.tests.test_sync_progress
bench --site x.conanacademy.com run-tests --app memora_admin --module memora_admin.tests.test_flush_interactions

# All existing Frappe tests still pass
bench --site x.conanacademy.com run-tests --app memora_admin
```
