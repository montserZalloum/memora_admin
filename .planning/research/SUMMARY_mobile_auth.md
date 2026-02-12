# Mobile-First Player Authentication Research Summary

**Project:** Memora Admin - v2.0 Mobile-First Player Authentication
**Domain:** Phone+password authentication migration on Frappe+FastAPI dual architecture
**Researched:** 2026-02-12
**Confidence:** HIGH

## Executive Summary

This migration transitions Memora's player authentication from Frappe User-based email login to phone+password authentication on the custom Player Profile DocType. The research reveals that **no new dependencies are needed** — Frappe's `__Auth` table and password hashing infrastructure already support custom DocTypes via `check_password(doctype, name, fieldname)`. The key technical insight is that Frappe's Password fieldtype uses reversible Fernet encryption (not suitable for auth), so we must bypass it and call `update_password()` / `check_password()` directly to get proper PBKDF2-SHA256 hashing.

The recommended approach uses `autoname: PLAYER-.#####.` (stable docname) with a separate unique `mobile` field, avoiding phone-as-docname which would force `rename_doc()` cascades on phone number changes. The architecture shifts from 4 HTTP round-trips per login (Frappe session creation/destruction) to a single `allow_guest=True` whitelisted API call. JWT `sub` becomes the stable docname (e.g., `PLAYER-00001`), not the phone number, making all downstream services identity-agnostic — they already treat `user.sub` as an opaque string.

The critical risk is **Redis identity split during migration**. Every Redis key is keyed by `user.sub` (currently email, future docname). Changing identity mid-flight orphans all player data: access grants, progress bitmaps, wallets, leaderboard entries. Prevention requires: (1) adding `mobile` field without removing `user` field initially, (2) Redis key migration script BEFORE JWT switch, (3) flushing dirty sets before migration, (4) testing with production data volume. The second major risk is event handlers and Frappe APIs that reference `doc.user` — these must be updated to `doc.name` atomically before the `user` field is removed.

## Key Findings

### Recommended Stack

**From STACK.md:** Zero new dependencies required. Frappe bench already includes `passlib 1.7.4` for PBKDF2-SHA256 hashing, and the `__Auth` table supports custom DocType passwords via the `doctype` parameter. FastAPI sidecar already has `httpx` for calling Frappe APIs, Redis client for OTP storage, and `PyJWT` for token creation. Phone validation needs only a regex — no `python-phonenumbers` library (overkill for Saudi/Jordan audience).

**Core technologies:**
- **Frappe `__Auth` table + password.py module**: Password hashing/verification for custom DocTypes — verified with live runtime test showing `check_password("PLAYER-00001", pwd, doctype="Memora Player Profile", fieldname="password")` works correctly with PBKDF2-SHA256 hashing
- **Redis with TTL**: OTP storage (`memora:otp:{type}:{mobile}`, 5-min TTL) and temp tokens (`memora:reset_token:{mobile}`, 10-min TTL) — already established pattern in codebase
- **Frappe whitelisted APIs with `allow_guest=True`**: Bridge for FastAPI to call password verification without creating Frappe sessions — eliminates 3 of 4 HTTP calls per login
- **Regex-based phone normalization**: `^\d{9,15}$` after stripping non-digits — sufficient for known audience, avoids libphonenumber complexity

**Critical insight (from live verification):** The Password fieldtype in Frappe uses `set_encrypted_password()` which stores with Fernet encryption (`encrypted=1` flag in `__Auth`). This is reversible and incompatible with `check_password()` which only queries `encrypted=0` rows. Solution: add `flags.ignore_save_passwords = ["password"]` in Player Profile's `__setup__()` (mimicking User DocType pattern) and hash manually via `update_password()` in `after_insert()` / `on_update()` hooks.

### Expected Features

**From FEATURES.md:** The domain is well-understood with clear consensus on table stakes. Key features break into 3 tiers.

**Must have (table stakes):**
- **TS-1: Phone+password login** with single-call Frappe API (no session creation) — replaces 4-call Frappe session flow
- **TS-2: Phone normalization** enforced at both FastAPI and Frappe layers — prevents duplicate accounts with different formatting
- **TS-3: Phone uniqueness** via UNIQUE constraint on `mobile` field — database-level race condition protection
- **TS-4: Self-registration with OTP** using Redis pending state — OTP gates account creation, not cosmetic after token issuance
- **TS-5: Password policy** (8+ chars, all Unicode allowed, no complexity requirements) — OWASP ASVS guidance
- **TS-6: Password reset (3-step OTP flow)** — request OTP, verify OTP (get reset_token), set new password
- **TS-7: OTP send rate limiting** (3/phone/10min, 10/IP/10min, 60s resend cooldown) — prevents SMS pumping
- **TS-8: OTP verification attempt limiting** (max 3 attempts per OTP) — prevents brute force on 6-digit code
- **TS-9: Session invalidation on password change** — OWASP mandates this for security
- **TS-10: Separate /auth/player/* and /auth/admin/* endpoints** — clean separation of auth mechanisms

**Should have (competitive):**
- **D-1: Enriched login response** with profile+XP data — already built, preserve pattern
- **D-2: Phone reservation during OTP window** — prevents race conditions with better UX
- **D-3: OTP resend with cooldown** — handles SMS delivery failures
- **D-4: Leaderboard privacy** — display_name only, never expose phone numbers
- **D-5: Admin password reset** for players — support channel via Frappe Desk

**Defer (v2+):**
- Real SMS gateway integration (static "1111" OTP for v1)
- Phone number change flow (admin-only for now)
- TOTP/authenticator app (unnecessary for target audience)
- CAPTCHA on OTP requests (rate limiting sufficient)

### Architecture Approach

**From ARCHITECTURE.md:** The migration transforms a session-heavy flow (4 HTTP calls per login: `login` → `get_logged_user` → fetch User → fetch Profile → `logout`) into a stateless single-call pattern. The new Frappe whitelisted API `verify_player_password()` receives phone+password, normalizes phone, looks up Player Profile by `mobile` field to get docname, calls `check_password()` with the docname (not phone), and returns profile data in the same response.

**Major components:**
1. **Frappe auth API** (`memora_admin/api/auth.py`) — 3 whitelisted methods: `verify_player_password()`, `register_player()`, `set_player_password()` — all `allow_guest=True` except set_player_password
2. **Player Profile DocType changes** — add `mobile` (unique, required) and `password` fields, change autoname to `PLAYER-.#####.`, implement `validate()` hook with phone normalization and password policy
3. **FastAPI PlayerAuthService** — replaces `FrappeAuthService.verify_credentials()` for players, calls new Frappe API via httpx (separate guest client, not FrappeClient)
4. **OTP service** — Redis-backed with Lua scripts for atomic verify-and-consume, pluggable provider interface (StaticOTPProvider for v1, TwilioOTPProvider for future)
5. **Event handler updates** — 4 files reference `doc.user` or `player_doc.user` (access_sync, device_sync, plan_change_sync, profile_sync) — must change to `doc.name` before removing `user` field

**Key pattern (dependency injection fix):** `deps.py` must inject `FrappeClient` into `AccessService` and `ProgressService` so `ensure_hydrated()` can fetch from MariaDB on Redis cache miss. Without this, hydration silently fails (logs warning but proceeds with empty data).

**Data flow insight:** JWT `sub` = Player Profile docname (e.g., `PLAYER-00001`), NOT phone number. This makes identity stable even if phone changes. Phone number goes in separate `mobile` claim for display purposes. All downstream services (sessions, progress, wallet, access) already use `user.sub` as an opaque string, so no changes needed beyond updating the identity value in JWT.

### Critical Pitfalls

**From PITFALLS.md (17 total, top 5 listed):**

1. **Redis key identity split during migration** (Critical) — Existing keys use email (`memora:access:ahmed@example.com`), new keys use docname (`memora:access:PLAYER-00001`). Orphans all player data: access grants, progress, wallets, leaderboards. Prevention: (a) add `mobile` field without removing `user` initially, (b) write Redis key migration script, (c) flush dirty progress/wallet sets first, (d) switch JWT `sub` only after migration complete, (e) clean up old keys after all players re-login. **Detection:** `redis-cli KEYS "memora:*@*"` shows orphaned email-keyed entries.

2. **Player Profile docname change breaks MariaDB foreign keys** (Critical) — Changing autoname from `field:user` to `PLAYER-.#####.` orphans all child records (subscriptions, progress, wallet, transactions, logs). Every `Link` field stores the old docname. Prevention: Use Frappe's `rename_doc()` API which cascades updates, OR keep dual-autoname (existing profiles keep email docnames, new profiles get PLAYER-#####). **Detection:** `SELECT COUNT(*) FROM tabMemora Player Wallet WHERE player NOT IN (SELECT name FROM tabMemora Player Profile)` — non-zero means broken FKs.

3. **Frappe `__Auth` table keying mismatch** (Critical) — `check_password(user, pwd, doctype, fieldname)` where `user` must be the document name in `__Auth`, not the phone number. Login API must: (a) receive phone, (b) lookup Player Profile by `mobile` field to get docname, (c) call `check_password(docname, pwd, ...)`. Passing phone directly causes all logins to fail with `AuthenticationError`. **Detection:** First login attempt fails with "Incorrect User or Password" despite correct credentials.

4. **Event hooks reference removed `doc.user` field** (Critical) — 5 files have `doc.user` or `player_doc.user` references. If `user` field is removed before handlers are updated, events fire and silently fail (Frappe catches exceptions in doc_events). Subscription grants not synced to Redis, session invalidation skipped, profile cache uses wrong key. **Prevention:** Update ALL handlers to use `doc.name` BEFORE removing `user` field from schema. Deploy handler code changes first, then `bench migrate`, then restart workers. **Detection:** `grep -rn "\.user\b" memora_admin/events/` before removing field, and check Redis for keys containing `None`: `redis-cli KEYS "memora:*None*"`.

5. **JWT `sub` change invalidates all existing tokens** (Moderate but high impact) — Every outstanding access/refresh token has `sub` set to email. After migration, new logins issue tokens with `sub` set to docname or phone. Old tokens still verify (signature valid) but identity they carry maps to nothing. **Prevention:** Rotate JWT secret at migration time (instantly invalidates all tokens, forces re-login) OR add `token_version` claim and increment at migration. Communicate forced re-login to users.

## Implications for Roadmap

Based on research, suggested 5-phase structure with clear dependency chain:

### Phase 1: DocType Foundation
**Rationale:** Schema is the foundation — everything depends on it. Cannot write Frappe auth API without password field existing, cannot test phone normalization without mobile field.
**Delivers:** Modified Player Profile DocType with `mobile` (unique) and `password` fields, `PLAYER-.#####.` autoname, `validate()` hook with phone normalization, password policy validation.
**Addresses:** TS-2 (phone normalization), TS-3 (phone uniqueness), TS-5 (password policy)
**Avoids:** Pitfall 3 (incorrect Password fieldtype usage) by implementing `flags.ignore_save_passwords` pattern
**Key decision:** KEEP `user` field temporarily (nullable) for backward compatibility. Remove in Phase 5.
**Research flags:** Standard DocType patterns, no deeper research needed.

### Phase 2: Frappe Auth API Bridge
**Rationale:** FastAPI endpoints cannot be built without the Frappe APIs they call. This phase creates the bridge between FastAPI and Frappe for player auth.
**Delivers:** `memora_admin/api/auth.py` with 3 whitelisted methods: `verify_player_password()`, `register_player()`, `set_player_password()`. All with `allow_guest=True` (except set_player_password which needs API key auth).
**Addresses:** TS-1 (phone login backend), part of TS-4 (registration backend)
**Uses:** Frappe `check_password()` and `update_password()` from `frappe.utils.password`
**Avoids:** Pitfall 3 (__Auth keying) by doing mobile→docname lookup BEFORE calling check_password
**Testing:** Test independently via curl against Frappe (no FastAPI needed yet)
**Research flags:** No research needed — Frappe password API verified in STACK.md.

### Phase 3: FastAPI Auth Endpoints + OTP
**Rationale:** Now that backend APIs exist, build the client-facing endpoints. OTP service is critical for registration and password reset.
**Delivers:**
- `PlayerAuthService` (calls new Frappe API via httpx)
- `OTPService` (Redis-backed, Lua scripts for atomic verify-consume)
- `/auth/player/login`, `/auth/player/register`, `/auth/player/register/verify`
- `/auth/player/password-reset/request`, `/auth/player/password-reset/verify`, `/auth/player/password-reset/confirm`
- Updated JWT `create_access_token()` (email optional, mobile optional)
- Updated `TokenPayload` (email and mobile both optional)
**Addresses:** TS-1 (phone login), TS-4 (registration with OTP), TS-6 (password reset), TS-7 (OTP rate limiting), TS-8 (OTP attempt limiting), D-3 (OTP resend)
**Avoids:** Pitfall 11 (OTP stub risk) by env-controlled OTP_MODE flag
**Implements:** Two-step registration pattern (request OTP → verify OTP + create account) per FEATURES.md security guidance
**Research flags:** No research needed — OTP patterns well-documented in FEATURES.md.

### Phase 4: Event Handler + API Migration
**Rationale:** Once new auth is working, update all code that references the old identity model. This is the bulk migration work.
**Delivers:**
- Update 4 event handlers: `access_sync.py`, `device_sync.py`, `plan_change_sync.py`, `profile_sync.py` (change `doc.user` → `doc.name`)
- Update 4 Frappe APIs: `purchase.py`, `profile.py`, `subscriptions.py`, `devices.py` (change `{"user": player_id}` → direct name lookup)
- Fix Redis client bug in `plan_change_sync.py` and `profile_sync.py` (use `get_fastapi_redis()` instead of `frappe.cache()`)
- Remove fallback lookup code in `subscriptions.py` (`{"user": player_id}` queries)
**Addresses:** TS-9 (session invalidation — plan_change_sync fix enables this)
**Avoids:** Pitfall 4 (event handler silent failures), Pitfall 5 (wrong Redis client), Pitfall 6 (hardcoded email lookup), Pitfall 10 (purchase flow breakage)
**Testing critical:** After this phase, test: (a) subscription change → session invalidated, (b) purchase flow, (c) profile update sync, (d) device removal sync
**Research flags:** No research needed — codebase audit complete in ARCHITECTURE.md.

### Phase 5: Migration + Cleanup
**Rationale:** Irreversible changes. Must only happen after full validation of Phases 1-4 in production-like environment.
**Delivers:**
- Data migration script: populate `mobile` from existing Frappe User `mobile_no`, decide on rename_doc vs dual-autoname
- Redis key migration script: copy all `memora:*:email` keys to `memora:*:PLAYER-#####` format
- Remove `user` field from Player Profile schema
- Rename `/auth/login` → `/auth/admin/login`
- Remove player-specific methods from old `FrappeAuthService`
- Delete orphaned Frappe User records for players
- Rotate JWT secret (forces all players to re-login)
**Addresses:** D-6 (graceful migration), D-4 (leaderboard privacy — audit in this phase), D-5 (admin password reset — verify works after migration)
**Avoids:** Pitfall 1 (Redis identity split) by running Redis migration script before JWT switch, Pitfall 2 (FK breakage) by using rename_doc or dual-autoname strategy, Pitfall 7 (JWT invalidation) by rotating secret
**Critical:** Flush dirty:progress and dirty:wallets sets BEFORE migration. Test migration script on production data snapshot.
**Research flags:** No research needed — migration patterns documented in PITFALLS.md.

### Phase Ordering Rationale

- **Schema first (Phase 1)** because Frappe API cannot be written without the fields existing on the DocType
- **Frappe API second (Phase 2)** because FastAPI endpoints call these APIs — cannot build client without server
- **FastAPI third (Phase 3)** because authentication endpoints are the primary user-facing interface
- **Event handlers fourth (Phase 4)** because they depend on the new auth working but can run in parallel with old `user` field still present (no data loss risk)
- **Migration last (Phase 5)** because it is irreversible — requires all prior phases tested and working

This ordering avoids the circular dependency trap: if we tried to remove `user` field in Phase 1, all event handlers and APIs would break before we have replacements. By keeping `user` field through Phase 4, the old system continues working while the new system is being built.

### Research Flags

**Phases with standard patterns (skip /gsd:research-phase):**
- **Phase 1:** DocType schema changes — established Frappe patterns, well-documented
- **Phase 2:** Whitelisted API creation — Frappe decorator pattern, already used extensively
- **Phase 3:** FastAPI endpoints + OTP — existing auth.py provides the pattern, OTP via Redis is established
- **Phase 4:** Code search-and-replace — mechanical work, no unknowns
- **Phase 5:** Data migration — Frappe `rename_doc()` is well-documented, Redis key patterns clear

**No phases need deeper research.** All patterns are either established in the codebase or verified in the 4 research files.

## Confidence Assessment

| Area | Confidence | Notes |
|------|------------|-------|
| Stack | HIGH | Live runtime test verified check_password() with custom DocType. All dependencies already in bench. Zero external libraries needed. |
| Features | HIGH | Well-understood domain (phone auth), OWASP cheat sheets provide clear guidance, Twilio docs confirm OTP patterns. |
| Architecture | HIGH | Full codebase audit completed (15+ files). Frappe password.py source code reviewed. Data flow verified. |
| Pitfalls | HIGH | All 17 pitfalls derived from line-by-line code analysis. Redis key patterns mapped. Event handler dependencies traced. |

**Overall confidence:** HIGH

### Gaps to Address

**Minimal gaps identified:**

- **Phone number change flow** — deferred to post-v1, but the `PLAYER-.#####.` autoname recommendation mitigates most risk (phone change is just a field update, not a docname change, so no Redis key migration needed)
- **SMS delivery monitoring** — static "1111" OTP in v1 means no SMS delivery issues, but when real SMS is added, need provider-specific retry/monitoring logic. Pluggable OTPProvider interface from FEATURES.md prepares for this.
- **Migration data volume testing** — `rename_doc()` performance on large datasets unknown. Recommendation: test migration script on production data snapshot before go-live. Alternative: use dual-autoname strategy (existing profiles keep email docnames, new profiles get PLAYER-#####) to avoid rename entirely.

**How to handle:**
- Phase 5 planning should include a decision point: `rename_doc()` vs dual-autoname based on actual player count at migration time
- Add structured logging to OTPService now (even with static stub) so SMS delivery monitoring plugs in seamlessly later
- Phone number change flow can be admin-only for v1 (update `mobile` field in Frappe Desk) — player-facing UI deferred to v2.1

## Sources

### Primary (HIGH confidence)
- **STACK.md** — Frappe password.py source code review, live runtime test on x.conanacademy.com, __Auth table DDL verification
- **ARCHITECTURE.md** — Full codebase audit (15 files), Frappe password API signature verification, data flow tracing
- **FEATURES.md** — OWASP Authentication Cheat Sheet, OWASP Forgot Password Cheat Sheet, OWASP ASVS, Twilio Verify docs, E.164 phone format standards
- **PITFALLS.md** — Line-by-line code analysis of all affected files, Frappe Field Types documentation, Redis key pattern analysis

### Secondary (MEDIUM confidence)
- OTP rate limiting patterns — Unkey Blog + Twilio documentation cross-reference
- SMS pumping attack prevention — TechTarget article
- Pending registration state pattern — Medium articles on broken OTP vulnerabilities + LoginRadius Redis+OTP guide

### Tertiary (LOW confidence)
- None — all findings verified with multiple sources or direct code inspection

---

*Research completed: 2026-02-12*
*Ready for roadmap: yes*
*Next step: Roadmap creation using phase suggestions above*
