# Phase 30: Frappe Auth API Bridge - Research

**Researched:** 2026-02-12
**Domain:** Frappe whitelisted APIs for player password verification, registration, and password management
**Confidence:** HIGH

## Summary

This phase creates three whitelisted Frappe API functions in a new `memora_admin/api/auth.py` file that serve as the bridge between the FastAPI sidecar and Frappe's password infrastructure. The APIs enable FastAPI to verify player passwords, register new players, and manage passwords -- all without creating Frappe sessions.

The critical infrastructure is already in place from Phase 29: the Player Profile DocType has `mobile` (unique, Data), `password` (Password fieldtype, hidden), PBKDF2-SHA256 hashing via `update_password()`, and the `flags.ignore_save_passwords` bypass. Phase 30 builds the API layer on top of this foundation.

The approach uses Frappe's existing `check_password()` and `update_password()` from `frappe.utils.password`, which have been verified to work with custom DocTypes via the `doctype` and `fieldname` parameters. The `__Auth` table stores hashed passwords with `encrypted=0`, and `check_password()` queries only `encrypted=0` rows.

**Primary recommendation:** Create `memora_admin/api/auth.py` with three functions: `verify_player_password` (allow_guest=False, called via FrappeClient), `register_player` (allow_guest=False, called via FrappeClient), and `set_player_password` (allow_guest=False, called via FrappeClient and from Desk). Use `allow_guest=False` on ALL three functions and call them through the existing `FrappeClient` (which authenticates with API key/secret) to prevent direct unauthenticated access to the password verification endpoint.

## Standard Stack

### Core

| Library/Module | Version | Purpose | Why Standard |
|----------------|---------|---------|--------------|
| `frappe.utils.password.check_password` | Frappe v15 | Verify password against `__Auth` table | Built-in, already verified to work with custom DocTypes |
| `frappe.utils.password.update_password` | Frappe v15 | Store PBKDF2-SHA256 hash in `__Auth` table | Built-in, INSERT ON DUPLICATE KEY UPDATE pattern |
| `frappe` ORM | v15 | DocType CRUD, `get_doc`, `get_value`, `db.exists` | Standard Frappe API patterns |
| `redis` (sync) | via `get_fastapi_redis()` | Session invalidation, wallet initialization | Established pattern in `access_sync.py`, `devices.py` |

### Supporting

| Library/Module | Version | Purpose | When to Use |
|----------------|---------|---------|-------------|
| `re` (stdlib) | Python 3.10+ | Phone normalization | Already in `memora_player_profile.py` |
| `frappe.utils.password.delete_all_passwords_for` | Frappe v15 | Cleanup on player deletion | Future use -- not needed this phase |

### Alternatives Considered

| Instead of | Could Use | Tradeoff |
|------------|-----------|----------|
| `allow_guest=False` + FrappeClient | `allow_guest=True` + guest httpx client | Guest access exposes endpoint to direct brute-force; FrappeClient auth prevents this (Pitfall 15 resolution) |
| Direct Redis DEL for session invalidation | Pubsub signal for distributed invalidation | Direct DEL is simpler and sufficient -- single FastAPI instance reads same Redis |

**No new dependencies required.** Everything needed already exists in the Frappe bench environment and the project codebase.

## Architecture Patterns

### Recommended Project Structure

```
memora_admin/
  api/
    auth.py              # NEW: 3 whitelisted functions
  memora_admin/
    doctype/
      memora_player_profile/
        memora_player_profile.py   # EXISTING: password hashing logic (Phase 29)
        memora_player_profile.js   # MODIFY: add "Reset Password" button
```

### Pattern 1: Whitelisted API with FrappeClient Authentication

**What:** All three auth APIs use `allow_guest=False` and are called from FastAPI via the existing `FrappeClient` which authenticates with API key/secret token.

**When to use:** When the API performs sensitive operations (password verification, user creation, password changes) and should not be directly accessible from the internet.

**Why this over `allow_guest=True`:** The PITFALLS research (Pitfall 15) identified that `allow_guest=True` on a password verification endpoint allows anyone on the network to call the Frappe endpoint directly, bypassing FastAPI's rate limiting. Using `allow_guest=False` with FrappeClient means:
1. Only the FastAPI sidecar (with API key credentials) can call these methods
2. Rate limiting is enforced at the FastAPI layer before the Frappe call
3. No additional network-level restrictions needed

**Example:**
```python
# memora_admin/api/auth.py (Frappe side)
import frappe
from frappe.utils.password import check_password

@frappe.whitelist(allow_guest=False)
def verify_player_password(mobile: str, password: str) -> dict:
    """Verify player password. Called by FastAPI via FrappeClient.

    allow_guest=False -- requires API key auth from FrappeClient.
    """
    # Normalize phone (reuse DocType logic)
    import re
    mobile = re.sub(r"[^\d]", "", mobile)

    # Resolve mobile -> docname (critical: check_password needs docname)
    player_name = frappe.db.get_value(
        "Memora Player Profile", {"mobile": mobile}, "name"
    )
    if not player_name:
        frappe.throw("Invalid credentials", frappe.AuthenticationError)

    # Verify against __Auth table (encrypted=0, PBKDF2-SHA256)
    try:
        check_password(
            player_name,
            password,
            doctype="Memora Player Profile",
            fieldname="password",
        )
    except frappe.AuthenticationError:
        frappe.throw("Invalid credentials", frappe.AuthenticationError)

    # Fetch profile data in same call (eliminates extra HTTP roundtrip)
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

### Pattern 2: Mobile-to-Docname Resolution Before check_password

**What:** Always resolve the mobile number to the Player Profile docname before calling `check_password()`, because the `__Auth` table's `name` column stores the docname (e.g., `PLAYER-00001`), not the phone number.

**When to use:** Every time password verification or password update is needed.

**Why critical:** The `__Auth` table primary key is `(doctype, name, fieldname)`. The `name` field contains the document name used in `update_password()`. Since Phase 29 set autoname to `PLAYER-.#####.`, the `__Auth` entry has `name=PLAYER-00001`. Calling `check_password("966512345678", ...)` would fail because no `__Auth` row exists with `name=966512345678`.

**Example:**
```python
# CORRECT: resolve mobile -> docname first
player_name = frappe.db.get_value("Memora Player Profile", {"mobile": mobile}, "name")
check_password(player_name, password, doctype="Memora Player Profile", fieldname="password")

# WRONG: using mobile directly
check_password(mobile, password, doctype="Memora Player Profile", fieldname="password")
# This will ALWAYS fail -- no __Auth row with name=mobile_number
```

### Pattern 3: Generic Error Messages (Anti-Enumeration)

**What:** Both "phone not found" and "wrong password" return the same generic error: "Invalid credentials". The CONTEXT.md decision explicitly requires this.

**When to use:** `verify_player_password` only. Registration duplicate error is different (see below).

**Example:**
```python
# Phone not found -- same error as wrong password
player_name = frappe.db.get_value("Memora Player Profile", {"mobile": mobile}, "name")
if not player_name:
    frappe.throw("Invalid credentials", frappe.AuthenticationError)

# Wrong password -- same error
try:
    check_password(player_name, password, ...)
except frappe.AuthenticationError:
    frappe.throw("Invalid credentials", frappe.AuthenticationError)
```

### Pattern 4: Registration with `ignore_permissions=True`

**What:** The `register_player` function creates the Player Profile with `ignore_permissions=True` because the FrappeClient's API user may not have explicit write permission on the DocType.

**When to use:** Any document creation from a whitelisted API called by the FastAPI sidecar.

**Why:** This is the established pattern in `subscriptions.py:51` (`doc.insert()` -- though there it relies on the API user's System Manager role). Using `ignore_permissions=True` makes the behavior explicit and independent of the API user's role configuration.

**Example:**
```python
doc = frappe.get_doc({
    "doctype": "Memora Player Profile",
    "mobile": mobile,
    "password": password,
    "display_name": display_name or generate_default_name(),
    "plan": plan,
    "avatar": avatar or "pre",  # Default avatar
    "grade": grade,
    "major": major,
    "season": season,
    "gender": gender,
})
doc.insert(ignore_permissions=True)
```

### Pattern 5: Session Invalidation via Direct Redis DEL

**What:** When a password is reset (via admin or self-service), immediately delete the player's session key from Redis to force re-login on all devices.

**When to use:** `set_player_password` function, after the password hash is updated.

**Why direct DEL over pubsub:** The codebase already uses direct Redis DEL for session invalidation in `devices.py:148` (`r.delete(session_key)`). The pubsub pattern is used for cache invalidation across multiple consumers (e.g., hierarchy cache), but session invalidation is simpler -- a single Redis key deletion. All FastAPI instances read from the same Redis, so there is no need for pubsub.

**Example:**
```python
# After updating password hash:
r = get_fastapi_redis()
session_key = f"memora:session:{player_name}"
r.delete(session_key)
```

### Anti-Patterns to Avoid

- **Calling `check_password()` with mobile number instead of docname:** The `__Auth` table `name` column stores the docname (`PLAYER-00001`), not the phone number. Always resolve mobile to docname first.
- **Using `set_encrypted_password()` instead of `update_password()`:** `set_encrypted_password()` stores with Fernet encryption (`encrypted=1`), which is reversible and invisible to `check_password()` (which queries `encrypted=0`).
- **Using `allow_guest=True` on password verification endpoint:** Exposes the endpoint to direct brute-force attacks bypassing FastAPI rate limiting.
- **Importing `frappe` in FastAPI code:** Frappe and FastAPI are separate processes with separate Python environments. Always use HTTP calls via FrappeClient.
- **Creating a Frappe session during verification:** The whole point of this phase is to avoid Frappe sessions for player auth. The whitelisted API functions run server-side within Frappe but do not create/return session cookies.

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| Password hashing | Custom bcrypt/argon2 implementation | `frappe.utils.password.update_password()` | Already stores PBKDF2-SHA256 in `__Auth` table with INSERT ON DUPLICATE KEY UPDATE. Auto-upgrades hash algorithm when passlib detects a better one is available. |
| Password verification | Custom hash comparison | `frappe.utils.password.check_password()` | Handles timing-safe comparison, hash algorithm detection, and automatic re-hashing when the hash scheme is outdated. |
| Phone normalization | New normalization function | `MemoraPlayerProfile._normalize_mobile()` (already exists) | Reuse the static method from Phase 29 to avoid divergent normalization logic between API and DocType. |
| HTTP client for Frappe calls | New httpx client for auth APIs | `FrappeClient` (existing singleton) | Already handles auth headers, error parsing, and connection management. All three auth APIs use `allow_guest=False`, so FrappeClient's API key auth is required. |
| Redis connection in Frappe | `frappe.cache()` | `get_fastapi_redis()` from `access_sync.py` | Must write to the same Redis namespace as FastAPI. `frappe.cache()` uses site-prefixed keys that FastAPI cannot read. |

**Key insight:** Every building block for this phase already exists. The phase is purely about composing existing utilities (`check_password`, `update_password`, `get_fastapi_redis`, `FrappeClient`) into three new API functions.

## Common Pitfalls

### Pitfall 1: `__Auth` Table Keying Mismatch

**What goes wrong:** Calling `check_password("966512345678", ...)` instead of `check_password("PLAYER-00001", ...)`. The `__Auth` table stores password hashes keyed by document name, not phone number. Phase 29's `_hash_password()` calls `update_password(self.name, ...)` where `self.name` is the autoname-generated docname.

**Why it happens:** Developers assume the "user" parameter in `check_password(user, pwd, ...)` means "the identifier the user typed" (i.e., the phone number). In reality, `user` maps to the `name` column in `__Auth`, which is the document name.

**How to avoid:** Always do a mobile-to-docname lookup FIRST:
```python
player_name = frappe.db.get_value("Memora Player Profile", {"mobile": mobile}, "name")
check_password(player_name, password, doctype="Memora Player Profile", fieldname="password")
```

**Warning signs:** `check_password` raises `AuthenticationError` even with correct passwords, or returns no results.

### Pitfall 2: Required Fields on Player Profile Block Registration

**What goes wrong:** The `register_player` API fails with Frappe ValidationError because required fields (`display_name`, `plan`, `avatar`, `grade`, `major`, `season`) are missing or empty.

**Why it happens:** The CONTEXT.md says `display_name`, `gender`, and `avatar` are optional at registration. But the DocType JSON schema has `"reqd": 1` on `display_name`, `plan`, `avatar`, `grade`, `major`, and `season`. Frappe enforces these on `doc.insert()`.

**How to avoid:** The `register_player` function must:
1. Accept all required fields as parameters (plan, grade, major, season are always required)
2. Provide sensible defaults for "optional at registration" fields:
   - `display_name`: Auto-generate if not provided (e.g., "player_{numeric_suffix}")
   - `avatar`: Default to "pre" (first option in the Select field)
   - `gender`: Truly optional (not `reqd: 1` in schema, so no issue)

**Warning signs:** `frappe.exceptions.MandatoryError` on `doc.insert()`.

### Pitfall 3: Password Not Hashed on Registration

**What goes wrong:** The password is stored via Fernet encryption (`encrypted=1`) instead of PBKDF2-SHA256 hashing (`encrypted=0`), making `check_password()` unable to verify it.

**Why it happens:** If the `register_player` API creates the doc and saves the password field without the `flags.ignore_save_passwords` bypass, Frappe's default `_save_passwords()` method stores it as Fernet-encrypted.

**How to avoid:** This is already handled by Phase 29's `__setup__()` method which sets `self.flags.ignore_save_passwords = ["password"]`. The `validate()` method captures the raw password, and `after_insert()` calls `_hash_password()` which uses `update_password()`. The `register_player` API just needs to set the `password` field on the doc before `insert()` -- the DocType class handles the rest.

**Warning signs:** After registration, `check_password()` raises `AuthenticationError` even with the correct password. Check `__Auth` table: if `encrypted=1` for the player's row, hashing was bypassed.

### Pitfall 4: Using `frappe.cache()` Instead of `get_fastapi_redis()` for Session Invalidation

**What goes wrong:** Session invalidation (Redis DEL) targets the wrong Redis namespace. The session key exists in the FastAPI Redis (`redis://127.0.0.1:13000`) but `frappe.cache()` writes to Frappe's prefixed Redis. The DELETE operation silently succeeds (deleting nothing), and the player's session remains active after password reset.

**Why it happens:** Developers assume `frappe.cache()` and the FastAPI Redis are the same. They are not -- `frappe.cache()` uses site-specific key prefixes.

**How to avoid:** Always use `get_fastapi_redis()` from `memora_admin/events/access_sync.py` for any Redis operations that need to be visible to FastAPI. This is the established pattern in `devices.py` and `access_sync.py`.

**Warning signs:** Password reset succeeds but player is not logged out. Check if session key still exists in FastAPI Redis.

### Pitfall 5: Admin Password Reset Not Triggering Session Invalidation

**What goes wrong:** Admin changes a player's password from Frappe Desk, but the player's sessions are not invalidated. The player continues using the old password's session until the refresh token expires.

**Why it happens:** The DocType's `on_update` hook calls `_hash_password()` but does not call session invalidation. Password hashing and session invalidation are separate concerns.

**How to avoid:** The `set_player_password` whitelisted API must explicitly invalidate sessions after updating the password. For the Desk UI flow, add a "Reset Password" button to the Player Profile JS that calls `set_player_password` (which handles both hashing and invalidation).

**Warning signs:** Admin resets password but player remains logged in on their mobile device.

### Pitfall 6: Duplicate Phone Error Leaks Information on Verify

**What goes wrong:** `verify_player_password` returns a different error for "phone not found" vs "wrong password", enabling phone number enumeration.

**Why it happens:** Developers catch only the `check_password` exception and forget to handle the "phone not found" case with the same generic error.

**How to avoid:** CONTEXT.md explicitly requires generic "Invalid credentials" for both cases. The code structure must be:
```python
if not player_name:
    frappe.throw("Invalid credentials", frappe.AuthenticationError)
try:
    check_password(...)
except frappe.AuthenticationError:
    frappe.throw("Invalid credentials", frappe.AuthenticationError)
```

## Code Examples

### verify_player_password (Complete Implementation)

```python
# Source: Composed from verified frappe.utils.password API + codebase patterns

import re
import frappe
from frappe.utils.password import check_password

MOBILE_PATTERN = re.compile(r"^\d{9,15}$")

@frappe.whitelist(allow_guest=False)
def verify_player_password(mobile: str, password: str) -> dict:
    """Verify player credentials and return profile data.

    Called by FastAPI via FrappeClient (API key auth).
    Returns profile bundle: player_id, display_name, plan, avatar, gender, mobile.
    Generic error on failure (anti-enumeration).
    """
    # Normalize phone (same logic as DocType validate)
    mobile = re.sub(r"[^\d]", "", mobile)
    if not MOBILE_PATTERN.match(mobile):
        frappe.throw("Invalid credentials", frappe.AuthenticationError)

    # Resolve mobile -> docname (check_password needs docname, not phone)
    player_name = frappe.db.get_value(
        "Memora Player Profile", {"mobile": mobile}, "name"
    )
    if not player_name:
        frappe.throw("Invalid credentials", frappe.AuthenticationError)

    # Verify password against __Auth table
    try:
        check_password(
            player_name,
            password,
            doctype="Memora Player Profile",
            fieldname="password",
        )
    except frappe.AuthenticationError:
        frappe.throw("Invalid credentials", frappe.AuthenticationError)

    # Fetch full profile in same call (avoids extra HTTP roundtrip)
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

### register_player (Complete Implementation)

```python
# Source: Composed from existing DocType patterns + CONTEXT.md decisions

@frappe.whitelist(allow_guest=False)
def register_player(
    mobile: str,
    password: str,
    plan: str,
    grade: str,
    major: str,
    season: str,
    display_name: str | None = None,
    avatar: str | None = None,
    gender: str | None = None,
) -> dict:
    """Create new player profile with phone+password.

    Called by FastAPI (self-registration) or Frappe Desk (admin creates player).
    Password is hashed via DocType's after_insert hook (PBKDF2-SHA256).
    Wallet is created via DocType's after_insert hook (_create_player_wallet).

    Returns profile data for immediate JWT creation by FastAPI.
    Duplicate phone returns specific error (safe: behind OTP verification).
    """
    # Normalize phone
    mobile = re.sub(r"[^\d]", "", mobile)
    if not MOBILE_PATTERN.match(mobile):
        frappe.throw("Mobile number must be 9-15 digits", frappe.ValidationError)

    # Check uniqueness (specific error per CONTEXT.md)
    if frappe.db.exists("Memora Player Profile", {"mobile": mobile}):
        frappe.throw("Phone already registered", frappe.DuplicateEntryError)

    # Default display_name if not provided
    if not display_name:
        # Generate Arabic default: "لاعب 12345" pattern
        # Use last 5 digits of next autoname sequence for uniqueness
        count = frappe.db.count("Memora Player Profile") + 1
        display_name = f"\u0644\u0627\u0639\u0628 {count}"

    doc = frappe.get_doc({
        "doctype": "Memora Player Profile",
        "mobile": mobile,
        "password": password,  # Captured by validate(), hashed by after_insert()
        "display_name": display_name,
        "plan": plan,
        "avatar": avatar or "pre",  # Default to first avatar option
        "grade": grade,
        "major": major,
        "season": season,
        "gender": gender,
    })
    doc.insert(ignore_permissions=True)

    # Initialize wallet in Redis (player fully ready after register)
    _initialize_redis_wallet(doc.name)

    return {
        "player_id": doc.name,
        "display_name": doc.display_name,
        "plan": doc.plan,
        "avatar": doc.avatar,
        "gender": doc.gender,
        "mobile": doc.mobile,
    }


def _initialize_redis_wallet(player_name: str) -> None:
    """Seed wallet hash in Redis with xp=0.

    Per CONTEXT.md: wallet initialized in same call as profile creation.
    The DocType after_insert creates the MariaDB wallet record.
    This seeds the Redis cache so the player is immediately ready.
    """
    from memora_admin.events.access_sync import get_fastapi_redis

    try:
        r = get_fastapi_redis()
        wallet_key = f"memora:wallet:{player_name}"
        r.hset(wallet_key, mapping={"xp": 0, "streak": 0})
    except Exception:
        # Non-fatal: wallet will be hydrated from MariaDB on first API call
        frappe.logger().warning(f"Failed to initialize Redis wallet for {player_name}")
```

### set_player_password (Complete Implementation)

```python
# Source: Composed from update_password API + devices.py session invalidation pattern

@frappe.whitelist(allow_guest=False)
def set_player_password(player_name: str, new_password: str) -> dict:
    """Set new password for a player. Invalidates all sessions.

    Called from:
    - FastAPI (password reset flow, after OTP verification)
    - Frappe Desk (admin resets player password via custom button)

    Args:
        player_name: Player Profile docname (e.g., PLAYER-00001)
        new_password: New password (min 8 chars, enforced by DocType validate)
    """
    # Validate player exists
    if not frappe.db.exists("Memora Player Profile", player_name):
        frappe.throw("Player not found", frappe.DoesNotExistError)

    # Validate password policy (same as DocType)
    if len(new_password) < 8:
        frappe.throw("Password must be at least 8 characters", frappe.ValidationError)

    # Update password hash in __Auth table
    from frappe.utils.password import update_password
    update_password(
        player_name,
        new_password,
        doctype="Memora Player Profile",
        fieldname="password",
    )

    # Invalidate all sessions (force logout on all devices)
    _invalidate_player_sessions(player_name)

    return {"success": True, "player_name": player_name}


def _invalidate_player_sessions(player_name: str) -> None:
    """Delete session key from Redis to force re-login on all devices.

    Uses direct Redis DEL (same pattern as devices.py:148).
    Access tokens expire naturally (short-lived). Refresh is blocked immediately.
    """
    from memora_admin.events.access_sync import get_fastapi_redis

    try:
        r = get_fastapi_redis()
        session_key = f"memora:session:{player_name}"
        r.delete(session_key)
        frappe.logger().info(f"Sessions invalidated for {player_name}")
    except Exception as e:
        frappe.logger().error(f"Failed to invalidate sessions for {player_name}: {e}")
        # Non-fatal: sessions will expire naturally via TTL
```

### Desk UI: Reset Password Button (JS)

```javascript
// Added to memora_player_profile.js
// Pattern matches existing "Grant Access" button

frappe.ui.form.on("Memora Player Profile", {
    refresh: function(frm) {
        if (frm.is_new()) return;

        frm.add_custom_button(__("Reset Password"), function() {
            let dialog = new frappe.ui.Dialog({
                title: __("Reset Player Password"),
                fields: [
                    {
                        fieldname: "new_password",
                        fieldtype: "Password",
                        label: __("New Password"),
                        reqd: 1,
                        description: __("Minimum 8 characters"),
                    },
                    {
                        fieldname: "confirm_password",
                        fieldtype: "Password",
                        label: __("Confirm Password"),
                        reqd: 1,
                    },
                ],
                primary_action_label: __("Reset Password"),
                primary_action: function(values) {
                    if (values.new_password !== values.confirm_password) {
                        frappe.msgprint(__("Passwords do not match"));
                        return;
                    }
                    if (values.new_password.length < 8) {
                        frappe.msgprint(__("Password must be at least 8 characters"));
                        return;
                    }
                    frappe.call({
                        method: "memora_admin.api.auth.set_player_password",
                        args: {
                            player_name: frm.doc.name,
                            new_password: values.new_password,
                        },
                        freeze: true,
                        freeze_message: __("Resetting password..."),
                        callback: function(r) {
                            if (r.message && r.message.success) {
                                dialog.hide();
                                frappe.show_alert({
                                    message: __("Password reset. Player will be logged out."),
                                    indicator: "green",
                                });
                            }
                        },
                    });
                },
            });
            dialog.show();
        }, __("Actions"));
    },
});
```

### FrappeClient Integration (FastAPI Side)

```python
# How FastAPI calls the new Frappe auth APIs
# Uses existing FrappeClient pattern from frappe_client.py

# In FastAPI endpoint or service:
frappe_client = await get_frappe_client()

# Verify password
result = await frappe_client.call(
    "memora_admin.api.auth.verify_player_password",
    {"mobile": "966512345678", "password": "userpass123"},
)
# result = {"player_id": "PLAYER-00001", "display_name": "...", ...}

# Register player
result = await frappe_client.call(
    "memora_admin.api.auth.register_player",
    {
        "mobile": "966512345678",
        "password": "userpass123",
        "plan": "PLAN-00001",
        "grade": "GRD-00001",
        "major": "MJR-00001",
        "season": "SN-00001",
        "display_name": "Ahmad",
        "avatar": "Caleb",
        "gender": "Male",
    },
)

# Set password (admin or password reset flow)
result = await frappe_client.call(
    "memora_admin.api.auth.set_player_password",
    {"player_name": "PLAYER-00001", "new_password": "newpass456"},
)
```

## State of the Art

| Old Approach (Current Login) | New Approach (This Phase) | When Changed | Impact |
|------------------------------|---------------------------|--------------|--------|
| Frappe `/api/method/login` creates session, then 3 more HTTP calls | Single `verify_player_password` call, no session | Phase 30 | Login drops from 4 HTTP calls to 1; no Frappe session overhead |
| `FrappeAuthService` with httpx guest client | `FrappeClient.call()` with API key auth | Phase 30 | Simpler, more secure, reuses existing singleton |
| Player identity via Frappe User email | Player identity via PLAYER-##### docname | Phase 29 | Decoupled from Frappe User system entirely |
| Admin password reset via Frappe User reset flow | Admin password reset via custom Desk button | Phase 30 | No Frappe User needed; immediate session invalidation |

**Deprecated/outdated after this phase:**
- `FrappeAuthService.verify_credentials()` in `fastapi_app/services/frappe.py` -- will be replaced in Phase 31 by calls to the new Frappe auth API. Not removed in this phase (backward compat for existing admin login).
- `FrappeAuthService.lookup_user_by_mobile()` -- will be replaced by the new `verify_player_password` which handles mobile lookup internally.

## Claude's Discretion Recommendations

### 1. Session Invalidation Mechanism: Direct Redis DEL

**Recommendation:** Use direct `r.delete(session_key)` via `get_fastapi_redis()`.

**Rationale:** The codebase already uses this exact pattern in `devices.py:148` for device removal + session invalidation. The pubsub pattern (used in `profile_sync.py` and `build_trigger.py`) is for cache invalidation across distributed consumers. Session invalidation is a simple key deletion -- one key, one Redis instance. Direct DEL is simpler and already proven in this codebase.

### 2. Default Display Name Format

**Recommendation:** Use Arabic pattern "لاعب {N}" where N is based on the current count of Player Profiles + 1.

**Rationale:**
- Target audience is Arabic-speaking students
- "لاعب" means "player" in Arabic
- Using count-based suffix ensures uniqueness without UUID ugliness
- Mobile app can prompt the user to set a custom display name later
- The count is approximate (not transactional) but sufficient for display names

### 3. Wallet Initialization Approach

**Recommendation:** Call `get_fastapi_redis()` directly in the `register_player` function to seed `memora:wallet:{player_name}` with `{xp: 0, streak: 0}`.

**Rationale:**
- The DocType's `after_insert` already creates the MariaDB wallet record
- Redis wallet needs to exist immediately so the first API call does not require hydration
- Using `get_fastapi_redis()` is the established pattern for Frappe-side Redis writes
- Non-fatal: if Redis write fails, wallet will self-heal via `ensure_hydrated()` on first API call
- Do NOT import WalletService (FastAPI async code) into Frappe sync code

## Open Questions

1. **`register_player` required fields vs CONTEXT.md "optional"**
   - What we know: DocType JSON has `reqd: 1` on `display_name`, `plan`, `avatar`, `grade`, `major`, `season`. CONTEXT.md says `display_name` and `avatar` are optional at registration.
   - What's unclear: Should we modify the DocType schema to make `display_name` and `avatar` not required? Or should the API provide defaults?
   - Recommendation: Keep the DocType schema as-is (reqd: 1) and have the `register_player` API provide defaults when not supplied. This maintains data integrity while allowing flexible API usage. The mobile app will always send these fields; the "optional" behavior is just for the API contract.

2. **Timing of wallet Redis initialization vs MariaDB wallet creation**
   - What we know: `after_insert()` creates MariaDB wallet synchronously. Redis wallet seeding happens in `register_player()` after `doc.insert()` returns.
   - What's unclear: Is there a race condition if FastAPI calls `get_wallet()` between `doc.insert()` and Redis seeding?
   - Recommendation: No practical risk. The `register_player` API is synchronous -- Redis seeding happens in the same request before the response is returned. FastAPI only gets the player_id from the response, and any wallet access will happen in a subsequent request.

## Sources

### Primary (HIGH confidence)
- `frappe/utils/password.py` at `/home/corex/aurevia-bench/apps/frappe/frappe/utils/password.py` -- `check_password()` and `update_password()` signatures and behavior verified from source code
- `memora_player_profile.py` at `/home/corex/aurevia-bench/apps/memora_admin/memora_admin/memora_admin/doctype/memora_player_profile/memora_player_profile.py` -- Phase 29 implementation verified
- `memora_player_profile.json` at `/home/corex/aurevia-bench/apps/memora_admin/memora_admin/memora_admin/doctype/memora_player_profile/memora_player_profile.json` -- Schema with required fields verified
- `devices.py` at `/home/corex/aurevia-bench/apps/memora_admin/memora_admin/api/devices.py` -- Session invalidation pattern (direct Redis DEL) verified
- `access_sync.py` at `/home/corex/aurevia-bench/apps/memora_admin/memora_admin/events/access_sync.py` -- `get_fastapi_redis()` pattern verified
- `frappe_client.py` at `/home/corex/aurevia-bench/apps/memora_admin/fastapi_app/services/frappe_client.py` -- FrappeClient API key auth pattern verified
- `STACK_mobile_auth.md` at `/home/corex/aurevia-bench/apps/memora_admin/.planning/research/STACK_mobile_auth.md` -- Password hashing verified with live runtime test
- `ARCHITECTURE.md` at `/home/corex/aurevia-bench/apps/memora_admin/.planning/research/ARCHITECTURE.md` -- Auth flow design and anti-patterns

### Secondary (MEDIUM confidence)
- `PITFALLS_mobile-auth.md` at `/home/corex/aurevia-bench/apps/memora_admin/.planning/research/PITFALLS_mobile-auth.md` -- Pitfall 15 (allow_guest bypass) informed the allow_guest=False decision

## Metadata

**Confidence breakdown:**
- Standard stack: HIGH -- all components verified from Frappe source code and existing codebase
- Architecture: HIGH -- patterns directly sourced from existing codebase (devices.py, access_sync.py, subscriptions.py)
- Pitfalls: HIGH -- all pitfalls verified against real code paths and __Auth table behavior
- Code examples: HIGH -- composed from verified API signatures and established codebase patterns

**Research date:** 2026-02-12
**Valid until:** 2026-03-12 (stable -- Frappe v15 password API is mature and unlikely to change)
