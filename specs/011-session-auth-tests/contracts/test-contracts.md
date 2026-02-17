# Test Contracts: Session + Auth Service Tests

**Feature**: 011-session-auth-tests
**Date**: 2026-02-17

## Overview

This feature has no API contracts (no new endpoints). Instead, test contracts define the **expected behavior** of each service method that tests must verify.

## File 1: test_game_session_service.py (~8 tests)

### TC-GS-01: Start session creates Redis hash with TTL
- **Input**: `start_session("USER-001", "LESSON-001", "MATH-G5", "device-abc")`
- **Assert return**: UUID string (non-empty)
- **Assert Redis**: `HGETALL {prefix}gamesession:USER-001` returns hash with 5 fields
- **Assert Redis**: `TTL {prefix}gamesession:USER-001` == 3600

### TC-GS-02: Start session force-closes existing session
- **Setup**: Call `start_session` to create session A
- **Input**: Call `start_session` again to create session B
- **Assert**: Session B's session_id differs from A's
- **Assert Redis**: Only one session hash exists (the new one)

### TC-GS-03: Get active session returns GameSession model
- **Setup**: `start_session("USER-001", "LESSON-001", "MATH-G5")`
- **Input**: `get_active_session("USER-001")`
- **Assert**: Returns `GameSession` with correct `lesson_id`, `subject_id`

### TC-GS-04: Get active session returns None when no session
- **Input**: `get_active_session("USER-NONEXISTENT")`
- **Assert**: Returns `None`

### TC-GS-05: End session returns data and deletes hash
- **Setup**: `start_session("USER-001", "LESSON-001", "MATH-G5")`
- **Input**: `end_session("USER-001")`
- **Assert return**: `GameSession` with `lesson_id="LESSON-001"`
- **Assert Redis**: `EXISTS {prefix}gamesession:USER-001` == 0

### TC-GS-06: Complete session sets progress bit (first completion)
- **Setup**: `start_session("USER-001", "LESSON-001", "MATH-G5")`
- **Input**: `complete_session("USER-001", bit_index=5, subject_id="MATH-G5", version=1, interaction_jsons=["{}"])`
- **Assert return**: `(True, False, GameSession)` — success=True, is_replay=False
- **Assert Redis**: `GETBIT {prefix}progress:USER-001:MATH-G5:v1 5` == 1
- **Assert Redis**: `SISMEMBER memora:dirty:progress "USER-001:MATH-G5:v1"` == True
- **Assert Redis**: `LLEN memora:buffer:interactions` >= 1

### TC-GS-07: Complete session detects replay
- **Setup**: Start session, complete once, start new session
- **Input**: `complete_session` with same bit_index
- **Assert return**: `(True, True, GameSession)` — is_replay=True

### TC-GS-08: Complete session with interaction buffer
- **Setup**: `start_session`
- **Input**: `complete_session` with `interaction_jsons=['{"stage":"S1"}', '{"stage":"S2"}']`
- **Assert Redis**: `LRANGE memora:buffer:interactions 0 -1` contains both JSON strings

## File 2: test_otp_service.py (~12 tests)

### TC-OTP-01: Create pending registration returns pending_id
- **Input**: `create_pending_registration("201000000000", "pass", "Player", "M", "G10", "PLAN-001", None, "1.2.3.4")`
- **Assert return**: Non-empty string
- **Assert Redis**: `GET {prefix}pending:{id}` contains JSON with mobile, otp="1111", attempts=0
- **Assert Redis**: `EXISTS {prefix}phone_reserved:201000000000` == 1
- **Assert Redis**: `TTL {prefix}pending:{id}` <= 300

### TC-OTP-02: Verify correct OTP returns registration data
- **Setup**: `create_pending_registration` to get `pending_id`
- **Input**: `verify_registration_otp(pending_id, "1111")`
- **Assert return**: dict with mobile, password, display_name (no otp, no attempts)
- **Assert Redis**: `EXISTS {prefix}pending:{pending_id}` == 0 (cleaned up)
- **Assert Redis**: `EXISTS {prefix}phone_reserved:201000000000` == 0

### TC-OTP-03: Verify wrong OTP increments attempts
- **Setup**: `create_pending_registration`
- **Input**: `verify_registration_otp(pending_id, "9999")`
- **Assert**: Raises `HTTPException(401)`
- **Assert Redis**: Pending data `attempts` field == 1

### TC-OTP-04: Max attempts exhausted deletes pending
- **Setup**: Create pending, submit 3 wrong OTPs
- **Input**: 4th call to `verify_registration_otp`
- **Assert**: Raises `HTTPException(401)` with "Too many attempts"
- **Assert Redis**: Pending key deleted, phone reservation deleted

### TC-OTP-05: Resend cooldown blocks rapid resend
- **Setup**: `create_pending_registration` (sets cooldown)
- **Input**: `resend_registration_otp` immediately
- **Assert**: Raises `HTTPException(429)` with "Please wait"

### TC-OTP-06: Phone rate limit blocks excess requests
- **Setup**: Call `create_pending_registration` 3 times for different pending IDs (need to clear phone_reserved between calls)
- **Input**: 4th call exceeds PHONE_LIMIT=3
- **Assert**: Raises `HTTPException(429)` with "Too many OTP requests for this phone number"

### TC-OTP-07: IP rate limit blocks excess requests
- **Setup**: Exhaust IP limit (10 requests from same IP)
- **Input**: 11th request
- **Assert**: Raises `HTTPException(429)` with "Too many OTP requests from this IP address"

### TC-OTP-08: Create password reset stores OTP
- **Input**: `create_password_reset("201000000000", "1.2.3.4", phone_exists=True)`
- **Assert Redis**: `GET {prefix}reset:201000000000` contains JSON with otp="1111"
- **Assert Redis**: `TTL {prefix}reset:201000000000` <= 300

### TC-OTP-09: Password reset anti-enumeration (phone not found)
- **Input**: `create_password_reset("201999999999", "1.2.3.4", phone_exists=False)`
- **Assert Redis**: `EXISTS {prefix}reset:201999999999` == 0 (no OTP stored)
- **Assert**: No exception raised (silent operation)

### TC-OTP-10: Verify password reset OTP returns reset token
- **Setup**: `create_password_reset(phone_exists=True)`
- **Input**: `verify_password_reset_otp("201000000000", "1111")`
- **Assert return**: Non-empty token string
- **Assert Redis**: `EXISTS {prefix}reset_token:{token}` == 1
- **Assert Redis**: Value of reset_token key == "201000000000"

### TC-OTP-11: Reset token single-use (consumed on validate)
- **Setup**: Create reset, verify OTP to get token
- **Input**: `validate_reset_token(token)` — first call
- **Assert return**: "201000000000"
- **Input**: `validate_reset_token(token)` — second call
- **Assert**: Raises `HTTPException(401)`

### TC-OTP-12: Verify expired/missing pending raises 401
- **Input**: `verify_registration_otp("nonexistent-id", "1111")`
- **Assert**: Raises `HTTPException(401)` with "OTP expired or invalid"

## File 3: test_session_service.py (~5 tests)

### TC-SS-01: Create session stores JSON and returns family_id
- **Input**: `create_session("USER-001", "PLAN-001")`
- **Assert return**: UUID string
- **Assert Redis**: `GET {prefix}session:USER-001` == `{"fid": fid, "plan": "PLAN-001"}`
- **Assert Redis**: `TTL` ~= 30*86400

### TC-SS-02: Validate matching family_id returns (True, plan)
- **Setup**: `create_session` to get family_id
- **Input**: `validate_session("USER-001", family_id)`
- **Assert**: Returns `(True, "PLAN-001")`

### TC-SS-03: Validate mismatched family_id returns (False, None)
- **Setup**: `create_session` to get family_id
- **Input**: `validate_session("USER-001", "wrong-uuid")`
- **Assert**: Returns `(False, None)`

### TC-SS-04: Invalidate session deletes key
- **Setup**: `create_session`
- **Input**: `invalidate_session("USER-001")`
- **Assert return**: `True`
- **Assert Redis**: `EXISTS {prefix}session:USER-001` == 0

### TC-SS-05: Create session overwrites previous
- **Setup**: `create_session` → fid_A
- **Input**: `create_session` again → fid_B
- **Assert**: fid_A != fid_B
- **Assert**: `validate_session("USER-001", fid_A)` returns `(False, None)`
- **Assert**: `validate_session("USER-001", fid_B)` returns `(True, plan)`

## File 4: test_rate_limiter.py (~6 tests)

### TC-RL-01: First request is allowed
- **Input**: `check_rate_limit("1.2.3.4", "user@test.com")`
- **Assert**: `(True, 0, "")`

### TC-RL-02: IP limit exceeded blocks request
- **Setup**: Call `check_rate_limit` 10 times (ip_limit=10)
- **Input**: 11th call
- **Assert**: `(False, retry_after>0, "ip")`

### TC-RL-03: Account limit exceeded blocks request
- **Setup**: Call `check_rate_limit` 5 times (account_limit=5)
- **Input**: 6th call
- **Assert**: `(False, retry_after>0, "account")`

### TC-RL-04: Get remaining returns correct counts
- **Setup**: 3 calls with IP "1.2.3.4" and account "user@test.com"
- **Input**: `get_remaining("1.2.3.4", "user@test.com")`
- **Assert**: `(7, 2)` — ip_remaining=10-3, account_remaining=5-3

### TC-RL-05: Account normalized to lowercase
- **Setup**: Call with `target_account="User@Test.COM"`
- **Input**: `get_remaining("1.2.3.4", "user@test.com")` (lowercase)
- **Assert**: account_remaining reflects the call above (same counter)

### TC-RL-06: No account skips account check
- **Setup**: Exhaust IP limit with `target_account=None`
- **Input**: `check_rate_limit("1.2.3.4", None)`
- **Assert**: `(False, retry_after>0, "ip")` — only IP checked

## File 5: test_device_service.py (~8 tests)

### TC-DS-01: Register new device
- **Input**: `register_device("USER-001", "dev-001", "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X)", 3)`
- **Assert return**: `DeviceRegistrationResult(success=True, status="new")`
- **Assert Redis**: Hash has 6 fields for `device:dev-001:*`

### TC-DS-02: Register existing device updates last_login
- **Setup**: Register device "dev-001"
- **Input**: Register "dev-001" again
- **Assert return**: `status="existing"`
- **Assert Redis**: `last_login` field updated

### TC-DS-03: Fingerprint match replaces old device
- **Setup**: Register device "dev-001" with user_agent "UA-iPhone"
- **Input**: Register "dev-002" with same user_agent (same fingerprint)
- **Assert return**: `status="fingerprint_match"`
- **Assert Redis**: "dev-001" fields deleted, "dev-002" fields exist

### TC-DS-04: Device limit exceeded
- **Setup**: Register 3 devices (max_devices=3)
- **Input**: Register 4th device with unique fingerprint
- **Assert return**: `success=False, status="limit_exceeded", current_count=3, max_count=3`

### TC-DS-05: Get devices returns list
- **Setup**: Register 2 devices
- **Input**: `get_devices("USER-001")`
- **Assert**: List of 2 `DeviceInfo` objects with correct fields

### TC-DS-06: Remove device deletes hash fields
- **Setup**: Register device "dev-001"
- **Input**: `remove_device("USER-001", "dev-001")`
- **Assert return**: `True`
- **Assert Redis**: All 6 fields for "dev-001" deleted

### TC-DS-07: Validate registered device returns True
- **Setup**: Register device "dev-001"
- **Input**: `validate_device("USER-001", "dev-001")`
- **Assert**: `True`

### TC-DS-08: Validate unknown device returns False
- **Input**: `validate_device("USER-001", "unknown-dev")`
- **Assert**: `False`
