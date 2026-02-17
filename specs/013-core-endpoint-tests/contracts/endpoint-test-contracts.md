# Endpoint Test Contracts

**Feature Branch**: `013-core-endpoint-tests`
**Date**: 2026-02-17

Each contract defines: route, method, auth requirement, request shape, expected response, and error cases.

---

## 1. Health Endpoints (`test_health_endpoints.py`)

### GET /api/v1/health/live

| Property | Value |
|----------|-------|
| Auth | None |
| Request | Empty |
| 200 Response | `{"status": "alive", "api_version": "v1"}` |

**Tests**:
- `test_liveness_ok`: GET → 200, verify status + api_version fields
- `test_liveness_no_auth_required`: GET without Authorization header → 200

### GET /api/v1/health/ready

| Property | Value |
|----------|-------|
| Auth | None |
| Request | Empty |
| 200 Response | `{"status": "ready", "api_version": "v1", "dependencies": {"redis": "ok"}}` |
| 503 Response | `{"status": "not_ready", "api_version": "v1", "dependencies": {"redis": "unreachable"}}` |

**Tests**:
- `test_readiness_ok`: GET → 200, verify redis dependency = "ok"
- `test_readiness_redis_down`: Mock redis.ping() to raise ConnectionError → 503, redis = "unreachable"

---

## 2. Auth Endpoints (`test_auth_endpoints.py`)

### POST /api/v1/auth/player/login

| Property | Value |
|----------|-------|
| Auth | None (creates auth) |
| Headers | `X-Device-ID: {device_uuid}` (required) |
| Request | `{"mobile": "201xxxxxxxxx", "password": "..."}` |
| 200 Response | `{"access_token": "...", "refresh_token": "...", "token_type": "bearer", "profile": {"display_name": "...", "avatar": "...", "xp": 0}}` |
| 400 Response | `{"detail": {"code": "DEVICE_ID_REQUIRED", ...}}` (missing X-Device-ID) |
| 401 Response | `{"detail": "Invalid credentials"}` (bad password) |
| 429 Response | `{"detail": "Too many login attempts", "retry_after": N}` + `Retry-After` header |

**Tests** (7):
- `test_player_login_success`: Mock verify_player_password → profile dict, verify 200 + tokens + profile
- `test_player_login_bad_credentials`: Mock verify_player_password → raise FrappeAPIError, verify 401
- `test_player_login_missing_device_id`: No X-Device-ID header → 400 DEVICE_ID_REQUIRED
- `test_player_login_rate_limited`: Exhaust rate limit (mock RateLimiter or call many times) → 429 + Retry-After
- `test_player_login_creates_session`: After login, verify Redis session key exists with fid
- `test_player_login_kicks_old_session`: Login twice, verify old fid replaced
- `test_player_login_registers_device`: After login, verify device hash exists in Redis

### POST /api/v1/auth/admin/login

| Property | Value |
|----------|-------|
| Auth | None (creates auth) |
| Request | `{"email": "admin@example.com", "password": "..."}` |
| 200 Response | `{"access_token": "...", "refresh_token": "...", "token_type": "bearer"}` |
| 401 Response | `{"detail": "Invalid credentials"}` |

**Tests** (2):
- `test_admin_login_success`: Mock FrappeAuthService.verify_credentials → (FrappeUser, {}) , verify 200 + tokens
- `test_admin_login_invalid_credentials`: Mock verify_credentials → (None, None), verify 401

### POST /api/v1/auth/refresh

| Property | Value |
|----------|-------|
| Auth | None (uses refresh token) |
| Request | `{"refresh_token": "..."}` |
| 200 Response | `{"access_token": "...", "refresh_token": "...", "token_type": "bearer"}` |
| 401 Response | `{"detail": "Invalid credentials"}` |

**Tests** (3):
- `test_refresh_valid_token`: Create refresh token, seed Redis session, verify 200 + new access token
- `test_refresh_expired_token`: Create token with negative expiry → 401
- `test_refresh_family_id_mismatch`: Seed Redis with different fid → 401

### GET /api/v1/auth/registration-options

| Property | Value |
|----------|-------|
| Auth | None |
| 200 Response | `{"grades": [...], "plans": [...], "seasons": [...]}` |

**Tests** (1):
- `test_registration_options`: Mock frappe_client.call → options dict, verify 200 + structure

### POST /api/v1/auth/player/register

| Property | Value |
|----------|-------|
| Auth | None |
| Request | `{"mobile": "...", "password": "...", "display_name": "...", "gender": "...", "grade": "...", "plan": "..."}` |
| 200 Response | `{"pending_id": "...", "message": "OTP sent"}` |
| 409 Response | `"Phone number already registered"` |

**Tests** (2):
- `test_register_success`: Mock check_phone_exists → {exists: false}, verify 200 + pending_id
- `test_register_duplicate_phone`: Mock check_phone_exists → {exists: true}, verify 409

### POST /api/v1/auth/player/register/verify

| Property | Value |
|----------|-------|
| Auth | None |
| Headers | `X-Device-ID: {device_uuid}` (required) |
| Request | `{"pending_id": "...", "otp": "..."}` |
| 200 Response | PlayerLoginResponse (tokens + profile) |
| 400/401 Response | Invalid OTP or expired pending |

**Tests** (2):
- `test_register_verify_valid_otp`: Pre-seed pending registration in Redis, mock register_player → profile, verify 200 + tokens
- `test_register_verify_invalid_otp`: Pre-seed pending, wrong OTP → error response

### POST /api/v1/auth/player/register/resend

| Property | Value |
|----------|-------|
| Auth | None |
| Request | `{"pending_id": "..."}` |
| 200 Response | `{"message": "OTP resent"}` |

**Tests** (1):
- `test_register_resend`: Pre-seed pending, verify 200

### POST /api/v1/auth/player/password-reset/request

| Property | Value |
|----------|-------|
| Auth | None |
| Request | `{"mobile": "201xxxxxxxxx"}` |
| 200 Response | `{"message": "If this number is registered, you will receive an OTP"}` (always 200) |

**Tests** (1):
- `test_password_reset_request_anti_enumeration`: Call for existing and non-existing phone → both return 200

### POST /api/v1/auth/player/password-reset/verify

| Property | Value |
|----------|-------|
| Auth | None |
| Request | `{"mobile": "...", "otp": "..."}` |
| 200 Response | `{"reset_token": "..."}` |
| 401 Response | Invalid OTP |

**Tests** (2):
- `test_password_reset_verify_valid`: Pre-seed reset OTP, verify 200 + reset_token
- `test_password_reset_verify_invalid`: Wrong OTP → error

### POST /api/v1/auth/player/password-reset/confirm

| Property | Value |
|----------|-------|
| Auth | None |
| Request | `{"reset_token": "...", "new_password": "newpass123"}` |
| 200 Response | `{"message": "Password reset successful. Please log in again."}` |
| 401 Response | Token already used / invalid |

**Tests** (2):
- `test_password_reset_confirm_success`: Pre-seed reset token, mock Frappe calls, verify 200
- `test_password_reset_confirm_reused_token`: Use token twice → second call fails

---

## 3. Session Endpoints (`test_session_endpoints.py`)

### GET /api/v1/sessions/current

| Property | Value |
|----------|-------|
| Auth | Bearer (player) |
| 200 Response | `{"session_id": "...", "lesson_id": "...", "subject_id": "...", "device_id": null, "started_at": "..."}` |
| 404 Response | `{"detail": {"code": "NO_ACTIVE_SESSION", ...}}` |
| 401 Response | No auth |

**Tests** (3):
- `test_get_current_active`: Seed game session hash → 200
- `test_get_current_none`: No session → 404
- `test_unauthenticated`: No Bearer → 401

### POST /api/v1/sessions/start

| Property | Value |
|----------|-------|
| Auth | Bearer (player) |
| Request | `{"lesson_id": "LESSON-001", "subject_id": "SUB-001"}` |
| 200 Response | `{"session_id": "...", "lesson_id": "LESSON-001"}` |
| 403 Response | `{"detail": {"code": "NO_ACCESS", ...}}` |
| 404 Response | `{"detail": {"code": "SUBJECT_NOT_FOUND", ...}}` |

**Tests** (5):
- `test_start_success`: Seed hierarchy + access grant → 200 + session_id
- `test_start_nonexistent_subject`: No hierarchy → 404
- `test_start_no_access`: No grant + no free content → 403
- `test_start_free_bypass`: No grant + hierarchy has free content → 200
- `test_start_nonexistent_lesson`: Hierarchy exists but lesson not found → 404

### POST /api/v1/sessions/end

| Property | Value |
|----------|-------|
| Auth | Bearer (player) |
| Request | `{"stages": [{"stage_id": "S1", "time_spent": 5000, "fail_count": 0, "completed_at": "..."}]}` |
| 200 Response | `{"success": true, "xp_awarded": N, "is_replay": false, "streak": N}` |
| 403 Response | `{"detail": {"code": "NO_ACTIVE_SESSION", ...}}` |

**Tests** (7):
- `test_end_success`: Seed full state (session + hierarchy + settings + wallet) → 200 + xp > 0
- `test_end_no_session`: No game session → 403
- `test_end_replay_detection`: Set bit before end → is_replay=True
- `test_end_streak_update`: Verify streak field in response
- `test_end_xp_awarded`: Verify xp_awarded > 0 for fresh completion
- `test_end_marks_dirty`: After end, verify player in dirty:wallets set
- `test_end_leaderboard_update`: After end, verify ZADD to leaderboard sorted set

---

## 4. Progress Endpoints (`test_progress_endpoints.py`)

### GET /api/v1/progress/

| Property | Value |
|----------|-------|
| Auth | Bearer (player) |
| 200 Response | `[{"subject_id": "...", "subject_name": "...", "percentage": 0.0, "completed": 0, "total": N}]` |
| 401 Response | No auth |

**Tests** (2):
- `test_progress_summary`: Seed access grants + hierarchy → 200 + list
- `test_unauthenticated`: No Bearer → 401

### GET /api/v1/progress/{subject}

| Property | Value |
|----------|-------|
| Auth | Bearer (player) |
| 200 Response | `{"subject_id": "...", "completed": N, "total": N, "tracks": [...]}` |
| 403 Response | NO_ACCESS |
| 404 Response | SUBJECT_NOT_FOUND |

**Tests** (4):
- `test_subject_progress`: Seed hierarchy + access → 200
- `test_subject_not_found`: No hierarchy → 404
- `test_access_denied`: No grant + no free content → 403
- `test_free_content_bypass`: No grant + free content → 200

### GET /api/v1/progress/{subject}/tracks

| Property | Value |
|----------|-------|
| Auth | Bearer (player) |
| 200 Response | `[{"track_id": "...", "completed": N, "total": N, "unlocked": true}]` |

**Tests** (1):
- `test_track_listing`: Seed hierarchy + access → 200 + list with track_id

### GET /api/v1/progress/{subject}/tracks/{track_id}

**Tests** (1):
- `test_track_detail`: Seed hierarchy + access → 200 + units list

### GET /api/v1/progress/{subject}/tracks/{track_id}/units/{unit_id}

**Tests** (1):
- `test_unit_detail`: Seed hierarchy + access → 200 + topics list

### GET /api/v1/progress/{subject}/topics/{topic_id}/lessons

**Tests** (1):
- `test_lesson_completion`: Seed hierarchy + access + progress bitmap → 200 + lessons with completed flags

---

## 5. Wallet Endpoints (`test_wallet_endpoints.py`)

### GET /api/v1/wallet

| Property | Value |
|----------|-------|
| Auth | Bearer (player) |
| 200 Response | `{"xp": N, "streak": N}` |
| 401 Response | No auth |

**Tests** (2):
- `test_get_own_wallet`: Seed wallet hash → 200 + correct xp/streak
- `test_empty_wallet_defaults`: No wallet data → 200 + xp=0, streak=0

### GET /api/v1/wallet/{player_id}

| Property | Value |
|----------|-------|
| Auth | Bearer (admin) |
| 200 Response | `{"xp": N, "streak": N}` |
| 403 Response | Non-admin |

**Tests** (2):
- `test_admin_get_player_wallet`: Admin client + seeded wallet → 200
- `test_non_admin_forbidden`: Player client → 403

---

## 6. Access Endpoints (`test_access_endpoints.py`)

### POST /api/v1/access/grants

| Property | Value |
|----------|-------|
| Auth | Bearer (admin) |
| Request | `{"player_id": "PLAYER-001", "content_keys": ["SUB-MATH"]}` |
| 200 Response | `{"granted": 1, "message": "Granted 1 new access key(s)"}` |
| 400 Response | EMPTY_KEYS (empty content_keys) |
| 403 Response | Non-admin |

**Tests** (3):
- `test_admin_grant_access`: Admin + valid request → 200 + granted=1
- `test_grant_idempotent`: Grant same key twice → second returns granted=0
- `test_non_admin_grant_forbidden`: Player client → 403

### DELETE /api/v1/access/grants

| Property | Value |
|----------|-------|
| Auth | Bearer (admin) |
| Request | `{"player_id": "PLAYER-001", "content_keys": ["SUB-MATH"]}` |
| 200 Response | `{"revoked": 1, "message": "Revoked 1 access key(s)"}` |
| 400 Response | EMPTY_KEYS |

**Tests** (1):
- `test_admin_revoke_access`: Grant then revoke → revoked=1

### GET /api/v1/access/grants/{player_id}

| Property | Value |
|----------|-------|
| Auth | Bearer (admin) |
| 200 Response | `{"player_id": "...", "grants": [...], "count": N}` |
| 403 Response | Non-admin |

**Tests** (2):
- `test_admin_list_grants`: Grant keys then list → returns granted keys
- `test_non_admin_list_forbidden`: Player client → 403

---

## Total Test Count: ~64

| File | Tests |
|------|-------|
| test_health_endpoints.py | 4 |
| test_auth_endpoints.py | 25 |
| test_session_endpoints.py | 15 |
| test_progress_endpoints.py | 10 |
| test_wallet_endpoints.py | 4 |
| test_access_endpoints.py | 6 |
| **Total** | **64** |
