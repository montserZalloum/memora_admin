# Player App — v2.0 Auth Migration Guide
---

## What Changed

Players now authenticate with **mobile number + password** instead of email + password.

| Before (v1.x) | After (v2.0) |
|---|---|
| Player login via email + password | Player login via **mobile number** + password |
| Single `/api/v1/auth/login` endpoint for all users | Separate `/api/v1/auth/player/login` and `/api/v1/auth/admin/login` |
| No registration flow (admin-created accounts) | Self-registration with **OTP verification** |
| No password reset flow | 3-step password reset with **OTP** |
| JWT `sub` = email address | JWT `sub` = `PLAYER-#####` (internal ID) |
| JWT has `email` claim | JWT has `mobile` claim (no `email` for players) |

---

## New Endpoints Summary

> **Base path:** All endpoints are under `/api/v1`. For example, `/auth/player/login` means the full URL is `https://x.conanacademy.com/api/v1/auth/player/login`.

| # | Method | Full Path | Purpose |
|---|--------|-----------|---------|
| 1 | `POST` | `/api/v1/auth/player/login` | Player login (mobile + password) |
| 2 | `POST` | `/api/v1/auth/admin/login` | Admin login (email + password) — unchanged |
| 3 | `POST` | `/api/v1/auth/refresh` | Refresh access token (works for both player and admin) |
| 4 | `GET` | `/api/v1/auth/registration-options` | Get form picker data (grades, plans, seasons) |
| 5 | `POST` | `/api/v1/auth/player/register` | Registration step 1: submit details, receive OTP |
| 6 | `POST` | `/api/v1/auth/player/register/verify` | Registration step 2: verify OTP, create account |
| 7 | `POST` | `/api/v1/auth/player/register/resend` | Resend registration OTP |
| 8 | `POST` | `/api/v1/auth/player/password-reset/request` | Password reset step 1: request OTP |
| 9 | `POST` | `/api/v1/auth/player/password-reset/verify` | Password reset step 2: verify OTP |
| 10 | `POST` | `/api/v1/auth/player/password-reset/confirm` | Password reset step 3: set new password |

---

## 1. Player Login

**`POST /api/v1/auth/player/login`**

### Headers (Required)

| Header | Required | Description |
|--------|----------|-------------|
| `X-Device-ID` | **Yes** | Unique device identifier (UUID or similar) |
| `X-Platform` | No | Platform hint: `iOS`, `Android`, or `Web` |
| `User-Agent` | No | Device user agent string |

### Request Body

```json
{
  "mobile": "962799555999",
  "password": "MyPassword123"
}
```

| Field | Type | Notes |
|-------|------|-------|
| `mobile` | string | Digits only (9-15 digits). No `+`, no dashes, no spaces. |
| `password` | string | Minimum 8 characters |

### Success Response — `200 OK`

```json
{
  "access_token": "eyJ...",
  "refresh_token": "eyJ...",
  "token_type": "bearer",
  "profile": {
    "display_name": "أحمد",
    "avatar": "pre",
    "xp": 1250
  }
}
```

### Error Responses

| Status | Body | When |
|--------|------|------|
| `400` | `{"detail": {"code": "DEVICE_ID_REQUIRED", "message": "X-Device-ID header required"}}` | Missing `X-Device-ID` header |
| `401` | `{"detail": "Invalid credentials"}` | Wrong mobile or password |
| `429` | `{"detail": "Too many login attempts", "retry_after": 45}` | Rate limit exceeded (10/min per IP, 5/min per account). Check `Retry-After` header. |
| `429` | `{"code": "DEVICE_LIMIT_EXCEEDED", "message": "Device limit reached (3/3)..."}` | Too many devices registered |

---

## 2. Token Refresh

**`POST /api/v1/auth/refresh`**

Works identically for both player and admin tokens. No changes from v1.x except:
- Player tokens now carry `mobile` claim instead of `email`
- The `sub` claim is `PLAYER-#####` for players

### Request Body

```json
{
  "refresh_token": "eyJ..."
}
```

### Success Response — `200 OK`

```json
{
  "access_token": "eyJ...(new)",
  "refresh_token": "eyJ...(same as sent)",
  "token_type": "bearer"
}
```

> **Note:** The refresh token is **not rotated** — you get the same one back. Store it once and reuse until it expires.

### Error Response — `401`

```json
{"detail": "Invalid credentials"}
```

Returned when: token expired, token invalid, or session was invalidated (e.g., password reset, new login from another device).

---

## 3. Registration Flow (2 Steps + Optional Resend)

### Step 0: Get Registration Options

**`GET /api/v1/auth/registration-options`**

Call this **once** to populate your form pickers. Cached for 5 minutes server-side.

#### Response — `200 OK`

```json
{
  "grades": [
    {
      "name": "GRADE-00001",
      "title": "الصف العاشر",
      "sort_order": 10,
      "majors": [
        {"name": "MAJOR-00001", "title": "علمي"},
        {"name": "MAJOR-00002", "title": "أدبي"}
      ]
    }
  ],
  "plans": [
    {
      "name": "PLAN-00001",
      "title": "خطة العاشر علمي",
      "grade": "GRADE-00001",
      "major": "MAJOR-00001"
    }
  ],
  "seasons": [
    {"name": "SEASON-00001", "title": "الفصل الأول 2026"}
  ]
}
```

> **Note:** Avatars and genders are **not** included — the client app already has these hardcoded.

**UI Logic:**
1. User selects **grade** → filter `plans` where `plan.grade == selected_grade`
2. If grade has **majors**, show major picker → filter plans by `plan.major` too
3. User selects **plan** from filtered list
4. Season is auto-selected server-side (latest published) — **don't send it**
5. Major is auto-derived from plan server-side — **send it only if you want to override**

### Step 1: Submit Registration

**`POST /api/v1/auth/player/register`**

#### Request Body

```json
{
  "mobile": "962799555999",
  "password": "MyPassword123",
  "display_name": "أحمد",
  "gender": "Male",
  "grade": "GRADE-00001",
  "plan": "PLAN-00001",
  "major": null
}
```

| Field | Type | Required | Notes |
|-------|------|----------|-------|
| `mobile` | string | Yes | Digits only, 9-15 digits |
| `password` | string | Yes | Minimum 8 characters |
| `display_name` | string | Yes | Player display name |
| `gender` | string | Yes | `"Male"` or `"Female"` |
| `grade` | string | Yes | DocType name from registration-options |
| `plan` | string | Yes | DocType name from registration-options |
| `major` | string | No | Auto-derived from plan if omitted |

#### Success Response — `200 OK`

```json
{
  "pending_id": "abc123...(opaque token)",
  "message": "OTP sent"
}
```

> **Save `pending_id`** — you need it for step 2 and for resend.

#### Error Responses

| Status | Body | When |
|--------|------|------|
| `409` | `{"detail": "Phone number already registered"}` | Mobile already has an account |
| `409` | `{"detail": "Phone number has a pending registration"}` | Already submitted, waiting for OTP verification |
| `429` | Rate limit error with `Retry-After` header | 3 OTPs/phone/10min or 10 OTPs/IP/10min exceeded |

### Step 2: Verify OTP

**`POST /api/v1/auth/player/register/verify`**

#### Headers (Required)

| Header | Required | Description |
|--------|----------|-------------|
| `X-Device-ID` | **Yes** | Same device ID as login |
| `X-Platform` | No | `iOS`, `Android`, or `Web` |

#### Request Body

```json
{
  "pending_id": "abc123...(from step 1)",
  "otp": "1111"
}
```

> **Current OTP for development/testing: `1111`** (static). Will be replaced with real SMS/WhatsApp delivery later.

#### Success Response — `200 OK` (Auto-Login)

On successful OTP verification, the account is created and the player is **automatically logged in**. Response is identical to the login response:

```json
{
  "access_token": "eyJ...",
  "refresh_token": "eyJ...",
  "token_type": "bearer",
  "profile": {
    "display_name": "أحمد",
    "avatar": "pre",
    "xp": 0
  }
}
```

#### Error Responses

| Status | Body | When |
|--------|------|------|
| `400` | `{"detail": {"code": "DEVICE_ID_REQUIRED", ...}}` | Missing `X-Device-ID` header |
| `401` | `{"detail": "OTP expired or invalid"}` | Wrong `pending_id` or OTP expired (5 min) |
| `401` | `{"detail": {"detail": "Invalid OTP", "remaining_attempts": 2}}` | Wrong OTP code. Show remaining attempts to user. |
| `401` | `{"detail": "Too many attempts. Please request a new OTP."}` | 3 wrong attempts — OTP invalidated, must restart |
| `409` | `{"detail": "Phone number already registered"}` | Race condition: someone registered this phone between step 1 and step 2 |
| `429` | `{"code": "DEVICE_LIMIT_EXCEEDED", ...}` | Device limit reached |

### Resend OTP

**`POST /api/v1/auth/player/register/resend`**

#### Request Body

```json
{
  "pending_id": "abc123...(from step 1)"
}
```

#### Success Response — `200 OK`

```json
{"message": "OTP resent"}
```

#### Error Responses

| Status | Body | When |
|--------|------|------|
| `401` | `{"detail": "Registration expired"}` | `pending_id` expired (5 min TTL) — must restart registration |
| `429` | `{"detail": "Please wait before requesting another OTP"}` with `Retry-After` header | 60-second cooldown between resends |

---

## 4. Password Reset Flow (3 Steps)

### Step 1: Request OTP

**`POST /api/v1/auth/player/password-reset/request`**

```json
{
  "mobile": "962799555999"
}
```

#### Response — Always `200 OK`

```json
{"message": "If this number is registered, you will receive an OTP"}
```

> **Anti-enumeration:** Always returns the same success message regardless of whether the phone exists. Never tell the user "phone not found".

#### Error Response

| Status | When |
|--------|------|
| `429` | Rate limit exceeded (same limits as registration OTP) |

### Step 2: Verify OTP

**`POST /api/v1/auth/player/password-reset/verify`**

```json
{
  "mobile": "962799555999",
  "otp": "1111"
}
```

#### Success Response — `200 OK`

```json
{
  "reset_token": "Xt7k9...(temporary token)"
}
```

> **Save `reset_token`** — needed for step 3. Valid for **15 minutes**, single-use.

#### Error Responses

Same pattern as registration OTP verify (`401` for expired/invalid/too many attempts).

### Step 3: Set New Password

**`POST /api/v1/auth/player/password-reset/confirm`**

```json
{
  "reset_token": "Xt7k9...(from step 2)",
  "new_password": "NewSecurePass123"
}
```

| Field | Notes |
|-------|-------|
| `reset_token` | From step 2. Single-use — cannot be reused. |
| `new_password` | Minimum 8 characters |

#### Success Response — `200 OK`

```json
{"message": "Password reset successful. Please log in again."}
```

> **Important:** After password reset, ALL existing sessions are invalidated. The user must log in again with the new password. Navigate to the login screen.

#### Error Responses

| Status | When |
|--------|------|
| `401` | `reset_token` expired (15 min) or already used |
| `401` | Account not found (shouldn't happen in normal flow) |

---

## 5. JWT Token Changes

### Access Token Payload (Player)

```json
{
  "sub": "PLAYER-00001",
  "mobile": "962799555999",
  "plan": "PLAN-00001",
  "name": "أحمد",
  "fid": "session-family-uuid",
  "type": "access",
  "iat": 1739000000,
  "exp": 1739003600,
  "jti": "unique-token-id"
}
```

| Claim | Change from v1.x |
|-------|-------------------|
| `sub` | Was email, now `PLAYER-#####` docname |
| `mobile` | **New** — player's phone number (digits only) |
| `email` | **Removed** from player tokens (only present in admin tokens) |
| `plan` | Unchanged |
| `name` | Unchanged (display name) |
| `fid` | Unchanged (session family ID) |

### Refresh Token Payload

```json
{
  "sub": "PLAYER-00001",
  "fid": "session-family-uuid",
  "type": "refresh",
  "iat": 1739000000,
  "exp": 1741592000,
  "jti": "unique-token-id"
}
```

Minimal payload. Same structure, just `sub` changed from email to `PLAYER-#####`.

### Token Lifetimes

| Token | Lifetime |
|-------|----------|
| Access token | 60 minutes |
| Refresh token | Driven by `session_timeout_days` in Memora Settings (admin-configurable) |

---

## 6. Rate Limiting Summary

| Limit | Scope | Window | Applies To |
|-------|-------|--------|------------|
| 10 attempts/min per IP | Login | 1 minute | `/auth/player/login` |
| 5 attempts/min per account | Login | 1 minute | `/auth/player/login` |
| 3 OTPs per phone | OTP send | 10 minutes | `/auth/player/register`, `/auth/player/password-reset/request` |
| 10 OTPs per IP | OTP send | 10 minutes | Same as above |
| 60 second cooldown | OTP resend | Per request | `/auth/player/register/resend`, between OTP requests |
| 3 attempts max | OTP verify | Per OTP | Wrong OTP code invalidates the OTP on 3rd failure |

All `429` responses include a `Retry-After` header (seconds until retry is allowed).

---

## 7. Mobile Number Format

The backend normalizes phone numbers to **digits only**:

| Input | Stored As | Valid? |
|-------|-----------|--------|
| `+962-799-555999` | `962799555999` | Yes (12 digits) |
| `962799555999` | `962799555999` | Yes |
| `0799555999` | `0799555999` | Yes (10 digits) |
| `12345678` | rejected | No (8 digits, minimum is 9) |
| `1234567890123456` | rejected | No (16 digits, maximum is 15) |

**Recommendation:** Strip `+`, dashes, and spaces on the client side before sending. Send digits only, 9-15 digits long.

---

## 8. Required App-Side Changes Summary

### Login Screen
- [ ] Change input from **email** to **mobile number** (numeric keyboard)
- [ ] Send `mobile` instead of `email` in login request body
- [ ] Hit `POST /api/v1/auth/player/login` instead of old login endpoint
- [ ] Always send `X-Device-ID` header
- [ ] Handle `DEVICE_LIMIT_EXCEEDED` error (show "contact support" message)

### Registration Screen (New)
- [ ] Fetch `GET /api/v1/auth/registration-options` for form pickers
- [ ] Build cascading pickers: Grade → Major (if grade has majors) → Plan
- [ ] Collect: mobile, password, display_name, gender, grade, plan
- [ ] Submit to `POST /api/v1/auth/player/register`
- [ ] Show OTP input screen with `pending_id`
- [ ] Submit OTP to `POST /api/v1/auth/player/register/verify` (include `X-Device-ID`)
- [ ] On success: store tokens + navigate to home (auto-login)
- [ ] Implement "Resend OTP" button with 60s cooldown timer
- [ ] Handle `remaining_attempts` in OTP error to show user

### Password Reset Screen (New)
- [ ] Step 1: Collect mobile → `POST /api/v1/auth/player/password-reset/request`
- [ ] Show generic "check your phone" message (anti-enumeration)
- [ ] Step 2: Collect OTP → `POST /api/v1/auth/player/password-reset/verify`
- [ ] Step 3: Collect new password (+ confirm) → `POST /api/v1/auth/player/password-reset/confirm`
- [ ] On success: navigate to login screen

### Token Storage
- [ ] JWT `sub` is now `PLAYER-#####` (not email) — update if you reference it locally
- [ ] Use `mobile` claim from JWT if you need to display the phone number
- [ ] `email` claim will be absent from player tokens — don't rely on it

### General
- [ ] All existing authenticated endpoints work unchanged (same `Authorization: Bearer <token>` header)
- [ ] Player identity in the system is now `PLAYER-#####` — if you store user IDs locally, they will be in this format for new accounts

---

## 9. OTP Notes (Development)

Currently using a **static OTP provider** for development:
- OTP code is always **`1111`**
- No actual SMS/WhatsApp is sent
- Logs are printed server-side for debugging

When real SMS/WhatsApp delivery is integrated later, the API contract stays identical — only the OTP value will be dynamic.
