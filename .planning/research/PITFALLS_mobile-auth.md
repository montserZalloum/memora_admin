# Domain Pitfalls: Mobile-First Player Authentication Migration

**Domain:** Phone+password auth migration on existing production Frappe+FastAPI system
**Researched:** 2026-02-12
**Overall confidence:** HIGH (based on direct codebase analysis of all affected files)

---

## Critical Pitfalls

Mistakes that cause data loss, broken authentication, or require rollback.

---

### Pitfall 1: Redis Key Identity Split During Migration Window

**What goes wrong:** During the transition from email-based to phone-based `user.sub`, existing Redis keys are keyed by email (e.g., `memora:access:ahmed@example.com`) while new logins produce keys under phone numbers (e.g., `memora:access:966512345678`). Players who had active sessions, progress, access grants, wallets, and leaderboard rankings under email-keyed Redis entries lose all of that data silently. The system does not crash -- it simply sees "no data" and either hydrates fresh (losing in-flight dirty data) or returns empty results.

**Why it happens:** The PRD states "No code changes needed -- these are just string concatenations with `user.sub`." This is technically correct but catastrophically misleading. The *code* does not change, but the *identity value* feeding into the code changes. Every Redis key created before migration uses email; every key after uses phone. There is no bridge.

**Concrete impact surface (verified in codebase):**

| Redis Key Pattern | File That Writes It | Consequence of Orphaned Key |
|---|---|---|
| `memora:session:{email}` | `session.py:43` | Player appears logged out, forced re-login (acceptable) |
| `memora:access:{email}` | `access_sync.py:114` | Access grants invisible; player locked out of paid content |
| `memora:progress:{email}:{subj}:v{ver}` | `progress.py:50` | Progress bitmap lost; completion percentage resets to 0 |
| `memora:wallet:{email}` | `wallet.py:112` | XP and streak lost until hydration re-pulls from MariaDB |
| `memora:devices:{email}` | `device.py:16` | Device registry lost; can re-register but history gone |
| `memora:profile:{email}` | `profile_sync.py:29` | Profile cache miss; re-fetched from MariaDB (low impact) |
| `memora:stats:{email}:{subj}:v{ver}` | `sessions.py:315` | Stats hash lost; recomputed from bitmap (but bitmap is ALSO orphaned) |
| Leaderboard sorted sets | `leaderboard.py:343-371` | Player's leaderboard entries are under old email; new entries under phone number. Player appears twice or loses ranking entirely |

**Dirty data risk:** The `dirty:progress` and `dirty:wallets` sets (in `sync.py`) contain `player_id` values that the sync task uses to look up MariaDB records. If the identity changes mid-flight:
- `sync_dirty_progress` (line 139) queries `{"player": user_id, "subject": subject_id}` -- if `user_id` was email but MariaDB player field has been updated, the lookup fails and dirty progress is never persisted.
- `sync_dirty_wallets` (line 226) queries `{"player": player_id}` with the same problem.

**Prevention:**
1. **Phase 1: Add `mobile` field and new auth WITHOUT removing `user` field.** Run both identity systems in parallel. New logins get phone-based JWT `sub`, but event hooks still reference `doc.user` which still exists.
2. **Phase 2: Write a Redis key migration script** that, for each existing player, copies all Redis keys from `email` pattern to the new identity pattern (using the new docname). Run this BEFORE switching the JWT `sub` claim.
3. **Phase 3: Flush dirty sets first.** Before ANY migration step, ensure `dirty:progress` and `dirty:wallets` are empty (wait for sync task to complete, or run `sync_dirty_progress()` and `sync_dirty_wallets()` manually).
4. **Phase 4: Switch JWT `sub`** to use the new Player Profile docname (e.g., `PLAYER-00001`).
5. **Phase 5: Clean up old email-keyed Redis entries** after confirming all players have re-logged.

**Detection:** After migration, run `redis-cli KEYS "memora:*@*"` to find orphaned email-keyed entries. If any exist with recent TTLs or data, migration was incomplete.

**Which phase should address it:** First phase of implementation. This must be the migration plan's backbone -- not an afterthought.

---

### Pitfall 2: Player Profile Docname Change Breaks All MariaDB Foreign Keys

**What goes wrong:** The current Player Profile uses `autoname: "field:user"`, meaning the docname IS the email address (e.g., `ahmed@example.com`). The PRD proposes changing to `autoname: "PLAYER-.#####."`. This changes every Player Profile's `name` (primary key). Every child DocType that links to Player Profile via a `Link` field stores the docname as a foreign key value. Changing the docname orphans all linked records.

**Affected MariaDB tables (verified via codebase grep):**

| DocType | Field | Current Value | After Migration |
|---|---|---|---|
| `Memora Player Subscription` | `player` (Link) | `ahmed@example.com` | Broken FK |
| `Memora Structure Progress` | `player` (Link) | `ahmed@example.com` | Broken FK |
| `Memora Player Wallet` | `player` (Link) | `ahmed@example.com` | Broken FK |
| `Memora Subscription Transaction` | `player` (Link) | `ahmed@example.com` | Broken FK |
| `Memora Interaction Log` | `player` (Link) | `ahmed@example.com` | Broken FK |
| `Memora Memory State` | `player` (Link) | `ahmed@example.com` | Broken FK |
| `Memora Content Report` | `player` (Link) | `ahmed@example.com` | Broken FK |

**Why it happens:** Frappe Link fields store the referenced document's `name` as a plain string in MariaDB. There are no cascading updates. If you change the Player Profile's `name`, nothing propagates.

**Consequences:**
- `sync_dirty_wallets` fails: queries `{"player": "PLAYER-00001"}` but MariaDB record has `player = "ahmed@example.com"`
- `sync_dirty_progress` fails: same pattern
- Hydration APIs fail: `get_player_access_keys` queries `{"player": player_id}` where `player_id` is the new JWT `sub` but MariaDB still has old docname
- All admin views (Frappe Desk) show broken links

**Prevention:**
1. **For existing players: Use Frappe's `rename_doc()` API.** This updates the document name AND all Link field references across the entire database. It is slow but correct.
2. **Migration script must:** (a) Create new Player Profile with `PLAYER-.#####.` autoname, (b) For each existing profile: call `frappe.rename_doc("Memora Player Profile", old_name, new_name)` which cascades updates to all linked DocTypes, (c) Verify all FK references updated.
3. **Alternative (recommended):** Do NOT rename existing profiles. Instead, add `mobile` and `password` fields to the existing DocType, keep the current `autoname: "field:user"` for existing records, and change `autoname` to `PLAYER-.#####.` for NEW records only. Frappe allows this -- existing records keep their old names, new records get the new pattern. But this means old players still have email-as-docname while new players get `PLAYER-XXXXX`. JWT `sub` should use the docname regardless of format (it is opaque to downstream code).

**Detection:** After migration, run: `SELECT COUNT(*) FROM tabMemora Player Wallet WHERE player NOT IN (SELECT name FROM tabMemora Player Profile)` -- any non-zero count means broken FKs.

**Which phase should address it:** Pre-migration planning. The decision between `rename_doc` and dual-autoname fundamentally shapes every subsequent phase.

---

### Pitfall 3: Frappe `__Auth` Table Keying for Custom DocType Password

**What goes wrong:** The `check_password(user, pwd, doctype, fieldname)` function in Frappe queries the `__Auth` table with `doctype`, `name`, and `fieldname` columns. The `name` column must match the Player Profile's docname. If the whitelisted API passes the phone number as `user` but the docname is `PLAYER-00001`, the password check fails because no `__Auth` row exists with `name = "966512345678"` and `doctype = "Memora Player Profile"`.

**Why it happens:** Developers assume `check_password` takes a "login identifier" (like phone number) and looks it up. It does not. It takes the exact document `name` from the DocType. For the User DocType, this happens to be the email because User's docname is the email. For a custom DocType with `autoname: "PLAYER-.#####."`, the name is `PLAYER-00001`, not the phone number.

**Source:** Verified via [Frappe password.py on GitHub](https://github.com/frappe/frappe/blob/develop/frappe/utils/password.py) -- `check_password(user, pwd, doctype="User", fieldname="password")` where `user` must match the document `name` in the `__Auth` table.

**Consequences:** All password verifications fail with `frappe.AuthenticationError`. Players cannot log in.

**Prevention:**
1. The Frappe whitelisted login API must:
   - Receive phone number from FastAPI
   - Look up the Player Profile by `mobile` field: `profile_name = frappe.get_value("Memora Player Profile", {"mobile": phone}, "name")`
   - Call `check_password(profile_name, password, "Memora Player Profile", "password")`
   - Return profile data to FastAPI
2. The `check_password` call MUST use the docname, not the phone number.
3. Add an integration test that verifies the full chain: phone input -> mobile lookup -> docname -> check_password -> success.

**Detection:** First login attempt after deployment fails with "Incorrect User or Password" even with correct credentials.

**Which phase should address it:** The phase that implements the Frappe whitelisted auth API.

---

### Pitfall 4: Event Hooks Reference Removed `doc.user` Field -- Silent Failure in Background

**What goes wrong:** Five event handler files reference `doc.user` or `player_doc.user` on Player Profile documents. If the `user` field is removed from the DocType schema before these handlers are updated, every event fires and silently fails (Frappe catches exceptions in doc_events and logs them, but does not block the save).

**Affected files (verified line-by-line):**

| File | Line | Code | Impact of Failure |
|---|---|---|---|
| `access_sync.py` | 96 | `user_id = player_doc.user` | Subscription grants NOT synced to Redis; players locked out |
| `access_sync.py` | 128 | `user_id = player_doc.user` | Subscription deletion NOT synced; stale grants remain |
| `device_sync.py` | 45 | `user_id = doc.user` | Device removal NOT synced to Redis; removed devices stay active |
| `profile_sync.py` | 29 | `redis_key = f"memora:profile:{doc.user}"` | Profile cache written to wrong key (empty string); leaderboard shows broken profiles |
| `profile_sync.py` | 33, 45 | `"player_id": doc.user` | Profile data has empty player_id |
| `plan_change_sync.py` | 32 | `session_key = f"memora:session:{doc.user}"` | Session NOT invalidated on plan change; player keeps old plan until token expires |
| `plan_change_sync.py` | 39 | `"player_id": doc.user` | Invalidation pubsub message has empty player_id |

**Why it happens:** Schema changes (removing `user` field from JSON) are deployed instantly. Event hooks registered in `hooks.py` fire on every save. If the field removal and handler updates are not deployed atomically, there is a window where events fire with the old handler code against the new schema.

**Critical detail:** `doc.user` on a Frappe Document does NOT raise `AttributeError` -- it returns `None` or an empty string if the field does not exist in the schema. So the handlers silently proceed with `user_id = None`, and the Redis operations silently succeed with keys like `memora:access:None`.

**Prevention:**
1. **Update ALL event handlers BEFORE removing the `user` field from the DocType schema.** The handlers should be changed to use the new identity (e.g., `doc.name` or `doc.mobile`) while the old `user` field still exists. This way the handlers work with both old and new schemas.
2. **Deploy handler code changes first**, then `bench migrate` to update the schema, then restart workers.
3. **Grep for `doc.user` and `player_doc.user`** across the entire codebase before removing the field. The command: `grep -rn "\.user\b" memora_admin/events/` catches all references.

**Detection:** Check Redis for keys containing `None`: `redis-cli KEYS "memora:*None*"`. Also check Frappe Error Log for any event handler failures.

**Which phase should address it:** Must be completed in the same phase that modifies the Player Profile DocType, and deployed BEFORE the schema change.

---

### Pitfall 5: `plan_change_sync.py` and `profile_sync.py` Use Wrong Redis Client

**What goes wrong:** `plan_change_sync.py` uses `frappe.cache().delete_value(session_key)` to invalidate sessions, and `profile_sync.py` uses `frappe.cache().set_value()` to write profile cache. Frappe's cache adds a site-specific prefix to all keys (e.g., `x.conanacademy.com|memora:session:ahmed@example.com`). But the FastAPI sidecar writes and reads session keys WITHOUT this prefix (plain `memora:session:ahmed@example.com`). So the session invalidation deletes a key that does not exist, and the actual session key remains untouched.

**This is a PRE-EXISTING BUG, not caused by the migration.** But the migration will touch these files (to replace `doc.user`), so it is the right time to fix it.

**Why it happens:** `access_sync.py` and `device_sync.py` correctly use `get_fastapi_redis()` which connects to the same Redis without site prefix. `plan_change_sync.py` and `profile_sync.py` were written later and incorrectly used `frappe.cache()`.

**Consequences:**
- When an admin changes a player's plan in Frappe Desk, the player's session is NOT actually invalidated. The player continues using their old plan until their access token expires naturally.
- Profile cache updates from Frappe Desk go to the wrong Redis keyspace. FastAPI never sees them.

**Prevention:**
1. During migration, fix `plan_change_sync.py` to use `get_fastapi_redis()` instead of `frappe.cache()`.
2. Similarly fix `profile_sync.py` which also uses `frappe.cache()` (lines 28, 39, 48).
3. Add a comment explaining the two Redis access patterns and when to use each.

**Detection:** After admin plan change, verify session is gone: `redis-cli GET "memora:session:{player_id}"` -- if it still exists, the bug is active.

**Which phase should address it:** Fix during the migration phase when these event handlers are being updated anyway.

---

## Moderate Pitfalls

Mistakes that cause delays, regressions, or technical debt.

---

### Pitfall 6: Hydration APIs Hardcode Email-Based Lookup Logic

**What goes wrong:** The hydration APIs in `memora_admin/api/subscriptions.py` and `memora_admin/api/wallet.py` contain fallback logic that queries Player Profile by `{"user": player_id}` to resolve the docname. After migration, the `user` field is removed, so this fallback breaks. The hydration returns empty results, and Redis self-healing fails silently.

**Concrete code paths:**

- `get_player_access_keys` (line 90-96): Falls back to `frappe.db.get_value("Memora Player Profile", {"user": player_id}, "name")` -- this query returns `None` after `user` field removal.
- `get_player_progress` (line 133-137): Same fallback pattern.
- `get_player_wallet` (line 23): Queries `{"player": player_id}` -- this works IF `player_id` matches the Player Wallet's `player` field, which links to Player Profile docname. If the docname changed, this also breaks.

**Why it happens:** These APIs were designed with the assumption that `player_id` could be either a Frappe User email or a Player Profile docname, with fallback logic to handle both. After migration, neither assumption holds cleanly.

**Prevention:**
1. Update hydration APIs to accept the new identity format. If `player_id` is the Player Profile docname (e.g., `PLAYER-00001`), direct queries on `{"player": player_id}` will work because Link fields store the docname.
2. Remove the `{"user": player_id}` fallback code after migration.
3. Test hydration by flushing Redis (`FLUSHDB`) and hitting an endpoint that triggers `ensure_hydrated()`.

**Which phase should address it:** Same phase as the auth migration, since it changes what `player_id` means.

---

### Pitfall 7: JWT `sub` Change Invalidates All Existing Tokens Simultaneously

**What goes wrong:** Every outstanding access token and refresh token has `sub` set to an email address. After migration, the auth endpoint issues tokens with `sub` set to a phone number (or docname). The `decode_token` function does not validate the format of `sub` -- it just checks signature and expiry. So old tokens with email `sub` will still be accepted, but the identity they carry no longer maps to any Player Profile.

**Concrete failure path:**
1. Player logs in before migration, gets token with `sub = "ahmed@example.com"`
2. Migration deploys, Player Profile docname changes
3. Player uses old token; `user.sub = "ahmed@example.com"` flows into all service calls
4. `memora:access:ahmed@example.com` -- keys no longer exist (migrated or orphaned)
5. `memora:wallet:ahmed@example.com` -- same
6. Player appears to have no progress, no access, no XP

**Worse scenario:** If refresh tokens are still valid (30-day lifetime), the `/auth/refresh` endpoint creates a NEW access token with `email=payload.get("email", "")` (line 224 in auth.py). This propagates the stale email into fresh tokens.

**Prevention:**
1. **Rotate the JWT secret** at migration time. This instantly invalidates ALL outstanding tokens, forcing every player to re-login. This is the cleanest approach.
2. **Alternative:** Add a `token_version` claim to JWTs and check it on decode. Increment the version at migration time. Old tokens fail validation.
3. **Communicate the forced re-login** to users (in-app notification before migration). The forced re-login is a one-time event and acceptable for a production system.

**Which phase should address it:** Deployment phase of the migration. Must happen simultaneously with the identity switch.

---

### Pitfall 8: Admin Login Regression -- `create_access_token` Requires `email` Parameter

**What goes wrong:** The current `create_access_token` function (in `security.py`) has `email` as a required positional parameter. If the migration makes `email` optional or removes it, the admin login path (which still uses Frappe User email) must still pass an email. If the migration accidentally breaks the admin token structure, admins get locked out of admin endpoints.

**Concrete code in `security.py` line 13:**
```python
def create_access_token(
    user_id: str,
    email: str,  # Required parameter
    plan_id: str,
    ...
```

**Admin dependency chain:**
1. Admin logs in via Frappe User (email+password)
2. Admin gets JWT with `sub = email`, `role = "System Manager"`
3. `require_admin` dependency checks `user.role == "System Manager"` (deps.py line 95)
4. Admin endpoints use `user.sub` as admin identifier in audit logs

**Prevention:**
1. Make `email` parameter optional with default `None` in `create_access_token`.
2. For player login: pass `email=None` (players don't have email).
3. For admin login: pass `email=admin_user.email` (admins still have email).
4. Keep the `email` field in `TokenPayload` as `Optional[str]` (it already is: line 45).
5. **Test both login paths** after migration: player login AND admin login.

**Which phase should address it:** Same phase as the auth endpoint changes. Add regression test for admin login.

---

### Pitfall 9: Phone Number Normalization Inconsistency Between Registration and Login

**What goes wrong:** A player registers with phone `+962 512 345 678` which normalizes to `962512345678`. Later, they log in with `0512345678` (local format). If the login path normalizes differently (or not at all), the lookup fails. The player sees "Invalid credentials" despite having a valid account.

**The PRD notes this risk but defers country code handling:** "No country code handling (digits only as entered)." This is a design decision, but it creates a trap: if `+962512345678` and `962512345678` are stored as different identifiers, duplicate accounts can be created.

**Where normalization must be enforced (all paths):**
1. Player Profile `validate()` hook (Frappe side) -- before saving to MariaDB
2. Registration endpoint (FastAPI side) -- before calling Frappe API
3. Login endpoint (FastAPI side) -- before looking up Player Profile
4. Password reset endpoint (FastAPI side) -- before looking up Player Profile
5. Webhook `player_id` field -- payment provider may send phone in different format

**Prevention:**
1. Create a single `normalize_phone(raw: str) -> str` function used everywhere.
2. The function should: strip `+`, strip spaces/dashes, strip leading `0` (if country code prefix is standard).
3. Apply normalization in `validate()` so the stored value is always canonical.
4. Apply the SAME normalization in login/reset before lookup.
5. Add a unique index on the `mobile` field in the DocType JSON to catch duplicates at the DB level.

**Which phase should address it:** Registration and login implementation phase. Must be done BEFORE any real phone numbers enter the system.

---

### Pitfall 10: `purchase.py` Lookup Will Break -- Uses `{"user": user_id}` Filter

**What goes wrong:** The Frappe whitelisted API `purchase.py` line 44 does:
```python
player_id = frappe.get_value("Memora Player Profile", {"user": user_id}, "name")
```
After migration, the `user` field is removed. This query returns `None`, and the purchase request fails with "Player profile not found."

**Why it is moderate, not critical:** Purchases go through a webhook flow that uses `player_id` from the payment provider, not from JWT. But the `purchase.py` API is also called directly from FastAPI's purchase endpoint (line 41 of `fastapi_app/api/v1/endpoints/purchase.py`) which passes `user.sub`. If `user.sub` is now the Player Profile docname, the query should be:
```python
player_id = user_id  # user.sub IS the docname
```

**Prevention:**
1. Update `purchase.py` to use direct name lookup instead of filter-by-user.
2. If the docname IS the identity (e.g., `PLAYER-00001`), then `player_id = user_id` directly.
3. Test the purchase flow end-to-end after migration.

**Which phase should address it:** Same phase as event handler updates.

---

### Pitfall 11: OTP Static Stub Creates Enumeration and Squatting Risk

**What goes wrong:** The PRD specifies a static OTP value "1111" for development. If this stub is deployed to production (intentionally for MVP or accidentally), any attacker can:
1. Send registration request with any phone number
2. Submit "1111" as OTP
3. Claim any phone number, potentially one belonging to a real student

This is account takeover via phone number squatting.

**Why it happens:** Static OTP stubs are common in development. The danger is that "we will add real OTP later" becomes "we shipped with static OTP and forgot."

**Prevention:**
1. Make the OTP stub behavior controlled by an environment variable: `OTP_MODE=stub` vs `OTP_MODE=sms`.
2. Add a startup check in FastAPI: if `OTP_MODE=stub` and `ENVIRONMENT=production`, log a CRITICAL warning.
3. Rate-limit registration attempts per phone number (not just per IP) even with stub OTP: maximum 3 registration attempts per phone per hour.
4. Add an admin-visible flag in the system settings showing whether OTP is in stub mode.
5. The static value should NOT be a common PIN like "1111" -- use something less guessable like "991122" for development only.

**Which phase should address it:** Registration implementation phase.

---

### Pitfall 12: Password Reset Temp Tokens Stored Without Proper Scoping

**What goes wrong:** The PRD describes a 3-step password reset flow with temp tokens. If the temp token is a simple random string stored in Redis without proper scoping (e.g., just `memora:reset:{token}`), an attacker who discovers a valid token format could attempt brute-force. More dangerously, if the token is not bound to a specific phone number, a token issued for phone A could be replayed against phone B.

**Prevention:**
1. Temp tokens must be: (a) cryptographically random (minimum 32 bytes, URL-safe base64), (b) bound to a specific phone number in Redis (`memora:reset:{token}` -> `{"phone": "966512345678", "step": "verified"}`), (c) short-lived (5 minutes maximum), (d) single-use (delete after successful password reset).
2. Rate-limit password reset requests: maximum 3 per phone per hour.
3. The reset endpoint must verify that the phone number in the request matches the phone number bound to the token.
4. Log all password reset attempts for audit.

**Which phase should address it:** Password reset implementation phase.

---

### Pitfall 13: Leaderboard Entries Under Two Identities After Migration

**What goes wrong:** Leaderboard sorted sets (daily, weekly, alltime) use `player_id` as the member key. Before migration, entries are stored as `ahmed@example.com` with XP score. After migration, new XP awards are stored as `PLAYER-00001`. The same player now has two entries in the leaderboard -- one under email with historical XP, one under new identity with zero.

**Consequences:**
- Player's ranking drops dramatically (only new XP counts under new identity)
- Old email entries persist in leaderboard, showing as unresolvable player_ids
- Profile batch fetch for leaderboard entries fails for orphaned email-keyed entries

**Prevention:**
1. Include leaderboard sorted sets in the Redis key migration script.
2. For each player: `ZINCRBY new_key, old_score, new_identity` then `ZREM old_key old_identity`.
3. Archive daily/weekly leaderboards (they reset anyway) and only migrate alltime.
4. Time the migration to coincide with a natural leaderboard reset (daily at 00:10, weekly at Friday 00:15 per hooks.py scheduler).

**Which phase should address it:** Redis key migration phase, ideally timed with a weekly leaderboard reset.

---

## Minor Pitfalls

Mistakes that cause annoyance but are fixable.

---

### Pitfall 14: Phone Number Exposed in Leaderboard API Response

**What goes wrong:** The leaderboard endpoint returns `player_id` in the response (used for "is this me?" detection at `leaderboard.py:74`). If `player_id` is the raw phone number, other players can see each other's phone numbers.

**Prevention:** The current code already resolves `player_id` to `display_name` and `avatar` via `ProfileService` before returning. Verify that:
- If using docname format (`PLAYER-00001`) as `user.sub`, this is safe -- opaque identifier
- If using phone number as `user.sub`, the `player_id` field in `LeaderboardEntry` response must be masked or excluded
- The recommended approach is to use the Player Profile docname (not phone number) as `user.sub`

**Which phase should address it:** Pre-deployment review of API response models.

---

### Pitfall 15: `allow_guest=True` Whitelisted Auth API Bypasses Rate Limiting

**What goes wrong:** The PRD recommends `allow_guest=True` on the Frappe whitelisted method that checks passwords. This is correct (the login API must be callable without authentication). But it means anyone on the network can call this Frappe endpoint directly, bypassing FastAPI's rate limiting that protects the login endpoint.

**Prevention:**
1. Add rate limiting in the Frappe whitelisted method itself (using `frappe.rate_limiter` or a simple Redis counter).
2. Restrict the Frappe method to accept only requests from localhost (check `frappe.local.request.remote_addr`).
3. Better yet: use `allow_guest=False` and have FastAPI call with API key authentication (already used by `FrappeClient`). This prevents direct access from outside.

**Which phase should address it:** Frappe auth API implementation phase.

---

### Pitfall 16: `FrappeUser` Model Assumed in Refresh Token Flow

**What goes wrong:** The refresh endpoint (auth.py line 224) creates a new access token with `email=payload.get("email", "")`. After migration, player tokens do not have an `email` field. The fallback to empty string is safe but creates tokens with `email: ""` which is misleading. If any downstream code checks `user.email` (e.g., for logging or error reports), it gets an empty string.

**Prevention:**
1. Update the refresh flow to not assume email exists.
2. For player tokens: set `email=None` (the TokenPayload already allows `None`).
3. Update `create_access_token` to accept `email: str | None = None` and exclude it from JWT payload when `None`.

**Which phase should address it:** Auth endpoint migration phase.

---

### Pitfall 17: `sync_dirty_progress` Uses Email as MariaDB `player` Field Value

**What goes wrong:** When `sync_dirty_progress` (sync.py line 139) inserts a new `Memora Structure Progress` record, it sets `"player": user_id` where `user_id` is parsed from the dirty set member (format: `user_id:subject_id:v{version}`). If the dirty set was populated with email-based identity but the Player Profile docname has changed, the INSERT creates a new progress record with the old email as the `player` value -- which is a broken Link field reference.

**Prevention:**
1. Ensure the identity transition in JWT `sub` and the dirty set member format happen atomically.
2. Before migration, drain all dirty sets completely.
3. After migration, verify no dirty set members contain email-format identifiers.

**Which phase should address it:** Pre-deployment checklist for migration day.

---

## Phase-Specific Warnings

| Phase Topic | Likely Pitfall | Mitigation |
|---|---|---|
| DocType schema change | Pitfall 2 (FK breakage), Pitfall 4 (event hooks) | Update hooks BEFORE schema change; use `rename_doc` or dual-autoname |
| Frappe auth API | Pitfall 3 (__Auth keying), Pitfall 15 (guest access) | Use docname in check_password; restrict to localhost |
| FastAPI login/register | Pitfall 9 (phone normalization), Pitfall 11 (OTP stub) | Single normalization function; env-controlled OTP mode |
| Redis key migration | Pitfall 1 (identity split), Pitfall 13 (leaderboard) | Migration script; flush dirty sets first; time with leaderboard reset |
| JWT token changes | Pitfall 7 (token invalidation), Pitfall 8 (admin regression) | Rotate JWT secret; test admin login path |
| Password reset | Pitfall 12 (temp tokens) | Bind token to phone; short TTL; single-use |
| Event handler updates | Pitfall 4 (doc.user), Pitfall 5 (Redis client mismatch) | Fix all handlers atomically; fix frappe.cache() bug |
| Hydration APIs | Pitfall 6 (email-based lookup) | Update queries to use new docname identity |
| Purchase flow | Pitfall 10 (user field lookup) | Update to direct name lookup |
| Deployment | Pitfall 7 (token invalidation) | Rotate JWT secret; communicate forced re-login |

---

## Migration Ordering (Recommended)

Based on pitfall dependencies, the safest migration order is:

1. **Pre-migration:** Flush all dirty sets, archive leaderboards, take MariaDB backup
2. **Schema preparation:** Add `mobile` + `password` fields WITHOUT removing `user` field
3. **Event handler update:** Fix all `doc.user` references to use `doc.name` or `doc.mobile`; fix `frappe.cache()` bug in `plan_change_sync.py` and `profile_sync.py`
4. **Frappe auth API:** Implement whitelisted `check_password` method with correct docname lookup
5. **FastAPI auth endpoints:** Implement new login/register with phone normalization
6. **Data migration:** Existing players get `mobile` field populated; if renaming docnames, run `rename_doc` for each player
7. **Redis key migration:** Script to copy/rename all Redis keys from old identity to new identity
8. **JWT switch:** Rotate JWT secret, deploy new token format, force re-login
9. **Cleanup:** Remove `user` field from schema, remove old fallback code, clean orphaned Redis keys
10. **Hydration API update:** Remove `{"user": player_id}` fallback logic

---

## Sources

- Direct codebase analysis of all files listed (HIGH confidence -- verified line by line)
- [Frappe password.py source on GitHub](https://github.com/frappe/frappe/blob/develop/frappe/utils/password.py) -- `check_password` function signature and __Auth table behavior
- [Frappe Field Types Documentation](https://docs.frappe.io/framework/user/en/basics/doctypes/fieldtypes) -- Password fieldtype behavior with __Auth table
- [Frappe Forum: Password field returns ****](https://discuss.frappe.io/t/frappe-db-get-value-for-password-field-returns/14553) -- confirmation that Password fields store in __Auth
- PRD at `.planning/prd/mobile-auth-migration.md` -- design decisions and planned changes
