# Phase 31: FastAPI Auth Endpoints + OTP System - Research

**Researched:** 2026-02-12
**Domain:** FastAPI endpoint layer for player authentication (login, registration with OTP, password reset with OTP), admin login, token refresh
**Confidence:** HIGH

## Summary

This phase builds the FastAPI endpoint layer on top of the Phase 30 Frappe Auth API Bridge. The three Frappe whitelisted APIs (`verify_player_password`, `register_player`, `set_player_password`) already exist and handle all password operations, DocType creation, and wallet initialization. Phase 31 adds the FastAPI endpoints that call these APIs, plus the OTP verification system (with static "1111" stub), rate limiting for OTP, and the registration options endpoint.

The codebase already has every building block needed: `FrappeClient` for calling Frappe APIs, `SessionService` for session management, `DeviceService` for device registration, `RateLimiter` with Lua scripts for atomic rate limiting, `WalletService` for XP fetch, and `create_access_token`/`create_refresh_token` for JWT creation. The existing `auth.py` endpoint file demonstrates the exact patterns (device check, rate limit, session create, token create, enriched response) that the new player login endpoint must follow.

The primary new code is: (1) an OTP service with Redis-backed storage and pluggable provider interface, (2) refactored auth endpoints splitting the current `/auth/login` into `/auth/player/login` and `/auth/admin/login`, (3) registration endpoints with 2-step OTP flow (request + verify), (4) password reset endpoints with 3-step OTP flow (request + verify + confirm), (5) a registration options endpoint, and (6) updated `create_access_token` to support `mobile` claim and make `email` optional.

**Primary recommendation:** Rewrite `fastapi_app/api/v1/endpoints/auth.py` completely. The new file replaces the single `/auth/login` with separate player/admin endpoints and adds registration + password reset flows. Create `fastapi_app/services/otp.py` as a new OTP service. Keep the existing services (`SessionService`, `DeviceService`, `RateLimiter`, `WalletService`) unchanged -- they work with any string user_id.

## Standard Stack

### Core

| Library/Module | Version | Purpose | Why Standard |
|----------------|---------|---------|--------------|
| `fastapi` | Already installed | Route handlers, request/response models, dependency injection | The application framework |
| `pydantic` | Already installed | Request/response validation models | FastAPI's model layer |
| `redis.asyncio` | Already installed | OTP storage, rate limiting, session management | Established async Redis pattern |
| `secrets` (stdlib) | Python 3.10+ | `secrets.token_urlsafe(32)` for temp tokens and pending IDs | Cryptographically secure randomness |
| `PyJWT` | Already installed | JWT access/refresh token creation and decoding | Existing `security.py` |
| `httpx` | Already installed | FrappeClient calls to Frappe auth APIs | Existing `frappe_client.py` |
| `structlog` | Already installed | Structured logging for OTP events | Established logging pattern |

### Supporting

| Library/Module | Version | Purpose | When to Use |
|----------------|---------|---------|-------------|
| `FrappeClient` (existing) | N/A | Call `verify_player_password`, `register_player`, `set_player_password` | All auth operations that touch Frappe |
| `SessionService` (existing) | N/A | Create/validate/invalidate sessions | Login, refresh, password reset |
| `DeviceService` (existing) | N/A | Register devices, enforce limits | Player login |
| `RateLimiter` (existing) | N/A | Login rate limiting | Player login (existing pattern) |
| `WalletService` (existing) | N/A | Fetch XP for enriched login response | Player login |
| `SettingsService` (existing) | N/A | Fetch `session_timeout_days` for refresh token TTL | Login session creation |

### Alternatives Considered

| Instead of | Could Use | Tradeoff |
|------------|-----------|----------|
| Redis-backed OTP with manual TTL | JWT-based OTP tokens | Redis gives server-side revocation, attempt tracking, and single-use deletion. JWT would require a blocklist. Redis is simpler and matches existing patterns. |
| `secrets.token_urlsafe(32)` for pending IDs | UUID4 | Both are fine. `secrets.token_urlsafe` produces 256-bit entropy, slightly more secure than UUID4's 122 bits. Consistent with RESET-04 requirement. |
| Rewriting auth.py completely | Patching existing auth.py | Complete rewrite is cleaner. The old `/auth/login` and `/auth/refresh` must be removed (MIGR-07). The new endpoint structure is fundamentally different (player vs admin separation). |

**No new dependencies required.** Everything is already installed.

## Architecture Patterns

### Recommended Project Structure

```
fastapi_app/
├── api/v1/endpoints/
│   └── auth.py              # REWRITE: player login, admin login, refresh, register, password-reset
├── services/
│   └── otp.py               # NEW: OTP service with provider protocol, Redis storage, rate limiting
├── models/
│   └── auth.py              # MODIFY: new request/response models for registration, OTP, password reset
└── core/
    ├── security.py          # MODIFY: create_access_token gets mobile param, email becomes optional
    └── config.py            # MODIFY: jwt_access_token_expire_minutes default -> 60
```

### Pattern 1: OTP Service with Pluggable Provider

**What:** An `OTPService` class that manages OTP lifecycle (generate, store, verify, rate limit) with a `Protocol`-based provider interface for the actual "sending" mechanism.

**When to use:** All OTP operations (registration and password reset).

**Example:**
```python
# fastapi_app/services/otp.py
import secrets
import json
from typing import Protocol, runtime_checkable

import redis.asyncio as redis
import structlog

logger = structlog.get_logger()


@runtime_checkable
class OTPProvider(Protocol):
    """Pluggable OTP delivery interface. Swap StaticOTPProvider for real SMS later."""

    async def send_otp(self, mobile: str, otp: str) -> bool:
        """Send OTP to mobile number. Returns True on success."""
        ...


class StaticOTPProvider:
    """Development provider -- always uses '1111', logs instead of sending."""

    async def send_otp(self, mobile: str, otp: str) -> bool:
        logger.info("otp_sent_static", mobile_suffix=mobile[-4:], otp_length=len(otp))
        return True


class OTPService:
    """Manages OTP lifecycle: generate, store in Redis, verify, rate limit."""

    OTP_TTL = 300           # 5 minutes
    RESET_TOKEN_TTL = 900   # 15 minutes for password reset temp token
    MAX_ATTEMPTS = 3
    COOLDOWN_TTL = 60       # 60-second resend cooldown
    RATE_LIMIT_WINDOW = 600 # 10-minute rate limit window
    PHONE_LIMIT = 3         # 3 OTP sends per phone per 10 min
    IP_LIMIT = 10           # 10 OTP sends per IP per 10 min

    def __init__(
        self,
        redis_client: redis.Redis,
        provider: OTPProvider | None = None,
        key_prefix: str = "memora:",
    ):
        self.redis = redis_client
        self.provider = provider or StaticOTPProvider()
        self.prefix = key_prefix
```

### Pattern 2: Pending Registration State in Redis

**What:** Store all registration fields + OTP + attempt counter in a single Redis JSON key with TTL. The phone number is NOT created in MariaDB until OTP verification succeeds.

**When to use:** Registration flow only.

**Why critical:** Creating the Player Profile before OTP verification would allow unverified phone numbers in the system. The pending state pattern ensures phone ownership is proven first.

**Redis key structure:**
```
Key:   memora:pending:{pending_id}
Value: JSON {
    "mobile": "966512345678",
    "password": "raw_password",    # Stored temporarily; hashed by Frappe on doc.insert()
    "display_name": "Ahmad",
    "gender": "Male",
    "grade": "GRD-00001",
    "plan": "PLAN-00001",
    "major": "MJR-00001",         # Optional
    "otp": "1111",
    "attempts": 0,
    "created_at": "2026-02-12T10:00:00"
}
TTL:   300 seconds (5 minutes)
```

**Phone reservation key:**
```
Key:   memora:phone_reserved:{mobile}
Value: "1"
TTL:   300 seconds (5 minutes)
```

### Pattern 3: 3-Step Password Reset with Temp Token

**What:** Password reset uses OTP verification to issue a short-lived temp token, which is then used to set the new password. This separates "prove identity" from "change password."

**When to use:** Password reset flow.

**Redis key structure:**
```
Step 1 (request OTP):
  Key:   memora:reset:{mobile}
  Value: JSON {"otp": "1111", "attempts": 0}
  TTL:   300 seconds (5 minutes)

Step 2 (verify OTP -> temp token):
  Key:   memora:reset_token:{token}
  Value: mobile number string
  TTL:   900 seconds (15 minutes per CONTEXT.md)
  (Also DELETE memora:reset:{mobile} after successful verification)

Step 3 (confirm new password):
  Validate temp token from Redis, call set_player_password via FrappeClient
  DELETE memora:reset_token:{token}
```

### Pattern 4: Reuse Existing Login Flow for Player Login

**What:** The new `/auth/player/login` endpoint follows the exact same flow as the current `/auth/login` but calls `verify_player_password` via FrappeClient instead of `FrappeAuthService.verify_credentials()`.

**When to use:** Player login endpoint.

**Current flow (to replicate):**
1. Require X-Device-ID header
2. Rate limit check (IP + account)
3. Verify credentials (now via FrappeClient -> `verify_player_password`)
4. Check plan exists in profile data
5. Get device limit from settings
6. Register device (atomic Lua script)
7. Fetch wallet XP
8. Create session (invalidates previous)
9. Create access + refresh tokens
10. Return enriched response (tokens + profile)

**Key change:** The FrappeClient returns `player_id` (docname like `PLAYER-00001`) directly, which becomes the JWT `sub`. No more Frappe User email resolution.

### Pattern 5: Admin Login Keeps Existing Frappe User Flow

**What:** The admin login endpoint uses the existing `FrappeAuthService.verify_credentials()` to authenticate against Frappe User. This is the only flow that still creates/destroys a Frappe session.

**When to use:** Admin login only (`POST /auth/admin/login`).

**Why keep it:** Admins use email+password with Frappe User. The migration only affects players. The `FrappeAuthService` class should be retained (not deleted) for admin use.

### Pattern 6: Registration Options Endpoint via Frappe API

**What:** A `GET /auth/registration-options` endpoint that returns available grades (with their majors), plans, seasons, and avatar options for the mobile client to populate picker UI.

**When to use:** Mobile app registration screen (before user fills in the form).

**Implementation:** Create a new Frappe whitelisted API (`memora_admin/api/auth.py:get_registration_options`) that fetches active data from the three DocTypes. Cache the response in Redis (5-min TTL) since these change infrequently.

**Data model (from DocType schemas verified):**
- Grades: `Memora Grade` -- `name` (GRD-#####), `grade_title`, `sort_order`, with child table `Memora Grade Major` linking to `Memora Major`
- Majors: `Memora Major` -- `name` (MJR-#####), `major_title`
- Plans: `Memora Academic Plan` -- `name` (PLAN-#####), `plan_name`, `grade`, `major`, `season`, `is_published`
- Seasons: `Memora Season` -- `name` (SEAS-#####), `season_title`, `is_published`
- Avatars: Fixed options from Player Profile schema: `pre`, `blonde`, `Caleb`, `Jad`, `Sadie`, `Valentina`

**Response shape recommendation:**
```json
{
    "grades": [
        {
            "name": "GRD-00001",
            "title": "الصف العاشر",
            "sort_order": 1,
            "majors": [
                {"name": "MJR-00001", "title": "علمي"},
                {"name": "MJR-00002", "title": "أدبي"}
            ]
        }
    ],
    "plans": [
        {
            "name": "PLAN-00001",
            "title": "خطة العاشر علمي",
            "grade": "GRD-00001",
            "major": "MJR-00001"
        }
    ]
}
```

> **Note:** Avatars and genders are hardcoded client-side and not returned by this endpoint.

**Client flow:** Client calls `GET /auth/registration-options` first, uses grade+major selection to filter available plans, then submits registration with the selected values.

### Anti-Patterns to Avoid

- **Storing raw password in Redis pending state longer than necessary:** The password is in the pending registration JSON for the 5-minute OTP window. This is acceptable because Redis is in-memory with TTL auto-expiry, and the pending state is deleted on verification or expiry. Do NOT store hashed passwords in pending state -- Frappe's DocType hooks handle hashing on `doc.insert()`.
- **Creating Player Profile before OTP verification:** The account must NOT exist until OTP is verified. Otherwise, unverified phone numbers enter the system.
- **Using `email` param in `create_access_token` for players:** Players do not have emails. The `email` parameter must become optional. Use a new `mobile` claim instead.
- **Returning different errors for "phone not found" vs "wrong password" in login:** CONTEXT.md mandates generic "Invalid credentials" for both cases. The `verify_player_password` Frappe API already implements this.
- **Hardcoding refresh token TTL:** CONTEXT.md specifies refresh token lifetime should be driven by `session_timeout_days` from Memora Settings (currently 30 days). Must fetch from SettingsService, not use config default.

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| Password verification | Custom hash comparison | `FrappeClient.call("memora_admin.api.auth.verify_player_password")` | Phase 30 built this. It handles mobile-to-docname resolution, PBKDF2-SHA256 verification, profile fetch, and XP fetch in one call. |
| Player registration | Direct MariaDB INSERT | `FrappeClient.call("memora_admin.api.auth.register_player")` | Phase 30 built this. It handles phone normalization, uniqueness check, DocType hooks (hashing, wallet creation), and Redis wallet seeding. |
| Password change | Direct `__Auth` table UPDATE | `FrappeClient.call("memora_admin.api.auth.set_player_password")` | Phase 30 built this. It handles hash update and session invalidation via Redis DEL. |
| Rate limiting | Custom counter logic | Existing `RateLimiter` class with Lua script | Already atomic, battle-tested. Just instantiate with different limits for OTP (3/phone/10min, 10/IP/10min). |
| Session management | Custom session logic | Existing `SessionService` | Already handles create, validate, invalidate. Works with any string user_id. |
| Device registration | Custom device logic | Existing `DeviceService` | Already handles atomic registration, fingerprint matching, limit enforcement. |
| JWT creation | New token functions | Existing `create_access_token`/`create_refresh_token` | Just add `mobile` param, make `email` optional. |
| Admin auth | New admin verification | Existing `FrappeAuthService.verify_credentials()` | Keep for admin login. Only player auth changes. |

**Key insight:** Phase 30 built the Frappe-side bridge. Phase 31 is purely about orchestrating FastAPI endpoints that call Phase 30's APIs and manage OTP state. The OTP service is the only genuinely new code; everything else is composition of existing services.

## Common Pitfalls

### Pitfall 1: Forgetting to Make `email` Optional in `create_access_token`

**What goes wrong:** `create_access_token()` currently requires `email: str` as a positional parameter. Player login has no email. Passing empty string works but is semantically wrong and pollutes JWTs with empty claims.

**Why it happens:** The function signature was designed for Frappe User-based auth where email is always available.

**How to avoid:** Update `create_access_token()` signature: make `email` optional (default `None`), add `mobile: str | None = None`. Include `mobile` claim in JWT for players, `email` claim for admins. This satisfies MIGR-01 (JWT `sub` = Player Profile docname) and MIGR-02 (`email` optional, `mobile` added).

**Warning signs:** Empty `email` field in player JWT tokens, or type errors when calling without email.

### Pitfall 2: Password in Pending Registration Redis Key

**What goes wrong:** The raw password is stored temporarily in Redis during the OTP verification window (up to 5 minutes). If Redis is compromised, passwords are exposed.

**Why it happens:** The registration flow collects all fields upfront (step 1) but only creates the account after OTP verification (step 2). The password must be stored somewhere between steps.

**How to avoid:** This is an acceptable tradeoff with mitigations:
1. Redis TTL ensures auto-deletion after 5 minutes
2. The pending key is deleted immediately on successful verification
3. Redis is on localhost (127.0.0.1:13000), not network-accessible
4. The alternative (collecting password only after OTP) would require a 3-step registration flow, adding UX friction

**Warning signs:** Pending registration keys persisting beyond 5 minutes (TTL not set properly).

### Pitfall 3: Race Condition on Phone Reservation

**What goes wrong:** Two simultaneous registration requests for the same phone number. Both pass the uniqueness check, both store pending state, both get OTP verified, and the second `register_player` call fails with "Phone already registered" from the MariaDB UNIQUE constraint.

**Why it happens:** The Redis reservation (`memora:phone_reserved:{mobile}`) and the MariaDB uniqueness check are not atomic across both stores.

**How to avoid:** Use Redis SETNX (SET if not exists) for the phone reservation. The reservation key is checked BEFORE storing the pending registration. If the key already exists, return "Phone number has a pending registration." The MariaDB UNIQUE constraint is the final safety net. This is a defense-in-depth approach.

```python
# Atomic reservation check
reserved = await redis.set(f"memora:phone_reserved:{mobile}", "1", ex=300, nx=True)
if not reserved:
    raise HTTPException(409, detail="Phone number has a pending registration")
```

**Warning signs:** Duplicate "PLAYER-00001" and "PLAYER-00002" with the same mobile number (should never happen due to UNIQUE constraint, but the error should be graceful).

### Pitfall 4: OTP Rate Limiter Using Wrong Key Prefix

**What goes wrong:** OTP rate limiting shares keys with login rate limiting, causing cross-contamination. A player who triggered login rate limits gets blocked from OTP requests, or vice versa.

**Why it happens:** The existing `RateLimiter` uses `memora:ratelimit:` prefix. If OTP reuses the same prefix and IP key pattern, counters overlap.

**How to avoid:** Use distinct key prefixes for OTP rate limiting:
- `memora:ratelimit:otp:phone:{mobile}` -- per-phone OTP send counter
- `memora:ratelimit:otp:ip:{ip}` -- per-IP OTP send counter
- `memora:ratelimit:otp:cooldown:{mobile}` -- 60s resend cooldown flag

The `RateLimiter` class can be instantiated with a different `key_prefix` for OTP vs login, or the OTP service can manage its own rate limit keys directly.

**Warning signs:** OTP requests returning 429 when the player has not exceeded OTP-specific limits.

### Pitfall 5: Not Fetching `session_timeout_days` from Memora Settings

**What goes wrong:** Refresh token lifetime hardcoded to `settings.jwt_refresh_token_expire_days` (30 days from `.env`). If admin changes `session_timeout_days` in Memora Settings, it has no effect.

**Why it happens:** The current login uses `settings.jwt_refresh_token_expire_days` from the FastAPI `Settings` class (populated from `.env`). CONTEXT.md specifies the value should come from Memora Settings DocType.

**How to avoid:** During login and registration (when creating sessions and refresh tokens), fetch `session_timeout_days` from `SettingsService` or add it to the `get_gamification_settings` API response. Use that value instead of the `.env` default.

**Implementation:** Either:
1. Add `session_timeout_days` to the existing `get_gamification_settings` Frappe API and `GamificationSettings` model
2. Or create a separate settings fetch

Option 1 is simpler and follows the existing pattern.

**Warning signs:** Changing `session_timeout_days` in Frappe Desk has no effect on new sessions.

### Pitfall 6: Forgetting Season Parameter in Registration

**What goes wrong:** The `register_player` Frappe API requires `season` as a parameter (it is `reqd: 1` on the DocType). But the CONTEXT.md registration flow lists required fields as `phone, password, display_name, gender, grade, plan` with `major` as optional. Season is not mentioned.

**Why it happens:** The CONTEXT.md discusses user-facing required fields. But the DocType has `season` as required, and the Frappe API needs it.

**How to avoid:** The registration options endpoint should include the current active season. The FastAPI registration endpoint should auto-populate the season from the active/published season (query `Memora Season` where `is_published=1`, take the latest). This is NOT a user-facing field -- the mobile app does not present a season picker. The server determines the current season.

**Warning signs:** Registration fails with `MandatoryError: season is required` from Frappe.

### Pitfall 7: Changing Access Token Lifetime Without Updating Config Default

**What goes wrong:** CONTEXT.md specifies access token lifetime should be 1 hour (`jwt_access_token_expire_minutes = 60`). The current default in `config.py` is 15 minutes. If not updated, all new tokens still have 15-minute lifetime.

**Why it happens:** The value comes from `.env` -> `Settings` -> `create_access_token`. If `.env` is not updated and the `config.py` default is not changed, the old value persists.

**How to avoid:** Update the default in `config.py` from `15` to `60`. Also update `.env` if needed. This is a simple config change but easy to forget.

**Warning signs:** Mobile app sessions expire after 15 minutes instead of 1 hour, causing excessive token refreshes.

## Code Examples

### Player Login Endpoint (Replacing Current `/auth/login`)

```python
# Source: Composed from existing auth.py patterns + Phase 30 Frappe API
@router.post("/player/login", response_model=PlayerLoginResponse)
async def player_login(
    request: Request,
    credentials: PlayerLoginRequest,
    redis: RedisClient,
    settings: SettingsDep,
) -> PlayerLoginResponse | JSONResponse:
    """Player login with phone + password."""
    # 1. Require X-Device-ID header
    device_id = request.headers.get("X-Device-ID")
    if not device_id:
        raise HTTPException(400, detail="X-Device-ID header required")

    # 2. Rate limit check
    client_ip = _get_client_ip(request)
    rate_limiter = RateLimiter(redis)
    allowed, retry_after, limit_type = await rate_limiter.check_rate_limit(
        ip_address=client_ip, target_account=credentials.mobile,
    )
    if not allowed:
        return JSONResponse(
            status_code=429,
            content={"detail": "Too many login attempts", "retry_after": retry_after},
            headers={"Retry-After": str(retry_after)},
        )

    # 3. Verify credentials via Frappe API (single call)
    frappe_client = await get_frappe_client()
    try:
        profile = await frappe_client.call(
            "memora_admin.api.auth.verify_player_password",
            {"mobile": credentials.mobile, "password": credentials.password},
        )
    except FrappeAPIError:
        raise HTTPException(401, detail="Invalid credentials")

    if not profile or not profile.get("plan"):
        raise HTTPException(401, detail="Invalid credentials")

    player_id = profile["player_id"]

    # 4. Fetch session_timeout_days from Memora Settings
    settings_service = SettingsService(redis, frappe_client)
    game_settings = await settings_service.get_gamification_settings()
    session_ttl_days = game_settings.session_timeout_days

    # 5. Device registration (existing pattern)
    device_service = DeviceService(redis, key_prefix=settings.redis_key_prefix)
    device_result = await device_service.register_device(
        user_id=player_id, device_id=device_id,
        user_agent=request.headers.get("User-Agent", "Unknown"),
        max_devices=game_settings.max_devices_per_player,
        platform_hint=request.headers.get("X-Platform"),
    )
    if not device_result.success:
        return JSONResponse(status_code=429, content={...})

    # 6. Create session
    session_service = SessionService(redis, key_prefix=f"{settings.redis_key_prefix}session:")
    family_id = await session_service.create_session(
        player_id, plan_id=profile["plan"], ttl_days=session_ttl_days,
    )

    # 7. Create tokens
    access_token = create_access_token(
        user_id=player_id, mobile=profile["mobile"],
        plan_id=profile["plan"], display_name=profile["display_name"],
        family_id=family_id,
        expires_delta=timedelta(minutes=settings.jwt_access_token_expire_minutes),
    )
    refresh_token = create_refresh_token(
        user_id=player_id, family_id=family_id,
        expires_delta=timedelta(days=session_ttl_days),
    )

    # 8. Return enriched response (no gender per CONTEXT.md)
    return PlayerLoginResponse(
        access_token=access_token, refresh_token=refresh_token,
        profile=LoginProfile(
            display_name=profile.get("display_name", ""),
            avatar=profile.get("avatar", "default_avatar"),
            xp=profile.get("xp", 0),
        ),
    )
```

### OTP Service Core Methods

```python
# Source: Composed from FEATURES.md patterns + existing codebase Redis patterns

class OTPService:
    async def create_pending_registration(
        self, mobile: str, password: str, display_name: str,
        gender: str, grade: str, plan: str, major: str | None,
        ip_address: str,
    ) -> tuple[str, str]:
        """Store pending registration + generate OTP. Returns (pending_id, otp)."""

        # Rate limit: per-phone and per-IP
        await self._check_otp_rate_limit(mobile, ip_address)

        # Phone reservation (SETNX for atomicity)
        reserved = await self.redis.set(
            f"{self.prefix}phone_reserved:{mobile}", "1",
            ex=self.OTP_TTL, nx=True,
        )
        if not reserved:
            raise HTTPException(409, detail="Phone number has a pending registration")

        # Also check MariaDB uniqueness (FrappeClient call or direct)
        # This is handled later by register_player, but catching early is better UX

        # Generate OTP (static "1111" via provider)
        otp = "1111"  # StaticOTPProvider always returns this
        await self.provider.send_otp(mobile, otp)

        # Store pending state
        pending_id = secrets.token_urlsafe(32)
        pending_data = json.dumps({
            "mobile": mobile, "password": password,
            "display_name": display_name, "gender": gender,
            "grade": grade, "plan": plan, "major": major,
            "otp": otp, "attempts": 0,
        })
        await self.redis.set(
            f"{self.prefix}pending:{pending_id}", pending_data, ex=self.OTP_TTL,
        )

        # Set cooldown for resend
        await self.redis.set(
            f"{self.prefix}ratelimit:otp:cooldown:{mobile}", "1", ex=self.COOLDOWN_TTL,
        )

        return pending_id, otp

    async def verify_registration_otp(self, pending_id: str, otp: str) -> dict:
        """Verify OTP for pending registration. Returns registration data on success."""
        key = f"{self.prefix}pending:{pending_id}"
        raw = await self.redis.get(key)
        if not raw:
            raise HTTPException(401, detail="OTP expired or invalid")

        data = json.loads(raw if isinstance(raw, str) else raw.decode())

        if data["attempts"] >= self.MAX_ATTEMPTS:
            await self.redis.delete(key)
            await self.redis.delete(f"{self.prefix}phone_reserved:{data['mobile']}")
            raise HTTPException(401, detail="Too many attempts. Please request a new OTP.")

        if data["otp"] != otp:
            data["attempts"] += 1
            remaining = self.MAX_ATTEMPTS - data["attempts"]
            await self.redis.set(key, json.dumps(data), keepttl=True)
            raise HTTPException(
                status_code=401,
                detail={"detail": "Invalid OTP", "remaining_attempts": remaining},
            )

        # OTP verified -- delete pending state and reservation
        await self.redis.delete(key)
        await self.redis.delete(f"{self.prefix}phone_reserved:{data['mobile']}")

        return data  # Contains all registration fields
```

### Password Reset 3-Step Flow

```python
# Step 1: Request OTP
@router.post("/player/password-reset/request")
async def password_reset_request(body: PasswordResetRequest, ...):
    # Rate limit, generate OTP, store in memora:reset:{mobile}
    # Return generic message (no user enumeration)
    return {"message": "If this number is registered, you will receive an OTP"}

# Step 2: Verify OTP -> temp token
@router.post("/player/password-reset/verify")
async def password_reset_verify(body: PasswordResetVerify, ...):
    # Verify OTP from memora:reset:{mobile}
    # Generate temp token via secrets.token_urlsafe(32)
    # Store in memora:reset_token:{token} -> mobile (15-min TTL)
    # Delete memora:reset:{mobile}
    return {"reset_token": token}

# Step 3: Confirm new password
@router.post("/player/password-reset/confirm")
async def password_reset_confirm(body: PasswordResetConfirm, ...):
    # Validate temp token from Redis
    # Call set_player_password via FrappeClient (handles hash + session invalidation)
    # Delete temp token from Redis
    return {"message": "Password reset successful. Please log in again."}
```

### Updated `create_access_token` Signature

```python
# Source: Modification to existing core/security.py
def create_access_token(
    user_id: str,
    plan_id: str,
    display_name: str,
    family_id: str,
    email: str | None = None,    # Optional -- only for admin tokens
    mobile: str | None = None,   # New -- for player tokens (MIGR-01)
    role: str | None = None,
    expires_delta: timedelta | None = None,
) -> str:
    payload: dict[str, Any] = {
        "sub": user_id,          # PLAYER-00001 for players, email for admins
        "plan": plan_id,
        "name": display_name,
        "fid": family_id,
        "type": "access",
        "iat": now,
        "exp": expire,
        "jti": str(uuid4()),
    }
    # Include mobile for player tokens (MIGR-01)
    if mobile:
        payload["mobile"] = mobile
    # Include email for admin tokens (backward compat)
    if email:
        payload["email"] = email
    # Include role for admin tokens
    if role:
        payload["role"] = role
```

### Registration Options Endpoint

```python
# Frappe-side API (memora_admin/api/auth.py -- add to existing file)
@frappe.whitelist(allow_guest=False)
def get_registration_options() -> dict:
    """Return available grades, plans, and seasons for registration.

    Called by FastAPI via FrappeClient. Provides data for mobile app pickers.
    Avatars and genders are hardcoded client-side.
    """
    # Get published seasons (should be exactly 1 active)
    seasons = frappe.get_all(
        "Memora Season",
        filters={"is_published": 1},
        fields=["name", "season_title"],
        order_by="season_seq DESC",
        limit=1,
    )

    # Get all grades with their majors (sorted by sort_order)
    grades = frappe.get_all(
        "Memora Grade",
        fields=["name", "grade_title", "sort_order"],
        order_by="sort_order ASC",
    )
    for grade in grades:
        grade["majors"] = frappe.get_all(
            "Memora Grade Major",
            filters={"parent": grade["name"]},
            fields=["major"],
        )

    # Get published plans
    plans = frappe.get_all(
        "Memora Academic Plan",
        filters={"is_published": 1},
        fields=["name", "plan_name", "grade", "major"],
    )

    return {
        "grades": grades,
        "plans": plans,
        "seasons": seasons,
    }
```

```python
# FastAPI-side endpoint
@router.get("/registration-options")
async def get_registration_options(
    redis: RedisClient,
    settings: SettingsDep,
) -> dict:
    """Return available options for registration form pickers.

    Cached in Redis for 5 minutes (changes infrequently).
    """
    cache_key = "memora:registration_options"
    cached = await redis.get(cache_key)
    if cached:
        return json.loads(cached if isinstance(cached, str) else cached.decode())

    frappe_client = await get_frappe_client()
    result = await frappe_client.call("memora_admin.api.auth.get_registration_options")

    if result:
        await redis.set(cache_key, json.dumps(result), ex=300)

    return result or {}
```

## State of the Art

| Old Approach (Current) | New Approach (This Phase) | When Changed | Impact |
|------------------------|---------------------------|--------------|--------|
| Single `/auth/login` for both players and admins | Separate `/auth/player/login` and `/auth/admin/login` | Phase 31 | Clean separation, different auth mechanisms per role |
| `FrappeAuthService.verify_credentials()` with Frappe session | `FrappeClient.call("verify_player_password")` -- no Frappe session | Phase 31 | Login drops from 4 HTTP calls to 1; no Frappe session overhead |
| JWT `sub` = email, `email` claim required | JWT `sub` = PLAYER-##### docname, `mobile` claim for players | Phase 31 (MIGR-01, MIGR-02) | Identity decoupled from phone number; email removed from player tokens |
| `jwt_access_token_expire_minutes = 15` | `jwt_access_token_expire_minutes = 60` | Phase 31 | Fewer token refreshes for mobile app |
| `jwt_refresh_token_expire_days` from `.env` (30) | `session_timeout_days` from Memora Settings DocType | Phase 31 | Admin-configurable session lifetime |
| No registration endpoint | Full 2-step OTP registration flow | Phase 31 | Players can self-register |
| No password reset endpoint | Full 3-step OTP password reset flow | Phase 31 | Players can recover accounts |

**Deprecated after this phase:**
- `FrappeAuthService.verify_credentials()` -- replaced by `FrappeClient.call("verify_player_password")` for players. Retained only for admin login.
- `FrappeAuthService.lookup_user_by_mobile()` -- no longer needed; `verify_player_password` handles mobile lookup internally.
- `is_email()` helper -- no longer needed; separate endpoints eliminate the need for identifier type detection.
- Single `/auth/login` endpoint -- replaced by `/auth/player/login` and `/auth/admin/login` (MIGR-07).

## Open Questions

1. **Password reset resend endpoint**
   - What we know: REG-05 specifies OTP resend for registration (`POST /auth/player/register/resend`). The FEATURES.md mentions `POST /auth/player/password-reset/resend` but the REQUIREMENTS.md does not list it as a requirement.
   - What's unclear: Is a separate resend endpoint needed for password reset, or can the player just call `/password-reset/request` again?
   - Recommendation: Include a password-reset resend capability. The player can call `/password-reset/request` again, which generates a new OTP and resets the attempt counter, subject to the same rate limits and 60-second cooldown. No separate endpoint needed -- the request endpoint is idempotent in this regard.

2. **Season auto-selection for registration**
   - What we know: CONTEXT.md lists `phone, password, display_name, gender, grade, plan` as required registration fields, with `major` optional. The DocType requires `season` (reqd: 1). The registration options endpoint returns available seasons.
   - What's unclear: Does the mobile app send `season` or does the server auto-populate it?
   - Recommendation: The server should auto-populate `season` with the current active/published season. The mobile client should NOT be required to send it. If the registration options response includes `seasons`, the server picks the first (most recent) published season. If no published season exists, registration fails with a clear error.

3. **`major` field -- required in DocType, optional in CONTEXT.md**
   - What we know: The DocType JSON has `major` with `reqd: 1`. CONTEXT.md says `major` is optional at registration.
   - What's unclear: Can the DocType schema be changed, or should the API enforce a default?
   - Recommendation: Keep the DocType schema as-is. When `major` is not provided in the registration request, the FastAPI endpoint should determine it from the selected plan (the `Memora Academic Plan` has a `major` field). If the plan does not have a major, the first major linked to the selected grade should be used. This satisfies both the "optional for user" and "required in DB" constraints.

4. **`LoginProfile` response shape -- `gender` removed per CONTEXT.md**
   - What we know: CONTEXT.md explicitly says "no `gender` -- dropped from login response." The current `LoginProfile` model has `gender: str | None`.
   - What's unclear: N/A -- the decision is clear.
   - Recommendation: Remove `gender` from the `LoginProfile` model used in the player login response. The field exists on the DocType and is returned by `verify_player_password`, but the FastAPI login response should not include it.

5. **Admin login response shape (Claude's Discretion)**
   - What we know: CONTEXT.md says admin login response shape is Claude's discretion.
   - Recommendation: Return `TokenResponse` (just tokens + token_type) for admin login. No enriched profile data. Admins use the Desk UI for profile info, not the mobile API. Include `role` in the JWT for admin authorization checks. This keeps admin login simple and avoids fetching unnecessary profile data.

## Sources

### Primary (HIGH confidence)
- `fastapi_app/api/v1/endpoints/auth.py` -- Current login/refresh endpoint patterns (the template for new endpoints)
- `fastapi_app/core/security.py` -- JWT creation functions (will be modified)
- `fastapi_app/models/auth.py` -- Current Pydantic models (will be modified)
- `fastapi_app/services/session.py` -- SessionService (unchanged, reused)
- `fastapi_app/services/device.py` -- DeviceService (unchanged, reused)
- `fastapi_app/services/rate_limit.py` -- RateLimiter Lua script pattern (reused for OTP)
- `fastapi_app/services/wallet.py` -- WalletService (unchanged, reused)
- `fastapi_app/services/frappe_client.py` -- FrappeClient (unchanged, reused)
- `fastapi_app/services/frappe.py` -- FrappeAuthService (retained for admin login only)
- `fastapi_app/services/settings.py` -- SettingsService (extended for session_timeout_days)
- `fastapi_app/core/config.py` -- Settings class (jwt_access_token_expire_minutes default changes)
- `fastapi_app/api/deps.py` -- Dependency injection patterns (reused)
- `memora_admin/api/auth.py` -- Phase 30 Frappe auth APIs (called via FrappeClient)
- `memora_admin/memora_admin/doctype/memora_player_profile/memora_player_profile.json` -- Player Profile schema (fields, required constraints)
- `memora_admin/memora_admin/doctype/memora_settings/memora_settings.json` -- session_timeout_days (default: 30)
- `memora_admin/memora_admin/doctype/memora_grade/memora_grade.json` -- Grade schema with majors child table
- `memora_admin/memora_admin/doctype/memora_major/memora_major.json` -- Major schema
- `memora_admin/memora_admin/doctype/memora_academic_plan/memora_academic_plan.json` -- Plan schema with grade, major, season
- `memora_admin/memora_admin/doctype/memora_season/memora_season.json` -- Season schema
- `.planning/research/FEATURES.md` -- Complete feature landscape with Redis key patterns, flow diagrams, error codes
- `.planning/research/STACK_mobile_auth.md` -- Stack decisions, OTP storage patterns, temp token design
- `.planning/phases/30-frappe-auth-api-bridge/30-RESEARCH.md` -- Phase 30 research (Frappe API patterns)
- `.planning/phases/30-frappe-auth-api-bridge/30-01-SUMMARY.md` -- Phase 30 completion, readiness for Phase 31

### Secondary (MEDIUM confidence)
- `.planning/REQUIREMENTS.md` -- Requirements mapping (AUTH-01 through MIGR-07)
- `.planning/ROADMAP.md` -- Phase 31 success criteria and dependencies

## Metadata

**Confidence breakdown:**
- Standard stack: HIGH -- all libraries already installed and in use in the codebase
- Architecture: HIGH -- patterns directly extracted from existing codebase (auth.py, rate_limit.py, session.py, device.py)
- OTP implementation: HIGH -- Redis key patterns verified from FEATURES.md research + existing RateLimiter Lua pattern
- Pitfalls: HIGH -- identified from actual code inspection (email param, season requirement, config default, rate limit key overlap)
- Code examples: HIGH -- composed from verified existing patterns, not hypothetical

**Research date:** 2026-02-12
**Valid until:** 2026-03-12 (stable -- building on established codebase patterns)
