# Mobile-First Player Authentication

## Problem

Players are Arabic-speaking students who mostly have mobile numbers, not emails. The current system authenticates players via Frappe User (email + password), but this forces us to require an email address that most players don't have.

## Decision

Replace Frappe User-based authentication for players with a **phone number + password** model stored directly in `Memora Player Profile`. Frappe User remains for admins/content managers only.

| Audience | Auth Method | Identity |
|----------|------------|----------|
| Players  | Phone + Password → FastAPI JWT | Phone number (e.g., `966512345678`) |
| Admins   | Email + Password → Frappe Session | Frappe User (email) |

## What Changes

### Player Profile DocType

- **Remove**: `user` field (Link → User)
- **Add**: `mobile` field (Data, unique, required) — becomes the primary key via `autoname: "field:mobile"`
- **Add**: `password` field (Password fieldtype) — auto-hashed to PBKDF2-SHA256 in Frappe's `__Auth` table
- Player Profile docname changes from email to phone number (e.g., `966512345678`)

### Login Flow (FastAPI)

**Before:**
```
Phone/Email + Password → FastAPI → Frappe /api/method/login → Frappe session → Fetch profile → JWT
```

**After:**
```
Phone + Password → FastAPI → Frappe whitelisted API (check_password on Player Profile) → JWT
```

- No Frappe User created for players
- No Frappe session created/destroyed per login
- Password verified via `frappe.utils.password.check_password()` against Player Profile's `__Auth` entry
- JWT `sub` claim = phone number instead of email

### Registration Flow

New Frappe whitelisted API to create Player Profile with:
- `mobile` (phone number — becomes docname)
- `password` (hashed automatically by Password fieldtype)
- `display_name`, `plan`, `avatar`, `grade`, `major`, `season`

No Frappe User created. Wallet auto-created via existing `after_insert` hook.

### Redis Keys

All keys that currently use email will use phone number:
```
memora:session:966512345678
memora:access:966512345678
memora:progress:966512345678:SUBJ-001:v1
memora:wallet:966512345678
memora:devices:966512345678
memora:profile:966512345678
memora:pending:966512345678
```

No code changes needed — these are just string concatenations with `user.sub`.

### JWT Token

```json
{
  "sub": "966512345678",
  "plan": "PLAN-00001",
  "name": "Ahmed",
  "fid": "uuid-session-id",
  "type": "access"
}
```

- `sub` = phone number (was email)
- `email` field removed (players don't have email)

## What Does NOT Change

- All FastAPI endpoints — they use `user.sub` from JWT, which is just a string
- Redis key structure — same pattern, different value
- Access control (Double-Gate) — same logic
- Session management (family_id) — same logic
- Admin panel — admins still use Frappe User with email
- ERPNext Sales Invoice linking — Product Grant → Item, independent of player identity
- Admin email notifications — queries admin Users only, never player Users
- Webhook payment flow — `player_id` comes from payment provider
- All game services (progress, wallet, leaderboard, reviews) — use `user.sub` opaquely

## Security Analysis

### Authorization Model: UNCHANGED

All 14 FastAPI endpoints were audited. Every player-scoped operation uses `user.sub` from the server-signed JWT — never from request body or path parameters. Players cannot impersonate other players regardless of whether `sub` is an email or phone number.

### Password Security: FRAPPE'S BATTLE-TESTED SYSTEM

Frappe's Password fieldtype on custom doctypes:
- Passwords stored in separate `__Auth` table (not in the doctype table)
- Auto-hashed with PBKDF2-SHA256 / Argon2 via `passlib`
- Automatic hash migration to newer algorithms
- Plaintext never stored — replaced with `*****` in document
- `check_password(user, pwd, doctype, fieldname)` works with any doctype

### Admin vs Player Separation: CLEAN

- Admin endpoints gated by `RequireAdmin` (checks `role == "System Manager"` in JWT)
- Admin role can only be set during token creation for actual Frappe System Manager users
- Players cannot forge admin tokens without `JWT_SECRET`

## Concerns & Cautions

### 1. Phone Number Normalization (CRITICAL)

Phone numbers must be normalized BEFORE storage. Without this, the same person could create duplicate accounts.

**Rule**: Store as digits only with country code, no `+` prefix.

```
Input: "+962 512 345 678" → Stored: "962512345678"
Input: "0512345678"       → Stored: "962512345678" (with default country code)
Input: "962512345678"     → Stored: "962512345678"
```

**Where to enforce**: Player Profile `validate()` hook + FastAPI login request validation.

### 2. Player Profile Docname is Immutable

Frappe docnames cannot be easily changed. If a player changes their phone number:
- Option A: Frappe `rename_doc()` — expensive, updates all linked records
- Option B: Add a separate `mobile` field and use a different autoname (e.g., `PLAYER-.#####.`)

**Recommendation**: Use `autoname: "PLAYER-.#####."` with a separate unique `mobile` field. This decouples the identity from the phone number and avoids rename headaches.

### 3. Password Verification Requires Frappe API Call

FastAPI cannot call `frappe.utils.password.check_password()` directly (different process). Login requires an HTTP round-trip to a Frappe whitelisted method.

**Impact**: ~10-50ms extra latency on login only. Acceptable since login is infrequent (once per 30-day refresh token lifetime).

**Mitigation**: The whitelisted method should be `allow_guest=True` (no Frappe session needed) and should do minimal work — just `check_password()` + return profile data.

### 4. Password Management Features Must Be Built

Frappe User provides these for free. With custom password, we must build:

| Feature | Priority | Notes |
|---------|----------|-------|
| Password min length / complexity | High | Add validation in Player Profile `validate()` |
| Rate limiting on login | **Already done** | `rate_limit.py` in FastAPI |
| Password reset (admin-initiated) | Medium | Admin sets new password via Frappe Desk |
| Password reset (player-initiated) | Low | Requires SMS/WhatsApp channel — future work |
| Password expiry | Low | Not needed initially |
| Password history (no reuse) | Low | Not needed initially |

### 5. `is_email()` Detection Must Be Updated

Current login uses `"@" in identifier` to distinguish email vs mobile. After migration:
- Players always use phone number
- Admins might still use email (if admin login goes through same endpoint)

**Recommendation**: Either separate the login endpoints (`/auth/player/login` vs Frappe Desk for admins) or update the detection logic.

### 6. Existing Event Handlers Need Updates

Three files reference `doc.user` (the old Link → User field):

| File | Line | Current | New |
|------|------|---------|-----|
| `events/access_sync.py` | 88-101 | `player_doc.user` | `player_doc.name` (or `player_doc.mobile`) |
| `events/device_sync.py` | 45 | `doc.user` | `doc.name` (or `doc.mobile`) |
| `events/plan_change_sync.py` | 32 | `doc.user` | `doc.name` (or `doc.mobile`) |

### 7. Frappe API `purchase.py` Lookup Must Be Updated

`memora_admin/api/purchase.py:44` currently does:
```python
player_id = frappe.get_value("Memora Player Profile", {"user": user_id}, "name")
```

After migration, `user_id` from JWT is the phone number which IS the profile name (or lookup by `mobile` field), so this lookup changes.

### 8. Leaderboard Display Names

Leaderboards show `player_id` in entries. Currently this is an email — after migration it's a phone number. Ensure leaderboard entries show `display_name`, not the raw `player_id`, to avoid exposing phone numbers to other players.

## Files to Modify

### Frappe Side
- `memora_player_profile.json` — Remove `user` field, add `mobile` + `password` fields, change `autoname`
- `memora_player_profile.py` — Add phone normalization in `validate()`, password policy
- `memora_admin/api/auth.py` — NEW: Whitelisted method for player password verification
- `memora_admin/api/purchase.py` — Update player lookup
- `events/access_sync.py` — Replace `doc.user` references
- `events/device_sync.py` — Replace `doc.user` references
- `events/plan_change_sync.py` — Replace `doc.user` references
- `events/profile_sync.py` — Replace `doc.user` references

### FastAPI Side
- `fastapi_app/api/v1/endpoints/auth.py` — New login flow (call Frappe whitelisted method instead of `verify_credentials`)
- `fastapi_app/services/frappe.py` — Replace `verify_credentials()` with `verify_player_password()`, remove `lookup_user_by_mobile()`
- `fastapi_app/models/auth.py` — Update `LoginRequest` (identifier → mobile), `TokenPayload` (email optional), `FrappeUser` model
- `fastapi_app/core/security.py` — `email` param optional in `create_access_token()`

### No Changes Needed
- All game endpoints (sessions, progress, wallet, reviews, leaderboard, catalog, notifications)
- All services (access, progress, wallet, session, device, leaderboard, settings, purchase service)
- Redis key logic
- Admin endpoints
- ERPNext integration
- Webhook handler
