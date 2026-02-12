# Feature Landscape: Phone+Password Authentication with OTP

**Domain:** Mobile-first player authentication for Arabic-speaking educational platform
**Researched:** 2026-02-12
**Mode:** Ecosystem (Features dimension for mobile auth migration milestone)
**Overall Confidence:** HIGH (well-understood domain, existing codebase reviewed, OWASP guidance cross-referenced)

---

## Table Stakes

Features users expect. Missing any of these makes the auth system broken or insecure.

### TS-1: Phone Number Login (Phone + Password)

| Aspect | Detail |
|--------|--------|
| **Feature** | Player logs in with phone number + password, receives JWT tokens |
| **Why Expected** | Core migration goal; replaces email-based Frappe User login |
| **Complexity** | Medium |
| **Confidence** | HIGH (PRD fully specifies; existing auth.py provides the pattern) |

**Behavior:**
- Player submits `mobile` + `password` to `POST /auth/player/login`
- FastAPI calls Frappe whitelisted API to verify password via `check_password()` on Player Profile
- On success: register device, create session, return JWT access + refresh tokens + profile
- On failure: generic "Invalid credentials" (no user enumeration)
- Existing enriched response pattern (tokens + profile + XP) is preserved

**What changes from current:**
- No Frappe session created/destroyed per login (no `login`/`logout` API calls)
- No `lookup_user_by_mobile` step (phone IS the identifier, not a lookup to email)
- `FrappeUser` model replaced with simpler player identity from Player Profile
- JWT `sub` = phone number (was email), `email` field removed from access token

### TS-2: Phone Number Normalization

| Aspect | Detail |
|--------|--------|
| **Feature** | All phone numbers normalized to digits-only with country code before storage and lookup |
| **Why Expected** | Without normalization, same person creates duplicate accounts with different formatting |
| **Complexity** | Low |
| **Confidence** | HIGH (E.164 standard is well-documented; PRD specifies this) |

**Behavior:**
- Strip all non-digit characters (`+`, spaces, dashes, parentheses)
- Store as digits only with country code: `966512345678`
- Normalization enforced in TWO places:
  1. Player Profile `validate()` hook in Frappe (catches admin-created profiles)
  2. FastAPI request validation (catches player-initiated registration/login)
- Target audience is Saudi Arabia (966) and Jordan (962) primarily

**Normalization rules:**
```
Input: "+962 512 345 678" -> Stored: "962512345678"
Input: "0512345678"       -> Stored: "966512345678" (with default country code)
Input: "962512345678"     -> Stored: "962512345678"
```

**Key format details (from E.164 research):**
- Saudi Arabia: country code 966, subscriber numbers are 9 digits, total 12 digits. Mobile numbers have second digit `5`.
- Jordan: country code 962, subscriber numbers are 9 digits (including area/operator code), total 12 digits. Mobile prefixes: 77 (Orange), 78 (Umniah), 79 (Zain).
- Validation: reject if fewer than 10 digits or more than 15 digits after normalization.

**Implementation decision from PRD:** User enters full number with country code digits. No country code picker in v1. If number starts with `0`, apply default country code (configurable, default `966`). Otherwise use as-is.

### TS-3: Phone Number Uniqueness

| Aspect | Detail |
|--------|--------|
| **Feature** | One account per phone number, enforced at database level |
| **Why Expected** | Prevents duplicate accounts; phone number is the identity |
| **Complexity** | Low |
| **Confidence** | HIGH (standard database constraint; PRD specifies `unique` on `mobile` field) |

**Behavior:**
- `mobile` field on Player Profile has `unique: 1` in DocType JSON
- MariaDB UNIQUE constraint prevents duplicates at DB level (race-condition-safe)
- Registration returns clear error on duplicate without revealing whether the phone is registered (for privacy): "Registration failed. If you already have an account, please login instead."

**Architecture note:** PRD recommends `autoname: "PLAYER-.#####."` with separate `mobile` field (not `autoname: "field:mobile"`). This decouples identity from phone number, allowing future phone number changes without Frappe `rename_doc()`.

### TS-4: Self-Registration with OTP Verification

| Aspect | Detail |
|--------|--------|
| **Feature** | Player registers with phone + password + profile fields; phone verified via OTP before account creation |
| **Why Expected** | Self-service registration is mandatory for mobile apps; OTP proves phone ownership |
| **Complexity** | Medium-High |
| **Confidence** | HIGH (well-established pattern; Redis pending state is standard) |

**Flow (2-step):**

1. **Request registration** (`POST /auth/player/register`)
   - Player submits: `mobile`, `password`, `display_name`, `avatar`, `grade`, `major`, `season`
   - Server validates all fields (phone format, password strength, required fields)
   - Server checks phone uniqueness (both MariaDB and Redis reservation)
   - Server generates OTP, stores pending registration in Redis with TTL
   - Server sends OTP to phone (static `"1111"` for now per project context)
   - Returns: `{ "message": "OTP sent", "pending_id": "<token>" }`

2. **Verify OTP and create account** (`POST /auth/player/register/verify`)
   - Player submits: `pending_id` + `otp`
   - Server validates OTP against Redis pending state
   - On match: create Player Profile in Frappe, delete pending state, return JWT tokens
   - On mismatch: increment attempt counter, return error

**Critical security pattern:** Do NOT issue a valid auth token at registration before OTP verification. The OTP step must gate actual account creation. Store registration data in Redis `memora:pending:{token}` with 10-minute TTL, only create the Frappe document after OTP verification succeeds. This is verified as a known vulnerability -- issuing tokens before OTP verification makes the OTP screen purely cosmetic. (Source: multiple security articles, including "Broken OTP: Why issuing a full token at signup is a security bug" - Medium, Feb 2026)

**Redis key:** `memora:pending:{random_token}` containing JSON with all registration fields + OTP + attempt count + created_at

### TS-5: Password Policy

| Aspect | Detail |
|--------|--------|
| **Feature** | Minimum password requirements enforced at registration and password change |
| **Why Expected** | Without policy, players use weak passwords leading to account compromises |
| **Complexity** | Low |
| **Confidence** | HIGH (OWASP ASVS provides clear guidance) |

**Recommended policy:**
- Minimum 8 characters (OWASP recommends 8+ with extra protections, or 12+ without)
- No maximum length cap below 128 characters
- Allow all characters (Arabic, Latin, digits, symbols, spaces)
- No arbitrary complexity rules (no "must contain uppercase + number" -- OWASP guidance: these reduce entropy by making passwords predictable)
- Check against common password list is optional for v1 but recommended post-launch

**Implementation:** Validate in Player Profile `validate()` hook AND in FastAPI request validation (belt and suspenders). Frappe's Password fieldtype handles the hashing (PBKDF2-SHA256 in `__Auth` table).

**Frappe Password fieldtype verification (HIGH confidence):** Passwords on custom DocTypes are stored in the separate `__Auth` table (not in the DocType table), auto-hashed with PBKDF2-SHA256 via passlib, and `check_password(doctype, name, fieldname, password)` works for any DocType. Verified via Frappe source code (`frappe/utils/password.py` on GitHub) and Frappe docs.

### TS-6: Password Reset via OTP (3-Step)

| Aspect | Detail |
|--------|--------|
| **Feature** | Player resets password via phone OTP: request OTP, verify OTP, set new password |
| **Why Expected** | Players forget passwords; without reset, they are permanently locked out |
| **Complexity** | Medium |
| **Confidence** | HIGH (PRD specifies 3-step flow; OWASP Forgot Password Cheat Sheet provides guidance) |

**Flow:**

1. **Request OTP** (`POST /auth/player/password-reset/request`)
   - Player submits: `mobile`
   - Server checks if phone exists (but returns same response either way -- no user enumeration)
   - If exists: generate OTP, store in Redis `memora:reset:{mobile}` with 10-min TTL
   - Send OTP to phone (static `"1111"` for now)
   - Returns: `{ "message": "If this number is registered, you will receive an OTP" }`

2. **Verify OTP** (`POST /auth/player/password-reset/verify`)
   - Player submits: `mobile` + `otp`
   - Server validates OTP against Redis
   - On match: generate a `reset_token` (short-lived, 5-min TTL), store in Redis `memora:reset_token:{token}` -> mobile
   - Returns: `{ "reset_token": "<token>" }`
   - On mismatch: increment attempt counter, return error

3. **Set new password** (`POST /auth/player/password-reset/confirm`)
   - Player submits: `reset_token` + `new_password`
   - Server validates token from Redis, retrieves mobile
   - Updates password on Player Profile via Frappe API
   - Deletes reset token from Redis
   - **Invalidates ALL existing sessions** (OWASP recommendation: mandatory)
   - Returns success (player must re-login)

**Security requirements (from OWASP Forgot Password Cheat Sheet):**
- Reset OTP valid for max 10 minutes
- Reset token (step 2 output) valid for max 5 minutes
- Max 3 OTP verification attempts before requiring new OTP request
- All tokens generated using cryptographically secure random number generator
- Tokens invalidated after use
- Session invalidation on password change is MANDATORY (prevents attacker who stole a session from retaining access after victim resets password)

### TS-7: OTP Rate Limiting (Send-Side)

| Aspect | Detail |
|--------|--------|
| **Feature** | Rate limit OTP send requests to prevent SMS pumping and abuse |
| **Why Expected** | Without this, attackers drain SMS budget via automated requests (SMS pumping attack) |
| **Complexity** | Medium |
| **Confidence** | HIGH (Twilio documents 5 per 10 minutes; industry consensus is clear) |

**Recommended limits:**

| Dimension | Limit | Window | Rationale |
|-----------|-------|--------|-----------|
| Per phone number | 3 OTP sends | 10 minutes | Prevents targeted harassment of one number |
| Per IP address | 10 OTP sends | 10 minutes | Prevents bot farms hitting many numbers |
| Global cooldown | 60 seconds between resends to same number | Per request | Prevents rapid resend clicking |

**SMS pumping context:** In an SMS pumping attack, bots submit premium-rate phone numbers into OTP forms, generating charges. Prevention requires rate limiting, geographic controls, and monitoring send-to-verify ratios. For static OTP ("1111"), SMS pumping is not a cost risk yet, but the rate limiting infrastructure must be built now so it is in place when real SMS is enabled. (Source: TechTarget SMS pumping article)

**Implementation:** Reuse the existing `RateLimiter` Lua script pattern from `rate_limit.py`. Add new keys:
- `memora:ratelimit:otp:phone:{mobile}` - per-phone OTP send counter
- `memora:ratelimit:otp:ip:{ip}` - per-IP OTP send counter
- `memora:ratelimit:otp:cooldown:{mobile}` - 60s cooldown flag

**Response when rate limited:** HTTP 429 with `retry_after` seconds, same pattern as login rate limiting.

### TS-8: OTP Verification Attempt Limiting

| Aspect | Detail |
|--------|--------|
| **Feature** | Limit OTP guess attempts per pending registration or reset flow |
| **Why Expected** | A 4-digit OTP has only 10,000 combinations; without limits, brute-force is trivial |
| **Complexity** | Low |
| **Confidence** | HIGH (universal security requirement; Twilio enforces max 5 check attempts) |

**Behavior:**
- Max 3 incorrect OTP attempts per pending registration/reset
- After 3 failures: invalidate the OTP, delete the pending state, require starting over
- Attempt counter stored alongside OTP in Redis (part of the pending state JSON)
- On each failed attempt, return remaining attempts: `{ "error": "INVALID_OTP", "remaining_attempts": 2 }`

**Math:** With 4-digit OTP and 3 attempts, brute-force probability is 3/10,000 = 0.03%. Acceptable.

### TS-9: Session Invalidation on Password Change

| Aspect | Detail |
|--------|--------|
| **Feature** | All existing sessions invalidated when password changes (via reset or admin action) |
| **Why Expected** | OWASP mandates this; prevents stolen sessions from persisting after password reset |
| **Complexity** | Low |
| **Confidence** | HIGH (OWASP session management cheat sheet; existing SessionService supports this) |

**Behavior:**
- On password reset (step 3): call `session_service.invalidate_session(user_id)`
- On admin password change: Frappe hook triggers session invalidation via Redis
- All devices with old tokens get 401 on next API call, must re-login
- Device registrations are NOT cleared (devices are still "known", just logged out)

**Already built:** `SessionService.invalidate_session()` exists and deletes the Redis session key, which automatically invalidates all tokens tied to that family_id. This is the exact mechanism needed.

### TS-10: Separate Admin and Player Login Endpoints

| Aspect | Detail |
|--------|--------|
| **Feature** | Distinct endpoints for admin vs player authentication |
| **Why Expected** | Different auth mechanisms (Frappe User vs Player Profile); prevents confusion and security crossover |
| **Complexity** | Low |
| **Confidence** | HIGH (PRD specifies this; clean separation is standard practice) |

**Endpoint structure:**
- `POST /auth/player/login` - Phone + password, verifies against Player Profile
- `POST /auth/player/register` - Self-registration, sends OTP
- `POST /auth/player/register/verify` - Verify OTP, create account
- `POST /auth/player/register/resend` - Resend OTP with cooldown
- `POST /auth/player/password-reset/request` - Request password reset OTP
- `POST /auth/player/password-reset/verify` - Verify OTP, get reset token
- `POST /auth/player/password-reset/confirm` - Set new password
- `POST /auth/admin/login` - Email + password, verifies against Frappe User (existing flow)
- `POST /auth/refresh` - Token refresh (shared, works for both player and admin tokens)

**Migration note:** The current single `/auth/login` endpoint with `is_email()` detection must be replaced. The `is_email()` function, `lookup_user_by_mobile()`, and the Frappe session-based `verify_credentials()` for players are all removed. Admin login can retain the Frappe session-based verification flow.

---

## Differentiators

Features that improve the experience beyond baseline. Valuable but not blocking for launch.

### D-1: Enriched Login Response with Profile Data

| Aspect | Detail |
|--------|--------|
| **Feature** | Login response includes profile data (display_name, avatar, gender, XP) alongside tokens |
| **Value Proposition** | Eliminates separate profile fetch call; faster app launch |
| **Complexity** | Low (already built) |
| **Confidence** | HIGH |

**Status:** Already implemented in current `EnrichedTokenResponse`. Must be preserved. The new player login endpoint returns the same enriched response structure.

### D-2: Pending Registration with Phone Reservation

| Aspect | Detail |
|--------|--------|
| **Feature** | During OTP verification window, the phone number is "soft-reserved" to prevent race conditions |
| **Value Proposition** | Prevents frustrating scenario where two people try to register the same number simultaneously |
| **Complexity** | Low |
| **Confidence** | MEDIUM |

**Behavior:**
- When registration is requested but OTP not yet verified, store a reservation key in Redis: `memora:phone_reserved:{normalized_mobile}` with 10-min TTL
- If another registration attempt comes in for the same number while pending, return: "This phone number has a pending registration. Please wait or try again later."
- Reservation cleared on: OTP success (account created), OTP expiry (TTL), or 3 failed attempts

**Why not table stakes:** The UNIQUE constraint on the `mobile` field in MariaDB is the true guard against duplicate accounts. This reservation is a UX improvement to give better error messages during the OTP window.

### D-3: OTP Resend with Cooldown

| Aspect | Detail |
|--------|--------|
| **Feature** | Player can request OTP resend during registration/reset, with 60s cooldown |
| **Value Proposition** | Handles SMS delivery failures without starting the entire flow over |
| **Complexity** | Low |
| **Confidence** | HIGH |

**Behavior:**
- `POST /auth/player/register/resend` with `pending_id`
- `POST /auth/player/password-reset/resend` with `mobile`
- Generates NEW OTP (invalidates old one), resets attempt counter
- Enforces 60-second cooldown between resends
- Does NOT reset the overall 10-minute TTL of the pending state
- Returns `retry_after` seconds if cooldown active

### D-4: Leaderboard Privacy Protection

| Aspect | Detail |
|--------|--------|
| **Feature** | Leaderboards display `display_name`, never raw phone numbers |
| **Value Proposition** | Prevents exposing player phone numbers to other players |
| **Complexity** | Low |
| **Confidence** | HIGH (PRD identifies this as concern #8) |

**Behavior:**
- All leaderboard entries use `display_name` from Player Profile
- JWT `sub` (now phone number) is never exposed in any player-facing response
- Audit all endpoints that return `player_id` to ensure they map to `display_name`

### D-5: Admin Password Reset for Players

| Aspect | Detail |
|--------|--------|
| **Feature** | Admin can reset a player's password from Frappe Desk |
| **Value Proposition** | Support channel for players who cannot self-reset (lost phone, etc.) |
| **Complexity** | Low |
| **Confidence** | HIGH |

**Behavior:**
- Admin navigates to Player Profile in Frappe Desk
- Sets new password via Password field
- Frappe handles hashing into `__Auth` table automatically
- Frappe hook triggers session invalidation for that player
- Player must re-login with new password on next app open

### D-6: Graceful Migration for Existing Players

| Aspect | Detail |
|--------|--------|
| **Feature** | Existing email-based players are migrated to phone-based auth without data loss |
| **Value Proposition** | No player progress is lost; smooth transition |
| **Complexity** | Medium |
| **Confidence** | MEDIUM |

**Behavior:**
- Migration script:
  1. For each existing Player Profile, read `user` field (Frappe User email)
  2. Look up `mobile_no` from that Frappe User
  3. Set `mobile` field on Player Profile with normalized number
  4. Set a temporary password (or flag account for password reset on first login)
  5. Change `autoname` from `field:user` to `PLAYER-.#####.`
  6. Use `rename_doc()` to change docname from email to new PLAYER-XXXXX format
- Redis keys referencing old email must be flushed or migrated
- All linked records (subscriptions, wallets, progress) follow the rename

**Risk:** `rename_doc()` updates all linked records but is expensive and can fail on large datasets. Test thoroughly with production data volume.

---

## Anti-Features

Features to explicitly NOT build. Common mistakes in this domain that waste time or create problems.

### AF-1: Do NOT Build Country Code Picker / Auto-Detection

| Aspect | Detail |
|--------|--------|
| **Anti-Feature** | Country code dropdown, auto-detection by IP, or phone number library (libphonenumber) |
| **Why Avoid** | Adds complexity for a known audience (Saudi Arabia + Jordan). Players know their own numbers. PRD explicitly states "user enters full number." |
| **What to Do Instead** | Accept digits-only input. Strip non-digit characters. If number starts with `0`, apply default country code (configurable, default `966`). Otherwise use as-is. Validate length (10-15 digits). |

### AF-2: Do NOT Build Real SMS Gateway in v1

| Aspect | Detail |
|--------|--------|
| **Anti-Feature** | Integrating Twilio/Vonage/local SMS provider for OTP delivery |
| **Why Avoid** | Project context specifies static "1111" OTP for now. SMS integration adds cost, vendor dependency, delivery reliability concerns, and regulatory requirements. Build the OTP verification logic correctly first; SMS provider is a pluggable backend to add later. |
| **What to Do Instead** | Use a `send_otp()` function that logs the OTP and returns success. Make it pluggable (strategy pattern or config flag) so swapping in a real SMS provider later requires changing one module, not the entire flow. Log OTP to structured logger in dev/staging. NEVER log OTP in production once real SMS is enabled. |

### AF-3: Do NOT Build Phone Number Change Flow in v1

| Aspect | Detail |
|--------|--------|
| **Anti-Feature** | Allowing players to change their phone number via the app |
| **Why Avoid** | Phone number change requires: OTP verification of NEW number, session invalidation, Redis key migration, potential `rename_doc()` if phone is the docname. High complexity for a rare use case. PRD recommends `PLAYER-.#####.` autoname specifically to decouple identity from phone number. |
| **What to Do Instead** | Admin-only phone number change via Frappe Desk for v1. Build the DocType with `PLAYER-.#####.` autoname so phone changes are just a field update, not a document rename. |

### AF-4: Do NOT Build Email Fallback Authentication

| Aspect | Detail |
|--------|--------|
| **Anti-Feature** | Keeping email as an alternative login method for players |
| **Why Avoid** | The entire point of this migration is to remove Frappe User dependency for players. Supporting both email and phone creates two auth paths, two verification flows, and doubles the security surface. Players are mobile-first Arabic students who have phones, not email accounts. |
| **What to Do Instead** | Clean break: players use phone only. Admins use email only. The `is_email()` detection logic is removed entirely. |

### AF-5: Do NOT Build TOTP/Authenticator App Support

| Aspect | Detail |
|--------|--------|
| **Anti-Feature** | Adding Google Authenticator or similar TOTP as a second factor |
| **Why Avoid** | Target audience is students on mobile phones. TOTP requires installing a separate app, understanding QR codes, and managing recovery codes. SMS OTP is the right level of security for this audience. Adding TOTP creates support burden with zero user demand. Research shows SMS OTP, while weaker than TOTP against sophisticated attacks, is the appropriate tradeoff for accessibility in educational platforms. |
| **What to Do Instead** | Phone ownership verification via SMS OTP at registration is sufficient. The 3-device limit + session family_id provides additional security layers. |

### AF-6: Do NOT Build Password Expiry or Password History

| Aspect | Detail |
|--------|--------|
| **Anti-Feature** | Forcing password rotation or preventing password reuse |
| **Why Avoid** | NIST 800-63B and modern OWASP guidance explicitly recommend AGAINST periodic password expiry. It leads to weaker passwords (users just increment a number). Password history checks add complexity for minimal security benefit. |
| **What to Do Instead** | Allow passwords to live indefinitely. Enforce strong passwords via length requirement (8+ chars). If a breach is detected, force reset via admin action. |

### AF-7: Do NOT Build CAPTCHA for OTP Requests

| Aspect | Detail |
|--------|--------|
| **Anti-Feature** | Adding CAPTCHA/reCAPTCHA before OTP send |
| **Why Avoid** | This is a native mobile app, not a web form. CAPTCHA is a poor experience in mobile apps, especially for young Arabic-speaking students. The rate limiting (TS-7) provides sufficient bot protection for the expected scale. |
| **What to Do Instead** | Rely on rate limiting (per-phone + per-IP) and device fingerprinting. If SMS pumping becomes a real problem at scale, consider invisible reCAPTCHA v3 or device attestation (SafetyNet/App Attest) as a future enhancement. |

### AF-8: Do NOT Build "Remember Me" or Biometric Login in Auth Layer

| Aspect | Detail |
|--------|--------|
| **Anti-Feature** | Server-side "remember me" toggle or biometric auth handling in the API |
| **Why Avoid** | The 30-day refresh token already provides "remember me" behavior. Biometric unlock (Face ID, fingerprint) is a client-side concern -- the mobile app stores the refresh token in secure storage and unlocks it with biometrics. The server never sees biometric data. |
| **What to Do Instead** | Keep the 30-day refresh token. Client-side biometric unlock is the mobile team's responsibility. Document this for the mobile team so they know the server contract is unchanged. |

---

## Feature Dependencies

```
TS-2 (Phone Normalization)
  |
  v
TS-3 (Phone Uniqueness) ------> TS-4 (Registration + OTP)
  |                                 |
  |                                 +--> TS-7 (OTP Send Rate Limiting)
  |                                 +--> TS-8 (OTP Verify Attempt Limiting)
  |                                 +--> D-2 (Phone Reservation)
  |                                 +--> D-3 (OTP Resend)
  |
  v
TS-1 (Phone Login) ---------------> TS-5 (Password Policy)
  |                                 |
  |                                 v
  |                               TS-6 (Password Reset + OTP)
  |                                 |
  |                                 +--> TS-9 (Session Invalidation)
  |
  v
TS-10 (Separate Endpoints) -------> D-1 (Enriched Login Response)
                                    D-4 (Leaderboard Privacy)
                                    D-5 (Admin Password Reset)

Independent (execute last):
  D-6 (Migration Script) -- depends on everything above being built first
```

**Key dependency insight:** Phone normalization (TS-2) is the foundation. Everything else depends on having a consistent, normalized phone number format. Build and test this first.

---

## MVP Recommendation

For the first usable version of phone+password auth, prioritize in this order:

### Must Ship (Phase 1 - Core Auth)

1. **TS-2** Phone Number Normalization -- foundation for everything
2. **TS-3** Phone Number Uniqueness -- database constraint
3. **TS-5** Password Policy -- validation logic
4. **TS-1** Phone Login -- core login endpoint
5. **TS-10** Separate Endpoints -- clean endpoint structure
6. **D-1** Enriched Login Response -- already built, just preserve it

### Must Ship (Phase 2 - Registration + OTP)

7. **TS-4** Self-Registration with OTP -- pending state in Redis
8. **TS-7** OTP Send Rate Limiting -- MUST ship with OTP
9. **TS-8** OTP Verify Attempt Limiting -- MUST ship with OTP
10. **D-2** Phone Reservation -- ship with registration

### Must Ship (Phase 3 - Password Reset + Migration)

11. **TS-6** Password Reset via OTP -- 3-step flow
12. **TS-9** Session Invalidation on Password Change -- security requirement
13. **D-3** OTP Resend -- quality of life
14. **D-5** Admin Password Reset -- support channel
15. **D-4** Leaderboard Privacy -- audit and fix before go-live
16. **D-6** Migration Script -- final step before go-live

### Defer to Post-Launch

- All anti-features above are explicitly excluded
- Real SMS provider integration (replace static "1111")
- Phone number change flow (admin-only for now)
- Common password list checking
- SMS delivery monitoring/analytics

---

## Detailed Behavior Specifications

### Error Response Patterns

All auth endpoints should follow a consistent error response format:

```json
{
  "detail": {
    "code": "ERROR_CODE",
    "message": "Human-readable message (Arabic-friendly)"
  }
}
```

**Error codes for registration:**

| Code | HTTP Status | When |
|------|-------------|------|
| `PHONE_INVALID_FORMAT` | 400 | Phone number fails validation after normalization |
| `PASSWORD_TOO_SHORT` | 400 | Password below minimum length |
| `MISSING_REQUIRED_FIELD` | 400 | Required profile field missing |
| `OTP_RATE_LIMITED` | 429 | Too many OTP requests |
| `PHONE_PENDING_REGISTRATION` | 409 | Phone has pending OTP verification |
| `INVALID_OTP` | 401 | Wrong OTP code |
| `OTP_EXPIRED` | 401 | Pending state expired (10 min TTL) |
| `OTP_MAX_ATTEMPTS` | 401 | 3 failed OTP attempts, must restart flow |
| `REGISTRATION_FAILED` | 500 | Frappe document creation failed |

**Error codes for login:**

| Code | HTTP Status | When |
|------|-------------|------|
| `INVALID_CREDENTIALS` | 401 | Wrong phone/password (generic, no enumeration) |
| `DEVICE_ID_REQUIRED` | 400 | Missing X-Device-ID header |
| `DEVICE_LIMIT_EXCEEDED` | 429 | 3-device limit reached |
| `NO_PLAN_ASSIGNED` | 401 | Player profile has no plan |
| `LOGIN_RATE_LIMITED` | 429 | Too many login attempts |

**Error codes for password reset:**

| Code | HTTP Status | When |
|------|-------------|------|
| `OTP_RATE_LIMITED` | 429 | Too many OTP requests |
| `INVALID_OTP` | 401 | Wrong OTP code |
| `OTP_EXPIRED` | 401 | Reset OTP expired |
| `OTP_MAX_ATTEMPTS` | 401 | 3 failed attempts, must restart |
| `INVALID_RESET_TOKEN` | 401 | Reset token invalid or expired |
| `PASSWORD_TOO_SHORT` | 400 | New password below minimum length |

### Redis Key Patterns for OTP State

| Key | Value | TTL | Purpose |
|-----|-------|-----|---------|
| `memora:pending:{token}` | JSON: `{mobile, password_hash, display_name, avatar, grade, major, season, otp, attempts, created_at}` | 600s (10 min) | Pending registration |
| `memora:phone_reserved:{mobile}` | `1` | 600s (10 min) | Prevent duplicate pending registrations |
| `memora:reset:{mobile}` | JSON: `{otp, attempts, created_at}` | 600s (10 min) | Password reset OTP state |
| `memora:reset_token:{token}` | mobile number string | 300s (5 min) | Verified reset token (between step 2 and 3) |
| `memora:ratelimit:otp:phone:{mobile}` | counter (integer) | 600s (10 min) | OTP send rate limit per phone |
| `memora:ratelimit:otp:ip:{ip}` | counter (integer) | 600s (10 min) | OTP send rate limit per IP |
| `memora:ratelimit:otp:cooldown:{mobile}` | `1` | 60s | Resend cooldown flag |

### OTP Provider Interface

Design the OTP sending as a pluggable interface from day one:

```python
class OTPProvider(Protocol):
    async def send_otp(self, mobile: str, otp: str) -> bool:
        """Send OTP to mobile number. Returns True on success."""
        ...

class StaticOTPProvider:
    """Development provider - always uses static OTP, logs instead of sending."""
    async def send_otp(self, mobile: str, otp: str) -> bool:
        logger.info("otp_generated", mobile=mobile[-4:], otp_length=len(otp))
        return True

class TwilioOTPProvider:
    """Production provider - sends via Twilio Verify. (Future)"""
    ...
```

**Configuration:** Select provider via environment variable `OTP_PROVIDER=static|twilio`.

---

## Sources

### HIGH Confidence (Official Documentation, Codebase, Standards)
- Frappe Password fieldtype and `__Auth` table: [Field Types - Frappe Docs](https://docs.frappe.io/framework/user/en/basics/doctypes/fieldtypes)
- Frappe `password.py` source: [frappe/frappe/utils/password.py](https://github.com/frappe/frappe/blob/develop/frappe/utils/password.py)
- OWASP Forgot Password Cheat Sheet: [OWASP](https://cheatsheetseries.owasp.org/cheatsheets/Forgot_Password_Cheat_Sheet.html)
- OWASP Authentication Cheat Sheet: [OWASP](https://cheatsheetseries.owasp.org/cheatsheets/Authentication_Cheat_Sheet.html)
- OWASP Session Management Cheat Sheet: [OWASP](https://cheatsheetseries.owasp.org/cheatsheets/Session_Management_Cheat_Sheet.html)
- Twilio OTP Rate Limits and Timeouts: [Twilio Verify Docs](https://www.twilio.com/docs/verify/api/rate-limits-and-timeouts)
- Twilio Developer Best Practices: [Twilio Verify Best Practices](https://www.twilio.com/docs/verify/developer-best-practices)
- E.164 Phone Format - Saudi Arabia: [sent.dm/resources/sa](https://www.sent.dm/resources/sa)
- E.164 Phone Format - Jordan: [sent.dm/resources/jo](https://www.sent.dm/resources/jo)
- Existing codebase: `fastapi_app/api/v1/endpoints/auth.py`, `services/rate_limit.py`, `services/session.py`, `services/device.py`, `services/frappe.py`
- PRD: `.planning/prd/mobile-auth-migration.md`

### MEDIUM Confidence (Verified with Multiple Sources)
- OTP rate limiting patterns: [Unkey Blog - Ratelimiting OTP](https://www.unkey.com/blog/ratelimiting-otp), confirmed by Twilio documentation
- SMS pumping attack prevention: [TechTarget](https://www.techtarget.com/searchsecurity/feature/SMS-pumping-attacks-and-how-to-mitigate-them)
- Pending registration state (no token before OTP verification): [Medium - Broken OTP](https://medium.com/@shamveelkhilji/broken-otp-why-issuing-a-full-token-at-signup-is-a-security-bug-and-how-to-fix-it-3ed99a2e18b8), confirmed by [LoginRadius Redis+OTP](https://www.loginradius.com/blog/engineering/guest-post/multi-factor-authentication-using-redis-cache-and-otp)
- OTP lifecycle states: [Medium - OTP Lifecycle](https://medium.com/@jayashri.shinde1795/understanding-the-lifecycle-and-status-codes-of-otp-verification-b74b83557b7e)
- OWASP ASVS password recommendations: [Medium - CWE-521](https://medium.com/@pavusa/secure-authentication-done-right-owasp-asvs-v4-0-3-cwe-521-weak-password-requirements-97bd38923be4)

### LOW Confidence (Single Source, Needs Validation if Critical)
- NIST 800-63B recommendation against password expiry (widely referenced but not directly verified from NIST document in this research session)
- Google Identity Platform test phone numbers pattern for development OTP testing

---

*Research complete. Ready for requirements definition and implementation planning.*
