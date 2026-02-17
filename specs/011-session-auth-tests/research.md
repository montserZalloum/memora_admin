# Research: Session + Auth Service Tests

**Feature**: 011-session-auth-tests
**Date**: 2026-02-17

## Research Tasks & Findings

### R1: Game Session Lua Script Behavior

**Decision**: Test Lua scripts through service methods against real Redis, not raw `EVALSHA`.

**Rationale**: Lua scripts are registered via `redis.register_script()` and cached on the service instance. Testing through the public API exercises the full code path including argument serialization, key construction, and result parsing. The service handles bytes-to-string decoding of `HGETALL` results from the Lua script return.

**Key findings**:
- `START_SESSION_SCRIPT`: KEYS[1]=session hash, ARGV[1..6]=session_id, lesson_id, subject_id, device_id, timestamp, ttl. Always DELetes existing session first (force-close). Returns `{1, session_id}`.
- `SESSION_COMPLETE_SCRIPT`: 4 KEYS (session, progress bitmap, dirty set, interaction buffer), N ARGV (bit_index, dirty_member, ...interaction_jsons). Returns `{0}` if no session, `{1, prev_bit, ...session_fields}` on success. `prev_bit=0` means first completion, `1` means replay.
- Both scripts use lazy-loaded caching: `self._start_script` and `self._complete_script`.

**Alternatives considered**: Testing raw Lua via `EVALSHA` — rejected because it bypasses the service's key construction and argument serialization logic.

---

### R2: OTP Service Architecture

**Decision**: Use `StaticOTPProvider` (always "1111") for all tests. Mock nothing except FrappeClient.

**Rationale**: The `StaticOTPProvider` is the built-in dev provider that logs instead of sending SMS. Using it means tests exercise the real OTP flow without mocking the provider layer.

**Key findings**:
- OTP is always `"1111"` in dev mode (hardcoded in `create_pending_registration` line 182 and `create_password_reset` line 329)
- `pending_id` = `secrets.token_urlsafe(32)` — opaque, URL-safe
- Rate limiting uses same Lua script as `RateLimiter` (atomic INCR + conditional EXPIRE)
- OTP rate limits: phone=3/10min, IP=10/10min (separate from login rate limits)
- Cooldown: 60s between resend requests per phone
- Max attempts: 3 wrong OTP entries per pending registration
- Password reset has anti-enumeration: rate limit + cooldown ALWAYS runs regardless of `phone_exists`
- Reset token: `secrets.token_urlsafe(32)`, stored as `reset_token:{token}` → mobile, 15min TTL, single-use (deleted on validate)

**Redis key patterns**:
| Key | TTL | Data |
|-----|-----|------|
| `{prefix}pending:{pending_id}` | 300s | JSON: mobile, password, display_name, gender, grade, plan, major, otp, attempts |
| `{prefix}phone_reserved:{mobile}` | 300s | "1" (SETNX lock) |
| `{prefix}ratelimit:otp:phone:{mobile}` | 600s | Integer counter |
| `{prefix}ratelimit:otp:ip:{ip}` | 600s | Integer counter |
| `{prefix}ratelimit:otp:cooldown:{mobile}` | 60s | "1" (flag) |
| `{prefix}reset:{mobile}` | 300s | JSON: otp, attempts |
| `{prefix}reset_token:{token}` | 900s | Mobile string |

---

### R3: Rate Limiter Sliding Window Pattern

**Decision**: Test the Lua script's conditional EXPIRE behavior (TTL set only on count==1).

**Rationale**: The rate limiter uses a simple counter with a twist: `EXPIRE` is only called when `INCR` returns 1 (first request in window). This means the window slides from the first request, not from each request. Testing must verify this specific behavior.

**Key findings**:
- `RateLimiter` constructor: `key_prefix="memora:ratelimit:"`, `ip_limit=10`, `account_limit=5`, `window_seconds=60`
- Key patterns: `{prefix}ip:{ip}`, `{prefix}account:{account.lower()}`
- `check_rate_limit()` returns `(allowed: bool, retry_after: int, limit_type: str)`
- `get_remaining()` returns `(ip_remaining: int, account_remaining: int)` — uses GET (not the Lua script)
- Account key normalizes to lowercase
- `retry_after` = max(TTL, 1) to ensure positive value

**Alternatives considered**: Testing with `time.sleep()` for TTL expiry — rejected per constitution (use mocked time or short TTL values).

---

### R4: Device Registration Lua Script

**Decision**: Test all 4 code paths of `REGISTER_DEVICE_SCRIPT` by manipulating Redis state before each call.

**Rationale**: The Lua script has 4 distinct branches that must each be exercised: existing device, fingerprint match, new device, and limit exceeded.

**Key findings**:
- `REGISTER_DEVICE_SCRIPT`: KEYS[1]=`{prefix}devices:{user_id}`, ARGV[1..7]=device_id, device_name, user_agent, platform, timestamp, max_devices, fingerprint
- 6 hash fields per device: `device:{id}:name`, `device:{id}:ua`, `device:{id}:platform`, `device:{id}:last_login`, `device:{id}:fingerprint`, `device:{id}:push_token`
- Return format: `{1, device_id, status}` (success) or `{0, '', "limit_exceeded", count, max}` (failure)
- Fingerprint scan iterates ALL `device:*:fingerprint` fields — O(N) where N = registered devices
- Fingerprint format: `device_family:brand:os_family:browser_family` (no version numbers)
- `_generate_fingerprint()` uses `user_agents.parse()` library
- `max_devices` default is 3 (from `Memora Settings` DocType)
- No TTL on device hash — devices persist until removed

**Methods to test**:
1. `register_device()` → 4 Lua paths
2. `get_devices()` → list DeviceInfo from hash
3. `remove_device()` → HDEL 6 fields
4. `validate_device()` → HEXISTS check
5. `update_push_token()` → HSET if device exists

---

### R5: Session Service JSON Format

**Decision**: Test both JSON format `{"fid": uuid, "plan": plan_id}` and legacy plain-string format.

**Rationale**: `get_session_data()` has a `try/except json.JSONDecodeError` that falls back to `{"fid": raw, "plan": None}` for legacy plain-string sessions. Both paths must be tested.

**Key findings**:
- `create_session()`: Generates UUID family_id, stores JSON `{"fid": fid, "plan": plan_id}`, 30-day TTL
- `validate_session()`: Returns `(True, plan_id)` or `(False, None)`
- `invalidate_session()`: DEL key, returns bool
- `get_session_data()`: JSON parse with legacy fallback
- `get_session_family_id()`: Convenience wrapper
- Key: `{prefix}{user_id}` (note: prefix includes `session:` by default)

---

### R6: Test Infrastructure Compatibility

**Decision**: Use existing `conftest.py` fixtures without modification. Create per-service fixtures in each test file.

**Rationale**: The existing fixtures (redis_client, test_prefix, cleanup_keys, mock_frappe) provide exactly what Phase 3 services need. Each test file creates its own service fixture using `test_prefix` for key isolation.

**Key patterns to follow**:
1. Service fixture: `ServiceClass(redis_client, key_prefix=test_prefix, ...)`
2. Constants at module level: `TEST_PLAYER = "PLAYER-TEST-001"`
3. Test classes group related scenarios: `TestStartSession`, `TestCompleteSession`, etc.
4. Direct Redis verification: `await redis_client.hgetall(key)` after service calls
5. Additional autouse fixtures for global keys (dirty sets, interaction buffer) that fall outside `test_prefix` scope

**Important**: `DIRTY_PROGRESS_KEY` and `INTERACTION_BUFFER_KEY` are hardcoded global constants (no prefix). Tests using `complete_session` must clean these up explicitly.

---

### R7: Prefix Isolation for Rate Limiter

**Decision**: Inject `test_prefix` as `key_prefix` for `RateLimiter`, overriding the default `"memora:ratelimit:"`.

**Rationale**: `RateLimiter.__init__` accepts `key_prefix` parameter (default `"memora:ratelimit:"`). By passing `test_prefix`, all rate limit keys fall under the test namespace and get auto-cleaned.

**Key consideration**: The `OTPService` constructs its own rate limit keys using `self.prefix` (e.g., `{prefix}ratelimit:otp:phone:{mobile}`). Passing `test_prefix` as `key_prefix` to `OTPService` ensures these also get auto-cleaned.
