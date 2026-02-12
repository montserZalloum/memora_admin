# Architecture Patterns: Mobile-First Player Authentication Migration

**Domain:** Phone+password auth on custom Frappe DocType, integrated with FastAPI sidecar
**Researched:** 2026-02-12
**Overall confidence:** HIGH (based on codebase audit + verified Frappe API signatures)

---

## Current Architecture (Before)

```
Mobile App
    |
    v
FastAPI (port 8002)
    |
    |-- POST /auth/login
    |       |
    |       v
    |   FrappeAuthService.verify_credentials()
    |       |-- POST frappe:8000/api/method/login  (creates Frappe session)
    |       |-- GET  frappe:8000/api/method/frappe.auth.get_logged_user
    |       |-- GET  frappe:8000/api/resource/User/{email}
    |       |-- GET  frappe:8000/api/resource/Memora Player Profile/{email}
    |       |-- GET  frappe:8000/api/method/logout  (destroys Frappe session)
    |       v
    |   JWT created (sub=email)
    |
    |-- All other endpoints use JWT (user.sub = email)
    |       |
    |       v
    |   Redis keys: memora:{type}:{email}
    |
    v
Frappe (port 8000)
    |-- Player Profile DocType (autoname: field:user, Link -> User)
    |-- Event handlers reference doc.user
    |-- Frappe APIs lookup by {"user": player_id}
```

**Problems with current flow:**
1. Creates/destroys a Frappe session per login (4 HTTP round-trips)
2. Requires Frappe User record per player (email mandatory)
3. Player identity = email (Arabic students often lack email)
4. `doc.user` coupling throughout event handlers

---

## Target Architecture (After)

```
Mobile App
    |
    v
FastAPI (port 8002)
    |
    |-- POST /auth/player/login          (NEW endpoint)
    |       |
    |       v
    |   PlayerAuthService.verify_player()
    |       |-- POST frappe:8000/api/method/memora_admin.api.auth.verify_player_password
    |       |       (allow_guest=True, single HTTP call, no Frappe session)
    |       v
    |   JWT created (sub=PLAYER-00001, mobile=966512345678)
    |
    |-- POST /auth/player/register       (NEW endpoint)
    |       |
    |       v
    |   POST frappe:8000/api/method/memora_admin.api.auth.register_player
    |       (allow_guest=True, creates Player Profile + wallet)
    |
    |-- POST /auth/admin/login           (EXISTING, renamed)
    |       |
    |       v
    |   FrappeAuthService.verify_credentials()  (unchanged Frappe User flow)
    |
    |-- All game endpoints: user.sub = "PLAYER-00001" (opaque string, same pattern)
    |       |
    |       v
    |   Redis keys: memora:{type}:PLAYER-00001
    |
    v
Frappe (port 8000)
    |-- Player Profile DocType (autoname: PLAYER-.#####., mobile unique, password field)
    |-- NEW: memora_admin/api/auth.py (whitelisted verify + register + reset)
    |-- Event handlers reference doc.name (= PLAYER-00001)
    |-- Frappe APIs lookup by doc.name directly (no {"user": id} indirection)
```

---

## Component Architecture

### New Components

| Component | Location | Purpose |
|-----------|----------|---------|
| `memora_admin/api/auth.py` | Frappe | Whitelisted APIs: verify_player_password, register_player, verify_otp, request_password_reset, reset_password |
| `fastapi_app/services/player_auth.py` | FastAPI | Replaces FrappeAuthService for player login, calls new Frappe API |
| `fastapi_app/services/otp.py` | FastAPI | OTP generation, Redis storage, verification |
| `fastapi_app/api/v1/endpoints/player_auth.py` | FastAPI | /auth/player/* endpoints (login, register, reset) |
| `fastapi_app/models/player_auth.py` | FastAPI | Request/response models for player auth |

### Modified Components

| Component | What Changes | Why |
|-----------|-------------|-----|
| `memora_player_profile.json` | Remove `user` field, add `mobile` + `password`, change autoname | Core schema change |
| `memora_player_profile.py` | Add `validate()` for phone normalization + password policy | Data integrity |
| `events/access_sync.py` | `player_doc.user` -> `player_doc.name` (lines 96, 128) | Identity field removed |
| `events/device_sync.py` | `doc.user` -> `doc.name` (line 45) | Identity field removed |
| `events/plan_change_sync.py` | `doc.user` -> `doc.name` (line 32) | Identity field removed |
| `events/profile_sync.py` | `doc.user` -> `doc.name` (lines 29, 33, 45, 50) | Identity field removed |
| `api/purchase.py` | `{"user": user_id}` -> direct name lookup (line 44) | No `user` field to query |
| `api/profile.py` | `{"user": ["in", player_ids]}` -> `{"name": ["in", player_ids]}` (line 46) | Identity is now doc.name |
| `api/profile.py` | `{"user": player_id}` -> direct name lookup (line 148) | No `user` field to query |
| `api/subscriptions.py` | Remove `{"user": player_id}` fallback lookup (lines 90-95, 133-137) | Simplification |
| `api/devices.py` | `profile.user` -> `profile.name` (lines 51, 127) | Identity field removed |
| `fastapi_app/core/security.py` | `email` param optional in `create_access_token()` | Players have no email |
| `fastapi_app/models/auth.py` | Update TokenPayload, add `mobile` field, `email` optional | New identity model |
| `fastapi_app/api/v1/endpoints/auth.py` | Rename to /auth/admin/login, strip player logic | Separation of concerns |

### Unchanged Components

| Component | Why Unchanged |
|-----------|--------------|
| All game endpoints (sessions, progress, wallet, etc.) | Use `user.sub` opaquely -- just a string |
| All FastAPI services (access, progress, wallet, etc.) | Accept player_id as string, identity-agnostic |
| Redis key structure | Same pattern `memora:{type}:{user.sub}`, value changes not structure |
| Session management (SessionService) | Uses `user_id` string, identity-agnostic |
| Device management (DeviceService) | Uses `user_id` string, identity-agnostic |
| Rate limiting (RateLimiter) | Uses IP + identifier string, identity-agnostic |
| Double-Gate access control | Uses `user.sub` from JWT, identity-agnostic |

---

## Data Flow Diagrams

### 1. Player Login Flow

```
Mobile App                    FastAPI (:8002)                    Frappe (:8000)
    |                              |                                  |
    |  POST /auth/player/login     |                                  |
    |  {mobile, password}          |                                  |
    |  X-Device-ID: uuid           |                                  |
    |----------------------------->|                                  |
    |                              |                                  |
    |                    Rate limit check (Redis)                     |
    |                    Normalize phone number                       |
    |                              |                                  |
    |                              |  POST /api/method/               |
    |                              |  memora_admin.api.auth.          |
    |                              |  verify_player_password          |
    |                              |  {mobile, password}              |
    |                              |--------------------------------->|
    |                              |                                  |
    |                              |                    check_password(
    |                              |                      player_name,
    |                              |                      password,
    |                              |                      doctype="Memora Player Profile",
    |                              |                      fieldname="password"
    |                              |                    )
    |                              |                    + fetch profile data
    |                              |                                  |
    |                              |  {player_id, display_name,       |
    |                              |   plan, avatar, gender, mobile}  |
    |                              |<---------------------------------|
    |                              |                                  |
    |                    Validate plan exists                          |
    |                    Register device (Redis Lua)                   |
    |                    Fetch wallet (Redis)                          |
    |                    Create session (Redis)                        |
    |                    Create JWT (sub=player_id)                    |
    |                              |                                  |
    |  {access_token, refresh_token,                                  |
    |   profile: {display_name,    |                                  |
    |             avatar, xp}}     |                                  |
    |<-----------------------------|                                  |
```

**Key design decisions:**
- Single HTTP call to Frappe (was 4 calls in current flow)
- `allow_guest=True` on Frappe API -- no Frappe session created or destroyed
- JWT `sub` = Player Profile docname (e.g., `PLAYER-00001`), not phone number
- Phone number stored in JWT `mobile` claim (for display, not as identity key)
- Login uses a separate guest httpx client, NOT `FrappeClient` (principle of least privilege)

**Frappe whitelisted API -- `verify_player_password`:**

```python
# memora_admin/api/auth.py

@frappe.whitelist(allow_guest=True)
def verify_player_password(mobile: str, password: str) -> dict:
    """Verify player password and return profile data.

    Uses frappe.utils.password.check_password() with custom doctype support.

    IMPORTANT: check_password()'s first param (`user`) maps to the document
    name in the __Auth table. Since autoname is PLAYER-.#####., we must
    first resolve mobile -> docname before calling check_password().

    Returns:
        {player_id, display_name, plan, avatar, gender, mobile}

    Raises:
        frappe.AuthenticationError on invalid credentials (generic message)
    """
    mobile = normalize_phone(mobile)

    # Resolve mobile -> docname
    player_name = frappe.db.get_value(
        "Memora Player Profile", {"mobile": mobile}, "name"
    )
    if not player_name:
        frappe.throw("Invalid credentials", frappe.AuthenticationError)

    # Verify password against __Auth table
    from frappe.utils.password import check_password
    try:
        check_password(
            player_name,              # document name in __Auth table
            password,
            doctype="Memora Player Profile",
            fieldname="password"
        )
    except frappe.AuthenticationError:
        frappe.throw("Invalid credentials", frappe.AuthenticationError)

    # Fetch profile data in same call
    profile = frappe.get_doc("Memora Player Profile", player_name)
    return {
        "player_id": profile.name,
        "display_name": profile.display_name,
        "plan": profile.plan,
        "avatar": profile.avatar,
        "gender": profile.gender,
        "mobile": profile.mobile,
    }
```

**Confidence:** HIGH -- `check_password()` signature verified from [frappe/utils/password.py](https://github.com/frappe/frappe/blob/develop/frappe/utils/password.py): `check_password(user, pwd, doctype="User", fieldname="password")`. The `user` parameter is the document `name` used to look up the `__Auth` table row, and the `doctype`/`fieldname` parameters allow custom DocType usage.

### 2. Player Registration Flow

```
Mobile App                    FastAPI (:8002)                    Frappe (:8000)
    |                              |                                  |
    |  POST /auth/player/          |                                  |
    |  request-otp                 |                                  |
    |  {mobile}                    |                                  |
    |----------------------------->|                                  |
    |                              |                                  |
    |                    Rate limit check                              |
    |                    Normalize phone                               |
    |                    Generate 6-digit OTP                          |
    |                    Store in Redis:                                |
    |                      memora:otp:register:{mobile}                |
    |                      TTL: 300s (5 min)                           |
    |                    Send OTP via SMS/WhatsApp                     |
    |                              |                                  |
    |  {message: "OTP sent"}       |                                  |
    |<-----------------------------|                                  |
    |                              |                                  |
    |  POST /auth/player/register  |                                  |
    |  {mobile, otp, password,     |                                  |
    |   display_name, plan,        |                                  |
    |   avatar, grade, major,      |                                  |
    |   season, gender}            |                                  |
    |  X-Device-ID: uuid           |                                  |
    |----------------------------->|                                  |
    |                              |                                  |
    |                    Verify OTP from Redis                         |
    |                    Delete OTP key (single-use)                   |
    |                              |                                  |
    |                              |  POST /api/method/               |
    |                              |  memora_admin.api.auth.          |
    |                              |  register_player                 |
    |                              |  {mobile, password, ...fields}   |
    |                              |--------------------------------->|
    |                              |                                  |
    |                              |                    Normalize phone
    |                              |                    Check mobile unique
    |                              |                    Validate password policy
    |                              |                    Create Player Profile
    |                              |                      (autoname generates
    |                              |                       PLAYER-00001)
    |                              |                    after_insert triggers:
    |                              |                      _create_player_wallet
    |                              |                                  |
    |                              |  {player_id: "PLAYER-00001",     |
    |                              |   display_name, plan, avatar,    |
    |                              |   gender, mobile}                |
    |                              |<---------------------------------|
    |                              |                                  |
    |                    Register device (Redis Lua)                   |
    |                    Fetch wallet (Redis)                          |
    |                    Create session (Redis)                        |
    |                    Create JWT (sub=PLAYER-00001)                 |
    |                              |                                  |
    |  {access_token, refresh_token,                                  |
    |   profile: {...}}            |                                  |
    |<-----------------------------|                                  |
```

**Registration Frappe API:**

```python
@frappe.whitelist(allow_guest=True)
def register_player(
    mobile: str,
    password: str,
    display_name: str,
    plan: str,
    avatar: str,
    grade: str,
    major: str,
    season: str,
    gender: str | None = None,
) -> dict:
    """Create new player profile with phone+password.

    Password is stored via Password fieldtype which auto-hashes
    to PBKDF2-SHA256/Argon2 in __Auth table.

    The after_insert hook on MemoraPlayerProfile auto-creates
    the player wallet.

    Returns profile data for immediate JWT creation.
    """
    mobile = normalize_phone(mobile)

    # Check uniqueness
    if frappe.db.exists("Memora Player Profile", {"mobile": mobile}):
        frappe.throw("Phone number already registered", frappe.DuplicateEntryError)

    doc = frappe.get_doc({
        "doctype": "Memora Player Profile",
        "mobile": mobile,
        "password": password,   # Auto-hashed by Password fieldtype
        "display_name": display_name,
        "plan": plan,
        "avatar": avatar,
        "grade": grade,
        "major": major,
        "season": season,
        "gender": gender,
    })
    doc.insert(ignore_permissions=True)

    return {
        "player_id": doc.name,   # PLAYER-00001
        "display_name": doc.display_name,
        "plan": doc.plan,
        "avatar": doc.avatar,
        "gender": doc.gender,
        "mobile": doc.mobile,
    }
```

### 3. Password Reset Flow (3-Step)

```
Mobile App                    FastAPI (:8002)                    Redis (:13000)        Frappe (:8000)
    |                              |                                  |                      |
    | STEP 1: Request OTP          |                                  |                      |
    | POST /auth/player/           |                                  |                      |
    | forgot-password              |                                  |                      |
    | {mobile}                     |                                  |                      |
    |----------------------------->|                                  |                      |
    |                              |                                  |                      |
    |                    Normalize phone                               |                      |
    |                    Verify mobile exists -------------------------|--------------------->|
    |                              |<-----------------------------------------------------------------|
    |                    Generate 6-digit OTP                          |                      |
    |                    SET memora:otp:reset:{mobile}  -------------->|                      |
    |                         value=OTP, TTL=300s                     |                      |
    |                    Send OTP via SMS                              |                      |
    |                              |                                  |                      |
    |  {message: "OTP sent"}       |                                  |                      |
    |<-----------------------------|                                  |                      |
    |                              |                                  |                      |
    | STEP 2: Verify OTP           |                                  |                      |
    | POST /auth/player/           |                                  |                      |
    | verify-reset-otp             |                                  |                      |
    | {mobile, otp}                |                                  |                      |
    |----------------------------->|                                  |                      |
    |                              |                                  |                      |
    |                    GET memora:otp:reset:{mobile}  -------------->|                      |
    |                              |<---------------------------------|                      |
    |                    Verify OTP matches                            |                      |
    |                    DEL memora:otp:reset:{mobile}  -------------->|                      |
    |                    Generate temp token (uuid4)                   |                      |
    |                    SET memora:reset_token:{mobile} ------------->|                      |
    |                         value=token, TTL=600s                   |                      |
    |                              |                                  |                      |
    |  {reset_token: "uuid..."}    |                                  |                      |
    |<-----------------------------|                                  |                      |
    |                              |                                  |                      |
    | STEP 3: Set New Password     |                                  |                      |
    | POST /auth/player/           |                                  |                      |
    | reset-password               |                                  |                      |
    | {mobile, reset_token,        |                                  |                      |
    |  new_password}               |                                  |                      |
    |----------------------------->|                                  |                      |
    |                              |                                  |                      |
    |                    GET memora:reset_token:{mobile} ------------->|                      |
    |                              |<---------------------------------|                      |
    |                    Verify token matches                          |                      |
    |                    DEL memora:reset_token:{mobile} ------------->|                      |
    |                              |                                  |                      |
    |                              |  POST /api/method/               |                      |
    |                              |  memora_admin.api.auth.          |                      |
    |                              |  set_player_password             |
    |                              |  {mobile, new_password}          |                      |
    |                              |------------------------------------------>|
    |                              |                                  |        |
    |                              |                                  |  set_encrypted_password(
    |                              |                                  |    "Memora Player Profile",
    |                              |                                  |    player_name,
    |                              |                                  |    new_password,
    |                              |                                  |    "password"
    |                              |                                  |  )
    |                              |                                  |        |
    |                              |                    Invalidate session:    |
    |                              |  DEL memora:session:{player_id}  |        |
    |                              |                                  |        |
    |  {message: "Password reset   |                                  |        |
    |   successful"}               |                                  |        |
    |<-----------------------------|                                  |        |
```

**Design rationale for 3-step flow:**
- Step 1 + Step 2 are separated so the OTP can be consumed (deleted) independently
- The temp token bridges Step 2 and Step 3 -- without it, the client would need to re-send the OTP, which was already consumed
- 10-minute TTL on reset_token gives the user time to choose a new password
- Session invalidation after password change forces re-login with new password

### 4. Admin Login Flow (Minimal Changes)

```
Admin (Frappe Desk)           FastAPI (:8002)                    Frappe (:8000)
    |                              |                                  |
    |  POST /auth/admin/login      |                                  |
    |  {identifier, password}      |                                  |
    |----------------------------->|                                  |
    |                              |                                  |
    |                    Rate limit check                              |
    |                    Detect email (has @)                          |
    |                              |                                  |
    |                              |  (Existing FrappeAuthService      |
    |                              |   flow -- 4 HTTP calls unchanged)|
    |                              |--------------------------------->|
    |                              |<---------------------------------|
    |                              |                                  |
    |                    Verify user has System Manager role           |
    |                    Create JWT (sub=email, role="System Manager") |
    |                              |                                  |
    |  {access_token, refresh_token}                                  |
    |<-----------------------------|                                  |
```

**Admin login is minimally affected.** The endpoint is renamed from `/auth/login` to `/auth/admin/login`, and the player-specific logic (phone lookup, profile fetch) is removed. The existing `FrappeAuthService.verify_credentials()` continues using Frappe session-based auth for System Manager users.

---

## Redis Key Patterns for Auth

### OTP Keys (New)

| Key Pattern | Value | TTL | Purpose |
|-------------|-------|-----|---------|
| `memora:otp:register:{mobile}` | 6-digit code (string) | 300s (5 min) | Registration OTP |
| `memora:otp:reset:{mobile}` | 6-digit code (string) | 300s (5 min) | Password reset OTP |
| `memora:otp:attempts:{mobile}` | Counter (int) | 3600s (1 hr) | OTP request rate limit (max 3/hr) |

**OTP Generation:**
```python
import secrets
otp = f"{secrets.randbelow(1000000):06d}"  # Zero-padded 6-digit
```

**OTP Verification (atomic, single-use via Lua):**
```python
OTP_VERIFY_SCRIPT = """
local stored = redis.call('GET', KEYS[1])
if stored == false then
    return 0  -- expired or not found
end
if stored == ARGV[1] then
    redis.call('DEL', KEYS[1])
    return 1  -- verified and consumed
end
return -1  -- wrong code
"""
```

### Temp Token Keys (New -- Password Reset)

| Key Pattern | Value | TTL | Purpose |
|-------------|-------|-----|---------|
| `memora:reset_token:{mobile}` | UUID string | 600s (10 min) | Temp token bridging OTP verification and password set |

### Existing Keys (Identity Value Changes Only)

All existing Redis keys use `user.sub` from JWT. After migration, `user.sub` changes from email to `PLAYER-00001`:

| Key Pattern | Before | After |
|-------------|--------|-------|
| `memora:session:{id}` | `memora:session:ahmed@x.com` | `memora:session:PLAYER-00001` |
| `memora:access:{id}` | `memora:access:ahmed@x.com` | `memora:access:PLAYER-00001` |
| `memora:progress:{id}:{subj}:v{ver}` | `memora:progress:ahmed@x.com:...` | `memora:progress:PLAYER-00001:...` |
| `memora:wallet:{id}` | `memora:wallet:ahmed@x.com` | `memora:wallet:PLAYER-00001` |
| `memora:devices:{id}` | `memora:devices:ahmed@x.com` | `memora:devices:PLAYER-00001` |
| `memora:profile:{id}` | `memora:profile:ahmed@x.com` | `memora:profile:PLAYER-00001` |
| `memora:pending:{id}` | `memora:pending:ahmed@x.com` | `memora:pending:PLAYER-00001` |

**No code changes needed** for these keys -- they are string concatenations with `user.sub`.

---

## JWT Token Changes

### Current Access Token

```json
{
  "sub": "ahmed@example.com",
  "email": "ahmed@example.com",
  "plan": "PLAN-00001",
  "name": "Ahmed",
  "fid": "uuid-family-id",
  "type": "access",
  "iat": 1707700000,
  "exp": 1707700900,
  "jti": "uuid"
}
```

### New Player Access Token

```json
{
  "sub": "PLAYER-00001",
  "mobile": "966512345678",
  "plan": "PLAN-00001",
  "name": "Ahmed",
  "fid": "uuid-family-id",
  "type": "access",
  "iat": 1707700000,
  "exp": 1707700900,
  "jti": "uuid"
}
```

### New Admin Access Token (unchanged except no mobile)

```json
{
  "sub": "admin@example.com",
  "email": "admin@example.com",
  "plan": "",
  "name": "Admin User",
  "fid": "uuid-family-id",
  "type": "access",
  "role": "System Manager",
  "iat": 1707700000,
  "exp": 1707700900,
  "jti": "uuid"
}
```

### Changes to `create_access_token()`

```python
def create_access_token(
    user_id: str,
    plan_id: str,
    display_name: str,
    family_id: str,
    email: str | None = None,       # Optional (admins only)
    mobile: str | None = None,      # Optional (players only)
    role: str | None = None,
    expires_delta: timedelta | None = None,
) -> str:
    payload = {
        "sub": user_id,
        "plan": plan_id,
        "name": display_name,
        "fid": family_id,
        "type": "access",
        "iat": now,
        "exp": expire,
        "jti": str(uuid4()),
    }
    if email:
        payload["email"] = email
    if mobile:
        payload["mobile"] = mobile
    if role:
        payload["role"] = role
    ...
```

### Changes to `TokenPayload`

```python
class TokenPayload(BaseModel):
    sub: str           # PLAYER-00001 or admin email
    fid: str
    type: str
    exp: int
    jti: str
    plan: str | None = None
    name: str | None = None
    email: str | None = None    # Admin only
    mobile: str | None = None   # Player only
    role: str | None = None     # Admin only
    iat: int | None = None
```

---

## DocType Schema Changes

### Player Profile DocType (Before -> After)

**Before:**
```json
{
  "autoname": "field:user",
  "fields": [
    {"fieldname": "user", "fieldtype": "Link", "options": "User", "unique": 1, "reqd": 1},
    {"fieldname": "display_name", ...},
    {"fieldname": "plan", ...},
    ...
  ],
  "search_fields": "display_name, user"
}
```

**After:**
```json
{
  "autoname": "PLAYER-.#####.",
  "fields": [
    {"fieldname": "mobile", "fieldtype": "Data", "unique": 1, "reqd": 1,
     "in_list_view": 1, "in_standard_filter": 1, "label": "Mobile"},
    {"fieldname": "password", "fieldtype": "Password", "reqd": 1, "label": "Password"},
    {"fieldname": "display_name", ...},
    {"fieldname": "plan", ...},
    ...
  ],
  "search_fields": "display_name, mobile"
}
```

**Autoname rationale:** `PLAYER-.#####.` generates stable, immutable docnames (`PLAYER-00001`, `PLAYER-00002`). If a player changes their phone number, only the `mobile` field updates -- no `rename_doc()` needed, no Redis key migration, no linked record updates. This is explicitly recommended by the PRD over `autoname: "field:mobile"`.

---

## Event Handler Migration Map

### access_sync.py (Lines 88-101, 122-138)

**Current:**
```python
def on_subscription_change(doc, method):
    player_id = doc.player
    if frappe.db.exists("Memora Player Profile", player_id):
        player_doc = frappe.get_doc("Memora Player Profile", player_id)
        user_id = player_doc.user       # <-- uses doc.user
    else:
        user_id = player_id
    redis_key = f"memora:access:{user_id}"
```

**After:**
```python
def on_subscription_change(doc, method):
    player_id = doc.player              # Already the PLAYER-00001 docname
    redis_key = f"memora:access:{player_id}"
```

**Simplification:** With `autoname: PLAYER-.#####.`, the `player` field in `Memora Player Subscription` directly links to the Player Profile docname. No lookup needed. The indirection (`player_doc.user`) was only necessary because `user` was the identity field used as Redis key but different from the docname. Now `doc.name` IS the Redis identity key.

### device_sync.py (Line 45)

**Current:**
```python
user_id = doc.user
devices_key = f"memora:devices:{user_id}"
session_key = f"memora:session:{user_id}"
```

**After:**
```python
user_id = doc.name    # PLAYER-00001 (the docname IS the identity)
devices_key = f"memora:devices:{user_id}"
session_key = f"memora:session:{user_id}"
```

### plan_change_sync.py (Line 32)

**Current:**
```python
session_key = f"memora:session:{doc.user}"
```

**After:**
```python
session_key = f"memora:session:{doc.name}"
```

### profile_sync.py (Lines 29, 33, 45, 50)

**Current:**
```python
redis_key = f"memora:profile:{doc.user}"
profile_data = {"player_id": doc.user, ...}
invalidation_msg = json.dumps({"type": "profile", "player_id": doc.user, ...})
```

**After:**
```python
redis_key = f"memora:profile:{doc.name}"
profile_data = {"player_id": doc.name, ...}
invalidation_msg = json.dumps({"type": "profile", "player_id": doc.name, ...})
```

---

## Frappe API Migration Map

### purchase.py (Line 44)

**Current:**
```python
player_id = frappe.get_value("Memora Player Profile", {"user": user_id}, "name")
```

**After:**
```python
# user_id from JWT is now PLAYER-00001 which IS the docname
player_id = user_id  # Direct -- no lookup needed
if not frappe.db.exists("Memora Player Profile", player_id):
    frappe.throw("Player profile not found", frappe.DoesNotExistError)
```

### profile.py - get_profiles_batch (Line 46)

**Current:**
```python
profiles = frappe.get_all(
    "Memora Player Profile",
    filters={"user": ["in", player_ids]},
    fields=["user", "display_name", "avatar"],
)
return [{"player_id": p.user, ...} for p in profiles]
```

**After:**
```python
profiles = frappe.get_all(
    "Memora Player Profile",
    filters={"name": ["in", player_ids]},
    fields=["name", "display_name", "avatar"],
)
return [{"player_id": p.name, ...} for p in profiles]
```

### profile.py - update_player_avatar (Line 148)

**Current:**
```python
profile_name = frappe.get_value("Memora Player Profile", {"user": player_id}, "name")
```

**After:**
```python
profile_name = player_id  # player_id IS the docname (PLAYER-00001)
if not frappe.db.exists("Memora Player Profile", profile_name):
    frappe.throw(...)
```

### subscriptions.py - get_player_access_keys (Lines 90-95)

**Current:** Falls back to `{"user": player_id}` lookup when direct match fails.
**After:** Direct match only -- `player_id` IS the docname. Remove fallback.

### subscriptions.py - get_player_progress (Lines 133-137)

Same simplification -- remove `{"user": player_id}` fallback.

### devices.py (Lines 51, 127)

**Current:**
```python
user_id = profile.user
```

**After:**
```python
user_id = profile.name  # PLAYER-00001
```

---

## Phone Number Normalization

### Rules

```
Input: "+962 512 345 678"  -> Stored: "962512345678"
Input: "0512345678"        -> Stored: "962512345678" (default country: Jordan)
Input: "962512345678"      -> Stored: "962512345678"
Input: "+966512345678"     -> Stored: "966512345678"
```

### Implementation Points

Normalization MUST happen at two locations (belt-and-suspenders):

1. **Frappe `validate()` hook** (source of truth, server-side enforcement):
```python
class MemoraPlayerProfile(Document):
    def validate(self):
        self.mobile = normalize_phone(self.mobile)
        if self.is_new():
            self._validate_password_policy()
```

2. **FastAPI request validation** (early normalization before Frappe call):
```python
class PlayerLoginRequest(BaseModel):
    mobile: str
    password: str

    @field_validator("mobile")
    @classmethod
    def normalize_mobile(cls, v: str) -> str:
        return normalize_phone(v)
```

### Shared Normalization Function

```python
import re

def normalize_phone(phone: str) -> str:
    """Strip to digits only, ensure country code prefix."""
    digits = re.sub(r"[^\d]", "", phone)

    # Handle local format: 05XXXXXXXX or 07XXXXXXXX
    if digits.startswith("0") and len(digits) == 10:
        digits = "962" + digits[1:]  # Default country: Jordan

    if not (10 <= len(digits) <= 15):
        raise ValueError("Invalid phone number length")

    return digits
```

**CRITICAL:** The same normalization logic must exist in both FastAPI and Frappe. They are separate processes -- FastAPI cannot import Frappe modules. Either duplicate the function or extract into a shared pure-Python utility module importable by both.

---

## Dependency Chain and Build Order

```
Phase 1: DocType Schema        Phase 2: Frappe Auth API       Phase 3: FastAPI Auth Endpoints
+-----------------------+      +-------------------------+     +---------------------------+
| 1a. Add mobile field  |      | 2a. verify_player_      |     | 3a. PlayerAuthService     |
| 1b. Add password field|----->|     password() API       |---->| 3b. OTP service           |
| 1c. Change autoname   |      | 2b. register_player()   |     | 3c. /auth/player/* routes |
| 1d. validate() hook   |      |     API                  |     | 3d. JWT changes           |
| 1e. Phone normalizer  |      | 2c. set_player_password()|     | 3e. Auth models           |
+-----------------------+      |     API                  |     +---------------------------+
                               +-------------------------+              |
                                                                        |
Phase 4: Event Handler + API Migration     Phase 5: Cleanup             |
+------------------------------+          +--------------------------+  |
| 4a. access_sync.py           |          | 5a. Remove user field    |  |
| 4b. device_sync.py           |          | 5b. Data migration script|  |
| 4c. plan_change_sync.py      |<---------| 5c. Rename /auth/login   |<-+
| 4d. profile_sync.py          |          |     to /auth/admin/login |
| 4e. Frappe API updates       |          | 5d. Remove FrappeAuthSvc |
|     (purchase, profile,      |          |     player methods       |
|      subscriptions, devices) |          +--------------------------+
+------------------------------+
```

### Recommended Phase Ordering with Rationale

**Phase 1: DocType Foundation** (must be first -- everything depends on it)
- Add `mobile` (unique) and `password` fields to Player Profile
- Change `autoname` to `PLAYER-.#####.`
- **Keep `user` field temporarily** (nullable, not required) for backward compat
- Add `validate()` hook with phone normalization
- Write data migration script (populate mobile from existing User records)
- **Why first:** Schema is the foundation. Frappe auth API cannot be written without the password field existing on the DocType.

**Phase 2: Frappe Auth API** (depends on Phase 1)
- Create `memora_admin/api/auth.py` with three methods:
  - `verify_player_password(mobile, password)` -- `allow_guest=True`
  - `register_player(mobile, password, ...)` -- `allow_guest=True`
  - `set_player_password(mobile, new_password)` -- `allow_guest=False`
- Test independently via curl against Frappe (no FastAPI needed)
- **Why second:** These APIs are the bridge. FastAPI endpoints cannot be built without them.

**Phase 3: FastAPI Auth Endpoints** (depends on Phase 2)
- Create `PlayerAuthService` (calls new Frappe API via httpx)
- Create OTP service (Redis-backed, Lua-verified)
- Create `/auth/player/login`, `/auth/player/register`
- Create `/auth/player/forgot-password`, `/auth/player/verify-reset-otp`, `/auth/player/reset-password`
- Update JWT `create_access_token()` and `TokenPayload`
- **Why third:** Cannot build without the Frappe APIs from Phase 2.

**Phase 4: Event Handler + API Migration** (can partially overlap with Phase 3)
- Update all `doc.user` -> `doc.name` in 4 event handlers
- Update all `{"user": player_id}` -> direct name lookups in 4 Frappe APIs
- Remove `{"user": ...}` fallback lookups in subscriptions.py
- **Why fourth:** These changes are safe to make once the new autoname is in place (Phase 1). Could start after Phase 1, but grouping here avoids partial migration state.

**Phase 5: Cleanup** (depends on Phases 3 + 4 being fully tested)
- Remove `user` field from DocType schema entirely
- Rename `/auth/login` to `/auth/admin/login`
- Remove `lookup_user_by_mobile()` and player methods from old `FrappeAuthService`
- Run data migration: migrate existing email-keyed Redis data to new PLAYER-##### keys
- Delete orphaned Frappe User records for players
- **Why last:** Irreversible changes. Must only happen after full validation.

---

## Integration Points Summary

| Integration Point | Current | New | Mechanism |
|-------------------|---------|-----|-----------|
| Login authentication | Frappe session (4 HTTP calls) | Frappe whitelisted API (1 HTTP call) | `check_password()` with custom doctype |
| Registration | Manual (admin creates User + Profile) | FastAPI -> Frappe API | `allow_guest=True` whitelisted method |
| Password reset | Frappe User built-in | Custom 3-step OTP flow | Redis temp tokens + `set_encrypted_password()` |
| OTP storage | N/A | Redis with TTL | `memora:otp:{type}:{mobile}` keys |
| Player identity in JWT | Email (`sub` claim) | PLAYER-00001 (`sub` claim) | Autoname-generated docname |
| Event handler identity | `doc.user` (Link -> User) | `doc.name` (docname) | Direct field access, simpler |
| Frappe API player lookup | `{"user": player_id}` query | Direct docname access | `player_id` IS the docname |
| Device sync | `profile.user` for Redis keys | `profile.name` for Redis keys | Field reference change |
| Admin auth | Frappe User session | Unchanged | Separate endpoint |

---

## Anti-Patterns to Avoid

### 1. Phone Number as Docname

**Do NOT** use `autoname: "field:mobile"`. If a player changes their phone number:
- Frappe `rename_doc()` must cascade to ALL linked records (subscriptions, progress, wallet, transactions)
- ALL Redis keys must be migrated atomically (impossible without downtime)
- Active JWT tokens become invalid (different `sub` claim)
- Leaderboard entries become orphaned

**Use** `autoname: "PLAYER-.#####."` with a separate unique `mobile` field.

### 2. Importing Frappe in FastAPI

**Do NOT** try to call `frappe.utils.password.check_password()` directly from FastAPI. They are separate processes with separate Python environments. Always use HTTP calls to whitelisted methods.

### 3. FrappeClient for Login Endpoint

**Do NOT** use the existing `FrappeClient` (token-authenticated with API key/secret) for the login verification call. The login API is `allow_guest=True` and should use a separate `httpx.AsyncClient` without admin auth headers. Using `FrappeClient` for login means password verification carries admin-level permissions unnecessarily.

**Do use** `FrappeClient` for `register_player()` and `set_player_password()` since those need write permissions via the API token.

### 4. Storing OTP in MariaDB

**Do NOT** store OTPs in MariaDB. They are ephemeral (5-min TTL), high-write, and need automatic expiry. Redis with TTL is the correct and only appropriate store.

### 5. Removing `user` Field Before Full Migration

**Do NOT** remove the `user` field from the DocType before all event handlers, APIs, and data migration are complete. Keep it during transition (nullable, not required), then remove in the cleanup phase. Premature removal will break all existing event handlers and API lookups.

### 6. Using Phone Number as JWT `sub`

**Do NOT** put the raw phone number in JWT `sub`. Use the stable docname (`PLAYER-00001`) as `sub`. Phone number goes in a separate `mobile` claim. This ensures that if a player changes their phone number, their session/access/progress Redis keys (all keyed on `sub`) remain valid.

---

## Scalability Considerations

| Concern | At Current Scale | At 10K Players | At 100K Players |
|---------|-----------------|----------------|-----------------|
| Login latency | ~50ms (1 Frappe API call) | Same | Same -- stateless |
| OTP Redis keys | Negligible | ~1K keys max (5-min TTL) | ~10K keys max |
| Registration burst | N/A | Rate limit per IP + mobile | Rate limit + queue |
| Password reset abuse | Rate limit per mobile (3/hr) | Same | Same + CAPTCHA consideration |
| Phone normalization | In-process, O(1) | Same | Same |
| __Auth table size | Grows with players | Index on (doctype, name) | Same -- Frappe maintains index |

---

## Sources

- [frappe/utils/password.py](https://github.com/frappe/frappe/blob/develop/frappe/utils/password.py) -- `check_password(user, pwd, doctype="User", fieldname="password")` signature verified. The `user` parameter maps to document `name` in `__Auth` table.
- [Frappe Field Types Documentation](https://docs.frappe.io/framework/user/en/basics/doctypes/fieldtypes) -- Password fieldtype stores hashed values in separate `__Auth` table, never in doctype table.
- Codebase audit of all files referencing `doc.user` or `{"user": ...}`:
  - `fastapi_app/services/frappe.py` (lines 96, 104, 111, 148-175)
  - `fastapi_app/api/v1/endpoints/auth.py` (full login flow)
  - `fastapi_app/core/security.py` (JWT creation)
  - `memora_admin/events/access_sync.py` (lines 96, 128)
  - `memora_admin/events/device_sync.py` (line 45)
  - `memora_admin/events/plan_change_sync.py` (line 32)
  - `memora_admin/events/profile_sync.py` (lines 29, 33, 45, 50)
  - `memora_admin/api/purchase.py` (line 44)
  - `memora_admin/api/profile.py` (lines 46, 148)
  - `memora_admin/api/subscriptions.py` (lines 92, 135)
  - `memora_admin/api/devices.py` (lines 51, 127)
- PRD: `.planning/prd/mobile-auth-migration.md`

---

*Architecture research completed: 2026-02-12*
*Confidence: HIGH -- Based on full codebase audit of 15+ files + verified Frappe password API signatures*
