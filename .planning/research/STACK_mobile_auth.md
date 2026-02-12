# Technology Stack: Mobile-First Player Authentication Migration

**Project:** Memora Admin - Player Auth Migration
**Researched:** 2026-02-12
**Mode:** Focused stack research for phone+password auth on custom Frappe DocType
**Overall Confidence:** HIGH (verified with live Frappe v15 source code and runtime tests)

---

## Executive Summary

The migration from Frappe User-based auth to phone+password on Memora Player Profile requires **zero new dependencies**. Frappe's `__Auth` table and `frappe.utils.password` module already support password hashing on arbitrary DocTypes via the `doctype` parameter. The key insight (verified in source code) is that the default Password **fieldtype** uses Fernet encryption (reversible), NOT passlib hashing -- so we must bypass the fieldtype mechanism and call `update_password()` / `check_password()` directly.

OTP storage and temp tokens for password reset should use Redis (already the established pattern for ephemeral data in this codebase), not a new DocType.

---

## Critical Finding: Password Fieldtype vs Password Hashing

**Confidence: HIGH** -- Verified by reading Frappe v15 source code at `/home/corex/aurevia-bench/apps/frappe/frappe/utils/password.py` and `/home/corex/aurevia-bench/apps/frappe/frappe/model/base_document.py`, lines 1126-1157, and confirmed with a live runtime test against `x.conanacademy.com`.

### The Two Password Storage Mechanisms in `__Auth`

Frappe's `__Auth` table stores ALL password-like data but with two fundamentally different approaches:

| Mechanism | Storage | `encrypted` flag | Use Case | Reversible? |
|-----------|---------|-------------------|----------|-------------|
| `set_encrypted_password()` | Fernet encryption | `1` | API keys, secrets, default Password fieldtype | Yes (decryptable) |
| `update_password()` | passlib hash (PBKDF2-SHA256/Argon2) | `0` | User login passwords | No (one-way hash) |

**What `check_password()` queries:**
```sql
SELECT name, password FROM `__Auth`
WHERE doctype = %s AND name = %s AND fieldname = %s AND encrypted = 0
```

It only matches `encrypted=0` rows (hashed passwords). It will NEVER find passwords stored by the default Password fieldtype (`encrypted=1`).

### The Trap: DO NOT Use Password Fieldtype for Login Auth

The PRD mentions adding a `password` field with `"fieldtype": "Password"` to Player Profile. If done naively, Frappe's `_save_passwords()` method (called automatically on `doc.save()`) will store the password via `set_encrypted_password()` with `encrypted=1`. This password would be:
1. Reversibly encrypted (less secure than hashing)
2. Invisible to `check_password()` (which queries `encrypted=0`)

### The Correct Approach

Mimic what Frappe's User DocType does:

1. **Add the field** as `"fieldtype": "Password"` (so the UI shows a password input with masking)
2. **Intercept before save** using `flags.ignore_save_passwords = ["password"]` (prevents `_save_passwords()` from storing it as encrypted)
3. **Hash manually** via `frappe.utils.password.update_password(player_name, raw_password, doctype="Memora Player Profile", fieldname="password")`
4. **Verify** via `frappe.utils.password.check_password(player_name, raw_password, doctype="Memora Player Profile", fieldname="password")`

This is exactly how Frappe's User DocType handles `new_password` -- see `user.py` line 144: `self.flags.ignore_save_passwords = ["new_password"]`.

### Live Verification

The following was tested against the production database and confirmed working:

```python
from frappe.utils.password import update_password, check_password

# Store hashed password for custom DocType
update_password("TEST_PLAYER", "TestPass123!", doctype="Memora Player Profile", fieldname="password")

# Verify: stored as hashed (encrypted=0) with $pbkdf2-sha256$ prefix
# check_password returns the document name on success, raises AuthenticationError on failure
verified = check_password("TEST_PLAYER", "TestPass123!", doctype="Memora Player Profile", fieldname="password")
# Result: "TEST_PLAYER" -- SUCCESS
```

**`__Auth` table entry produced:**

| doctype | name | fieldname | encrypted | password (prefix) |
|---------|------|-----------|-----------|-------------------|
| Memora Player Profile | TEST_PLAYER | password | 0 | `$pbkdf2-sha256$29000$...` |

---

## `__Auth` Table Structure

**Confidence: HIGH** -- Verified from live MariaDB `DESCRIBE` and Frappe's `framework_mariadb.sql`.

```sql
CREATE TABLE `__Auth` (
    `doctype`   VARCHAR(140) NOT NULL,
    `name`      VARCHAR(255) NOT NULL,
    `fieldname` VARCHAR(140) NOT NULL,
    `password`  TEXT NOT NULL,
    `encrypted` INT(1) NOT NULL DEFAULT 0,
    PRIMARY KEY (`doctype`, `name`, `fieldname`)
) ENGINE=InnoDB ROW_FORMAT=DYNAMIC;
```

**Key implications:**
- Primary key is `(doctype, name, fieldname)` -- one password per doctype+name+fieldname combination
- `update_password()` uses INSERT...ON DUPLICATE KEY UPDATE -- safe for both create and update
- `name` column is VARCHAR(255) -- supports `PLAYER-00001` format docnames
- `check_password()` is case-insensitive on `name` matching (returns the canonical case from DB)

**Existing data pattern** (verified on production):
```
doctype=User, name=Administrator, fieldname=password, encrypted=0  -- hashed login password
doctype=User, name=Administrator, fieldname=api_secret, encrypted=1 -- encrypted API secret
doctype=Email Account, name=xxx, fieldname=password, encrypted=1   -- encrypted credential
```

---

## Recommended Stack: No New Dependencies

### What Already Exists (DO NOT Add)

| Need | Already Available | Where |
|------|-------------------|-------|
| Password hashing | `passlib 1.7.4` (PBKDF2-SHA256) | Frappe bench env, `frappe.utils.password` |
| Password verification | `check_password(user, pwd, doctype, fieldname)` | `frappe.utils.password` |
| Password update | `update_password(user, pwd, doctype, fieldname)` | `frappe.utils.password` |
| JWT token creation | `PyJWT 2.3.0` | Already in FastAPI sidecar |
| HTTP client (FastAPI->Frappe) | `httpx` | Already in requirements.txt |
| Redis async client | `redis>=5.0.0` | Already in requirements.txt |
| Secure random tokens | `secrets` (stdlib) | Python 3.10+ stdlib |
| Phone validation | `re` (stdlib) | Python 3.10+ stdlib |
| Rate limiting | `RateLimiter` class | `fastapi_app/services/rate_limit.py` |

### What NOT to Add (and Why)

| Library | Why Not |
|---------|---------|
| `python-phonenumbers` | Overkill. Target audience is Saudi Arabia only. A simple regex `^\d{9,15}$` after stripping `+` and leading zeros is sufficient. No international formatting needed. |
| `pyotp` / `twilio` | OTP is a static stub `"1111"` for now. No TOTP/HOTP needed. Future SMS/WhatsApp integration will use a provider SDK, not pyotp. |
| `bcrypt` / `argon2-cffi` | `passlib` already handles hashing with PBKDF2-SHA256 and auto-upgrades to Argon2 when available. Frappe manages this. |
| `itsdangerous` | Temp tokens for password reset can use `secrets.token_urlsafe()` + Redis TTL. No need for signed tokens -- Redis is the source of truth. |
| `pydantic[email]` | Players use phone numbers, not emails. Already have `email-validator` for admin use. |

---

## Component-Level Stack Decisions

### 1. Frappe Whitelisted API for Password Operations

**File:** `memora_admin/api/auth.py` (NEW)

**Functions needed:**

```python
@frappe.whitelist(allow_guest=True, methods=["POST"])
def verify_player_password(mobile: str, password: str) -> dict:
    """Verify player password. Called by FastAPI login endpoint.

    Uses check_password() with doctype="Memora Player Profile".
    Returns player profile data on success, raises AuthenticationError on failure.

    allow_guest=True because FastAPI calls this without a Frappe session.
    """

@frappe.whitelist(allow_guest=True, methods=["POST"])
def register_player(mobile: str, password: str, display_name: str,
                    plan: str, avatar: str, grade: str, major: str, season: str) -> dict:
    """Create new Player Profile with hashed password.

    1. Create Player Profile document (autoname: PLAYER-.#####.)
    2. Hash password via update_password()
    3. Return profile data

    allow_guest=True because new players have no existing auth.
    """

@frappe.whitelist(allow_guest=True, methods=["POST"])
def reset_player_password(mobile: str, new_password: str, temp_token: str) -> dict:
    """Set new password after OTP verification.

    Validates temp_token (FastAPI will have verified OTP and issued temp token).
    Calls update_password() to store new hashed password.
    """
```

**Why `allow_guest=True`:** FastAPI calls these APIs as an HTTP client without a Frappe session. The `allow_guest=True` flag lets unauthenticated HTTP requests reach the function. Security is enforced by:
- Password verification (login)
- OTP verification + temp token (registration, reset)
- Rate limiting (FastAPI-side, already built)

**Frappe imports needed:**
```python
import frappe
from frappe.utils.password import check_password, update_password
```

### 2. Player Profile DocType Modifications

**File:** `memora_player_profile.json` changes:

```json
{
  "autoname": "PLAYER-.#####.",
  "fields": [
    {
      "fieldname": "mobile",
      "fieldtype": "Data",
      "label": "Mobile",
      "unique": 1,
      "reqd": 1,
      "in_list_view": 1,
      "in_standard_filter": 1
    },
    {
      "fieldname": "password",
      "fieldtype": "Password",
      "label": "Password",
      "hidden": 1
    }
  ]
}
```

**File:** `memora_player_profile.py` changes:

```python
class MemoraPlayerProfile(Document):
    __new_password = None

    def __setup__(self):
        # CRITICAL: Prevent _save_passwords() from storing as encrypted
        # This mimics User DocType behavior (user.py:144)
        self.flags.ignore_save_passwords = ["password"]

    def validate(self):
        # Capture raw password before clearing
        if self.password and not self.is_dummy_password(self.password):
            self.__new_password = self.password
            self.password = ""

        # Normalize mobile number
        if self.mobile:
            self.mobile = self._normalize_mobile(self.mobile)

    def after_insert(self):
        self._hash_password()
        self._create_player_wallet()

    def on_update(self):
        self._hash_password()

    def _hash_password(self):
        if self.__new_password:
            from frappe.utils.password import update_password
            update_password(self.name, self.__new_password,
                          doctype="Memora Player Profile", fieldname="password")
            self.__new_password = None

    @staticmethod
    def _normalize_mobile(mobile: str) -> str:
        """Strip non-digits. No country code logic needed (handled by client)."""
        return "".join(c for c in mobile if c.isdigit())
```

### 3. OTP Storage: Redis (NOT a DocType)

**Confidence: HIGH** -- Based on existing codebase patterns.

**Decision:** Store OTP codes in Redis with auto-expiry.

**Why Redis, not a DocType:**
- OTPs are ephemeral (5-minute TTL) -- MariaDB is overkill for throwaway data
- Redis `SET ... EX` is the established pattern in this codebase (rate limiter, idempotency keys, session data)
- No need for queryability, reporting, or persistence beyond TTL
- Atomic operations prevent race conditions

**Redis key pattern:**
```
memora:otp:{mobile}  ->  {code}  (TTL: 300 seconds = 5 minutes)
```

**Implementation (FastAPI service):**
```python
class OTPService:
    OTP_TTL = 300  # 5 minutes
    OTP_KEY_PREFIX = "memora:otp:"

    def __init__(self, redis: redis.asyncio.Redis):
        self.redis = redis

    async def generate_otp(self, mobile: str) -> str:
        """Generate and store OTP. Returns the code."""
        # Static stub for now; pluggable for future SMS/WhatsApp
        code = "1111"
        key = f"{self.OTP_KEY_PREFIX}{mobile}"
        await self.redis.set(key, code, ex=self.OTP_TTL)
        return code

    async def verify_otp(self, mobile: str, code: str) -> bool:
        """Verify OTP code. Deletes on success (one-time use)."""
        key = f"{self.OTP_KEY_PREFIX}{mobile}"
        stored = await self.redis.get(key)
        if stored and stored.decode() == code:
            await self.redis.delete(key)  # One-time use
            return True
        return False
```

**Rate limiting on OTP requests:** Reuse existing `RateLimiter` with a separate key prefix (`memora:ratelimit:otp:`) to prevent OTP spam. Recommended: 3 OTP requests per mobile per 5 minutes.

### 4. Temp Token for 3-Step Password Reset

**Confidence: HIGH** -- Standard pattern, uses Python stdlib + existing Redis.

**Flow:**
```
Step 1: POST /auth/forgot-password {mobile}
        -> Generate OTP, store in Redis, return success

Step 2: POST /auth/verify-reset-otp {mobile, otp_code}
        -> Verify OTP, generate temp_token, store in Redis
        -> Return {temp_token} (single-use, 10-min TTL)

Step 3: POST /auth/reset-password {mobile, new_password, temp_token}
        -> Verify temp_token from Redis, call Frappe update_password()
        -> Delete temp_token, return success
```

**Redis key pattern:**
```
memora:reset_token:{token_value}  ->  {mobile}  (TTL: 600 seconds = 10 minutes)
```

**Token generation:**
```python
import secrets

def generate_temp_token() -> str:
    """Generate cryptographically secure URL-safe token."""
    return secrets.token_urlsafe(32)  # 256 bits of entropy
```

**Why NOT use JWT for temp tokens:**
- Temp tokens are single-use and must be invalidatable server-side
- JWT is stateless -- cannot be revoked without a blocklist (complexity for no gain)
- Redis key with TTL gives us: auto-expiry, single-use (delete after verify), server-side validation
- Simpler, more secure for this use case

**Why NOT use Frappe's `reset_password_key` pattern:**
- That's tied to Frappe User and email-based reset flow
- Uses `frappe.generate_hash()` stored on the User document
- We don't have a Frappe User for players
- Redis TTL is cleaner than a DB column for ephemeral tokens

### 5. Phone Number Validation

**Confidence: HIGH** -- Simple regex, no library needed.

**Decision:** Use a simple regex, NOT `python-phonenumbers`.

**Rationale:**
- All players are in the Saudi Arabia / Jordan / Middle East region
- Client app handles country code selection and formatting
- Server receives digits-only (e.g., `966512345678`)
- We need: is this 9-15 digits? That's it.

**Implementation:**
```python
import re

MOBILE_PATTERN = re.compile(r"^\d{9,15}$")

def validate_mobile(mobile: str) -> str:
    """Validate and normalize mobile number.

    Strips +, spaces, dashes. Validates digit count.
    Returns digits-only string.

    Raises ValueError if invalid.
    """
    # Strip common formatting
    cleaned = re.sub(r"[\s\-\+\(\)]", "", mobile)

    if not MOBILE_PATTERN.match(cleaned):
        raise ValueError("Mobile number must be 9-15 digits")

    return cleaned
```

**Where to enforce:**
1. `MemoraPlayerProfile.validate()` -- Frappe side (on save)
2. FastAPI Pydantic model -- request validation (on API call)
3. Both locations for defense in depth

---

## Frappe API Function Reference

### Functions to Use

| Function | Import | Signature | Purpose |
|----------|--------|-----------|---------|
| `check_password` | `frappe.utils.password` | `(user, pwd, doctype="User", fieldname="password", delete_tracker_cache=True)` | Verify password hash. Returns `name` on success, raises `AuthenticationError` on failure. |
| `update_password` | `frappe.utils.password` | `(user, pwd, doctype="User", fieldname="password", logout_all_sessions=False)` | Store hashed password. Uses INSERT...ON DUPLICATE KEY UPDATE. |
| `delete_all_passwords_for` | `frappe.utils.password` | `(doctype, name)` | Delete all `__Auth` entries for a document (useful on player deletion). |

### Functions to AVOID

| Function | Why Avoid |
|----------|-----------|
| `set_encrypted_password()` | Stores with Fernet encryption (`encrypted=1`), NOT hashing. Incompatible with `check_password()`. |
| `get_decrypted_password()` | For retrieving encrypted secrets, not for auth password verification. |
| `frappe.utils.password.get_password_reset_limit()` | Reads from System Settings -- only applies to Frappe User, not custom DocTypes. |

---

## Environment Configuration Changes

### New Config Values

No new `.env` variables needed. All infrastructure (Redis, Frappe URL, JWT secret) already exists.

### Config Values to Update

| Setting | Current | After Migration | Notes |
|---------|---------|-----------------|-------|
| `create_access_token()` `email` param | Required | Optional (default `""`) | Players don't have email |

---

## Migration Path for Existing Data

### Player Profile Autoname Change

- **Current:** `autoname: "field:user"` (docname = email like `mohamad@gmail.com`)
- **Target:** `autoname: "PLAYER-.#####."` (docname = `PLAYER-00001`)

**Migration approach:**
1. Add `mobile` field and `password` field to DocType
2. Migrate existing profiles: set `mobile` from Frappe User's `mobile_no`
3. Use `frappe.rename_doc()` to change docnames from email to PLAYER-##### format
4. Update `__Auth` entries: copy User's hashed password to Player Profile entries
5. Remove `user` field link

**Critical: Password migration from User to Player Profile:**
```python
# For each existing player:
# 1. Get the hashed password from User's __Auth entry
# 2. Insert directly into __Auth with new doctype+name
frappe.db.sql("""
    INSERT INTO `__Auth` (doctype, name, fieldname, password, encrypted)
    SELECT 'Memora Player Profile', player_new_name, 'password', a.password, a.encrypted
    FROM `__Auth` a
    WHERE a.doctype = 'User' AND a.name = old_email AND a.fieldname = 'password'
    ON DUPLICATE KEY UPDATE password = a.password
""")
```

---

## Summary: What to Build, What to Reuse

### Build New

| Component | Location | Effort |
|-----------|----------|--------|
| Frappe whitelisted auth API | `memora_admin/api/auth.py` | Medium |
| Player Profile DocType changes | `memora_player_profile.json/.py` | Medium |
| OTP service | `fastapi_app/services/otp.py` | Low |
| Temp token logic | Inline in auth endpoints | Low |
| FastAPI auth endpoint rewrite | `fastapi_app/api/v1/endpoints/auth.py` | Medium |
| FrappeAuthService rewrite | `fastapi_app/services/frappe.py` | Medium |
| Data migration script | `memora_admin/patches/` | Medium |
| Phone validation (Pydantic + DocType) | Both sides | Low |

### Reuse Existing (No Changes)

| Component | Why No Changes |
|-----------|----------------|
| JWT token creation/decode | `security.py` -- just make `email` optional |
| Rate limiter | Already works, just different target_account string |
| Session service | Uses `user_id` opaquely |
| Device service | Uses `user_id` opaquely |
| Redis connection pool | Same pool, new key prefixes |
| All game endpoints | Use `user.sub` from JWT -- string-agnostic |

---

## Sources

All findings verified from local source code inspection:

| Source | Path | Confidence |
|--------|------|------------|
| Frappe password module | `/home/corex/aurevia-bench/apps/frappe/frappe/utils/password.py` | HIGH (source code) |
| Frappe base document | `/home/corex/aurevia-bench/apps/frappe/frappe/model/base_document.py:1126-1157` | HIGH (source code) |
| Frappe User DocType | `/home/corex/aurevia-bench/apps/frappe/frappe/core/doctype/user/user.py:144` | HIGH (source code) |
| `__Auth` table DDL | `/home/corex/aurevia-bench/apps/frappe/frappe/database/mariadb/framework_mariadb.sql:279-287` | HIGH (source code) |
| `__Auth` live data | MariaDB `SELECT` on production | HIGH (verified) |
| `check_password` with custom DocType | Runtime test on `x.conanacademy.com` | HIGH (verified) |
| passlib version | `pip show passlib` in bench env: 1.7.4 | HIGH (verified) |
| Existing codebase patterns | Multiple files in `fastapi_app/services/` | HIGH (source code) |
