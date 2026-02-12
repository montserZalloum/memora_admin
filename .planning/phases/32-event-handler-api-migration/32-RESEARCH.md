# Phase 32: Event Handler & API Migration - Research

**Researched:** 2026-02-12
**Domain:** Frappe event handlers, Frappe APIs, and FastAPI service layer identity migration
**Confidence:** HIGH

## Summary

This phase completes the v2.0 Mobile-First Player Authentication migration by updating all event handlers and Frappe APIs from the old user-based identity model (email in `doc.user`, `{"user": player_id}` lookups) to the new docname-based identity model (`PLAYER-#####` via `doc.name`, `{"mobile": phone}` or direct docname lookups). The scope is well-defined: a finite set of files with clear, mechanical changes, plus a pre-existing Redis client bug to fix.

**The comprehensive audit identified 20 files requiring changes** across four categories: (1) event handlers with `doc.user` references, (2) Frappe APIs with `{"user": ...}` lookups, (3) the Player Profile JSON schema `user` field removal, and (4) a scheduled task with stale `user`-based queries. The FastAPI sidecar (auth endpoints, JWT, services) is already aligned with PLAYER-##### identity from Phase 31 and requires no changes. The two-pronged Redis invalidation pattern (direct delete + pubsub) is well-established in `catalog_sync.py` and `build_trigger.py` and should be adopted for `profile_sync.py` and `plan_change_sync.py` when migrating them to `get_fastapi_redis()`.

**Primary recommendation:** Group changes into three plans: (1) event handlers + schema removal, (2) Frappe API migration, (3) scheduled task fixes. Each plan is self-contained with clear verification steps.

<user_constraints>
## User Constraints (from CONTEXT.md)

### Locked Decisions
- **Pre-launch, no real players** -- database has only test data. All new players will be PLAYER-##### format
- **Drop old-style compatibility completely** -- remove all email/user-based identity paths. Clean break, simpler code
- **Leave test data alone** -- don't include cleanup scripts. Just make new code work with PLAYER-##### naming
- **Admin overlap exists** -- some events (device management, profile updates) can be triggered by admins acting on player data. Event handlers must distinguish between admin users (Frappe User email) and player docnames (PLAYER-#####)
- **Replace doc.user with doc.name** -- rewrite all references to use the player's docname (PLAYER-#####). Remove doc.user dependency entirely
- **Remove doc.user field from schema** -- delete from Player Profile JSON schema. Clean break, no confusion about what identifies a player
- **Redis key audit needed** -- not certain which Redis keys use doc.user vs docname. Claude must audit all key patterns and fix any that reference user/email instead of docname
- **Full codebase audit** -- grep for every doc.user, user=, email-based lookup and fix them all. Nothing left behind
- **Include JavaScript** -- audit Python AND JavaScript (.js files in DocTypes). Form handlers, list views, client scripts
- **Audit all FastAPI too** -- full audit of fastapi_app/ for any user-based identity references, not just Frappe side
- **Add code comments** -- document in key files that player identity is PLAYER-##### docname, not email

### Claude's Discretion
- JWT claims audit -- verify JWT 'sub' claim alignment with PLAYER-##### and fix if needed
- Redis helper pattern and connection approach
- Invalidation pattern (two-pronged vs direct-only)
- Scope of get_fastapi_redis() migration beyond SC#3 files

### Deferred Ideas (OUT OF SCOPE)
None -- discussion stayed within phase scope
</user_constraints>

## Comprehensive Codebase Audit Results

### Files Requiring Changes (20 files)

#### Category 1: Event Handlers with `doc.user` References (4 files)

| File | Lines | Current Code | Required Change | Impact |
|------|-------|-------------|-----------------|--------|
| `events/access_sync.py` | 96, 128 | `user_id = player_doc.user` | `user_id = player_doc.name` | Access grants written to wrong Redis key |
| `events/device_sync.py` | 45-47 | `user_id = doc.user` | `user_id = doc.name` | Device removal and session invalidation fail |
| `events/plan_change_sync.py` | 28, 32-45 | `frappe.cache()` + `doc.user` | `get_fastapi_redis()` + `doc.name` | Session not invalidated, wrong Redis namespace |
| `events/profile_sync.py` | 28-50 | `frappe.cache()` + `doc.user` | `get_fastapi_redis()` + `doc.name` | Profile cache written to wrong key, wrong Redis namespace |

#### Category 2: Frappe APIs with `{"user": ...}` Lookups (4 files)

| File | Lines | Current Code | Required Change |
|------|-------|-------------|-----------------|
| `api/purchase.py` | 44 | `frappe.get_value(..., {"user": user_id}, "name")` | Use `user_id` directly as docname (JWT `sub` = `PLAYER-#####`) |
| `api/profile.py` | 46-47 | `filters={"user": ["in", player_ids]}`, `fields=["user", ...]` | `filters={"name": ["in", player_ids]}`, `fields=["name", ...]` |
| `api/profile.py` | 148 | `frappe.get_value(..., {"user": player_id}, "name")` | Use `player_id` directly as docname |
| `api/subscriptions.py` | 88-103 | Dual lookup: direct match then `{"user": player_id}` fallback | Remove fallback, use `player_id` directly |
| `api/subscriptions.py` | 132-147 | Same dual lookup pattern in `get_player_progress` | Remove fallback, use `player_id` directly |

#### Category 3: Schema Change (1 file)

| File | Change |
|------|--------|
| `doctype/memora_player_profile/memora_player_profile.json` | Remove `user` field from `fields` array and `field_order` |

#### Category 4: Scheduled Tasks (2 files)

| File | Lines | Current Code | Required Change |
|------|-------|-------------|-----------------|
| `tasks/profile_cache.py` | 153-166 | `filters={"user": ...}`, `fields=["user", ...]`, `p.user` | `filters={"name": ...}`, `fields=["name", ...]`, `p.name` |
| `tasks/fsrs_processor.py` | 97, 101 | `SELECT pp.user AS player ... WHERE pp.user IN` | `SELECT pp.name AS player ... WHERE pp.name IN` |

### Files Confirmed Clean (NO changes needed)

#### FastAPI Sidecar (ALREADY ALIGNED)

| File | Status | Evidence |
|------|--------|---------|
| `fastapi_app/core/security.py` | Clean | `sub` claim = `user_id` param; docstring says "For players: PLAYER-##### docname" |
| `fastapi_app/models/auth.py` | Clean | `TokenPayload.sub` documented as "PLAYER-##### for players, email for admins" |
| `fastapi_app/api/v1/endpoints/auth.py` | Clean | Player login uses `player_id = profile["player_id"]` (which is `doc.name`) |
| `fastapi_app/services/session.py` | Clean | Keys by `user_id` param (receives PLAYER-##### from JWT sub) |
| `fastapi_app/services/access.py` | Clean | Keys by `player_id` param (receives PLAYER-##### from JWT sub) |
| `fastapi_app/services/device.py` | Clean | Keys by `user_id` param (receives PLAYER-##### from JWT sub) |
| `fastapi_app/services/wallet.py` | Clean | Keys by `player_id` param (receives PLAYER-##### from JWT sub) |
| `fastapi_app/services/progress.py` | Clean | Keys by `user_id` param (receives PLAYER-##### from JWT sub) |
| `fastapi_app/services/profile.py` | Clean | Keys by `player_id` param; cache key is `memora:profile:{player_id}` |
| `fastapi_app/services/purchase.py` | Clean | Keys by `user_id` param (receives PLAYER-##### from JWT sub) |
| `fastapi_app/api/deps.py` | Clean | `user.sub` used for all service calls |
| All other FastAPI services | Clean | Use `user_id`/`player_id` params from JWT sub |

#### JavaScript Files

| File | Status | Evidence |
|------|--------|---------|
| `memora_player_profile.js` | Clean | Uses `frm.doc.name` for all API calls (password reset, device sync, grant access) |
| `public/js/admin_filter_helper.js` | Clean | No user references |
| `public/js/game_lesson.js` | Clean | No user references |

#### Frappe APIs (Already Clean)

| File | Status | Evidence |
|------|--------|---------|
| `api/auth.py` | Clean | All 5 functions use `mobile` lookups and `doc.name` (Phase 31) |
| `api/devices.py` | **NEEDS FIX** | Lines 51, 55, 127, 131-132: `profile.user` for Redis keys |
| `api/wallet.py` | Clean | `player_id` param used directly in `{"player": player_id}` filter |
| `api/subscriptions.py` | **NEEDS FIX** | Lines 88-103: `{"user": player_id}` fallback lookup |

**Correction on api/devices.py:** This file was initially missed. It uses `profile.user` to construct Redis keys:
- Line 51: `user_id = profile.user`
- Line 55: `devices_key = f"memora:devices:{user_id}"`
- Line 127: `user_id = profile.user`
- Line 131-132: Same key construction

This is a **5th API file** requiring changes.

### Updated File Count: 5 API files need changes (not 4)

## Redis Key Audit

### Key Patterns Currently Using Identity

| Redis Key Pattern | Current Identity Source | New Identity Source | Change Needed? |
|-------------------|----------------------|-------------------|----------------|
| `memora:access:{player_id}` | `player_doc.user` (access_sync.py) | `player_doc.name` | YES |
| `memora:session:{player_id}` | `doc.user` (plan_change_sync, device_sync) | `doc.name` | YES |
| `memora:profile:{player_id}` | `doc.user` (profile_sync.py) | `doc.name` | YES |
| `memora:devices:{player_id}` | `doc.user` (device_sync.py, devices.py API) | `doc.name` | YES |
| `memora:wallet:{player_id}` | Already uses PLAYER-##### (auth.py L244) | Already correct | NO |
| `memora:progress:{user_id}:...` | JWT `sub` (FastAPI only) | Already correct | NO |
| `memora:stats:{user_id}:...` | JWT `sub` (FastAPI only) | Already correct | NO |
| `memora:gamesession:{user_id}` | JWT `sub` (FastAPI only) | Already correct | NO |
| `memora:pending:{user_id}` | JWT `sub` (FastAPI only) | Already correct | NO |
| `memora:lb:*` | JWT `sub` (FastAPI only) | Already correct | NO |

**Key insight:** All FastAPI-written Redis keys already use PLAYER-##### (the JWT `sub` claim has been PLAYER-##### since Phase 31). Only Frappe-side event handlers still write keys using the old `doc.user` identity. This means after migration, all Redis keys will consistently use PLAYER-##### docnames.

## JWT Claims Audit (Claude's Discretion)

**Finding:** JWT `sub` claim is ALREADY aligned with PLAYER-#####.

Evidence:
- `create_access_token(user_id=player_id, ...)` in auth.py Line 131 -- `player_id` comes from `profile["player_id"]` which is `doc.name` (PLAYER-#####)
- `TokenPayload.sub` documented as "User ID (PLAYER-##### for players, email for admins)" in models/auth.py Line 30
- Admin tokens use `user.user_id` (email) as `sub` -- this is correct and unchanged

**Recommendation:** No JWT changes needed. The `sub` claim already contains the correct identity. All FastAPI services receiving `user.sub` get PLAYER-##### for players and email for admins.

## Redis Helper Pattern Audit (Claude's Discretion)

### `get_fastapi_redis()` Pattern (Established)

```python
# access_sync.py - the canonical pattern
def get_fastapi_redis():
    """Get Redis connection for FastAPI sidecar."""
    redis_url = os.getenv("REDIS_URL", "redis://localhost:6379/0")
    return redis.from_url(redis_url)
```

**Used correctly by:**
- `access_sync.py` (defines it)
- `device_sync.py` (imports from access_sync)
- `catalog_sync.py` (imports from access_sync)
- `build_trigger.py` (imports from access_sync)
- `api/devices.py` (imports from access_sync)
- `api/auth.py` (imports from access_sync)

**NOT used by (bugs):**
- `profile_sync.py` -- uses `frappe.cache()` (WRONG)
- `plan_change_sync.py` -- uses `frappe.cache()` (WRONG)

**Recommendation:** Reuse `get_fastapi_redis()` imported from `access_sync.py`. The pattern is well-established, all other event handlers already use it.

### `frappe.cache()` vs `get_fastapi_redis()` Analysis

| Operation | `frappe.cache()` | `get_fastapi_redis()` |
|-----------|-----------------|----------------------|
| Key prefix | Adds `{site_name}\|` prefix | No prefix (raw key) |
| Serialization | Pickle (binary) | Plain string |
| SET method | `cache.set_value(key, val)` | `r.set(key, val, ex=TTL)` |
| DEL method | `cache.delete_value(key)` | `r.delete(key)` |
| PUBLISH | `cache.publish(channel, msg)` | `r.publish(channel, msg)` |

**Critical:** `frappe.cache()` adds a site prefix to keys (e.g., `x_conanacademy_com|memora:session:PLAYER-00001`). FastAPI reads keys WITHOUT this prefix (`memora:session:PLAYER-00001`). Using `frappe.cache()` in event handlers that write data consumed by FastAPI means FastAPI never sees the data.

## Invalidation Pattern Audit (Claude's Discretion)

### Two-Pronged Pattern (Established)

```python
# catalog_sync.py - the canonical two-pronged pattern
r = get_fastapi_redis()

# 1. Direct cache delete (immediate effect)
r.delete(f"memora:catalog:{plan_id}")

# 2. Pubsub notification for FastAPI sidecar
r.publish("memora:cache:invalidate", json.dumps({
    "type": "catalog",
    "plan_id": plan_id,
    "timestamp": str(frappe.utils.now()),
}))
```

**Used by:**
- `catalog_sync.py` -- direct delete + pubsub
- `build_trigger.py:_invalidate_hierarchy_cache()` -- direct delete + pubsub

**Needed for:**
- `profile_sync.py` -- currently uses `cache.set_value()` + `cache.publish()` via wrong Redis
- `plan_change_sync.py` -- currently uses `cache.delete_value()` + `cache.publish()` via wrong Redis

**Recommendation:** Keep the two-pronged pattern. It is already established, well-documented, and handles the edge case where FastAPI's in-process cache hasn't received the pubsub message yet (direct delete ensures immediate effect). Apply it to profile_sync.py and plan_change_sync.py when migrating to `get_fastapi_redis()`.

### Profile Sync Special Case

`profile_sync.py` does a SET (not just DELETE) -- it pushes the full profile data to Redis. The migrated version should:
1. Direct `r.set()` with TTL (replaces `cache.set_value()`)
2. Pubsub publish for FastAPI in-process caches (same as current)

## `get_fastapi_redis()` Scope Audit (Claude's Discretion)

### Full Event Handler Redis Usage Audit

| Event Handler | Redis Client | Writes FastAPI-Consumed Data? | Fix Needed? |
|---------------|-------------|-------------------------------|-------------|
| `access_sync.py` | `get_fastapi_redis()` | Yes (access grants) | NO |
| `device_sync.py` | `get_fastapi_redis()` | Yes (devices, sessions) | NO |
| `catalog_sync.py` | `get_fastapi_redis()` | Yes (catalog cache) | NO |
| `build_trigger.py` | `frappe.cache` (debounce only) + `get_fastapi_redis()` (hierarchy) | Debounce keys are Frappe-only; hierarchy is correctly using get_fastapi_redis | NO |
| `profile_sync.py` | `frappe.cache()` | Yes (profile cache, pubsub) | **YES** |
| `plan_change_sync.py` | `frappe.cache()` | Yes (session, pubsub) | **YES** |
| `purchase_sync.py` | None (Frappe desk + email only) | No | NO |

**Conclusion:** Only `profile_sync.py` and `plan_change_sync.py` need Redis client migration. `build_trigger.py` uses `frappe.cache` (property, not function call) for debounce keys, which is fine because debounce keys are Frappe-internal (not consumed by FastAPI).

**Note on `build_trigger.py`:** It uses `frappe.cache` (the Redis property, not `frappe.cache()` function) for debounce keys. These keys (`memora:build:pending:*`) are only used within Frappe's scheduler to prevent duplicate build queue entries. They are never read by FastAPI. No change needed.

## Admin vs Player Distinction

### When Admins Trigger Player Events

Event handlers fire on `Memora Player Profile` doc_events. The `doc` is always a Player Profile document (PLAYER-##### docname). The admin is `frappe.session.user` (an email).

| Event | Triggered By | `doc.name` | `frappe.session.user` | Action |
|-------|-------------|-----------|----------------------|--------|
| Profile update | Admin editing form | PLAYER-00001 | admin@example.com | Use `doc.name` for Redis keys |
| Device removal | Admin removing device | PLAYER-00001 | admin@example.com | Use `doc.name` for Redis keys |
| Plan change | Admin changing plan | PLAYER-00001 | admin@example.com | Use `doc.name` for session invalidation |
| Profile update | Player via API | PLAYER-00001 | (FastAPI, no Frappe session) | Event triggered by API, doc.name still correct |

**Key insight:** `doc.name` always returns the correct PLAYER-##### identity regardless of who triggered the event. The old `doc.user` was needed because the docname was the email (old autoname was `field:user`). With `autoname: PLAYER-.#####.`, `doc.name` IS the identity key. No special admin/player distinction logic is needed in event handlers.

### Where Admin vs Player Matters

Only in the `api/devices.py` and `api/profile.py` APIs, where the `player_name` parameter is passed explicitly from the admin form (via `frm.doc.name` in JavaScript). These already pass docname correctly.

## Architecture Patterns

### Before vs After: Event Handler Pattern

**Before (current):**
```python
# profile_sync.py -- WRONG: uses frappe.cache() + doc.user
def on_player_profile_updated(doc, method):
    cache = frappe.cache()              # WRONG Redis
    redis_key = f"memora:profile:{doc.user}"  # WRONG identity
    profile_data = {"player_id": doc.user, ...}  # WRONG identity
    cache.set_value(redis_key, json.dumps(profile_data), expires_in_sec=CACHE_TTL)
    cache.publish("memora:cache:invalidate", json.dumps({
        "type": "profile", "player_id": doc.user, ...
    }))
```

**After (migrated):**
```python
# profile_sync.py -- FIXED: uses get_fastapi_redis() + doc.name
from memora_admin.events.access_sync import get_fastapi_redis

def on_player_profile_updated(doc, method):
    # Player identity is PLAYER-##### docname (not email)
    r = get_fastapi_redis()
    redis_key = f"memora:profile:{doc.name}"
    profile_data = {
        "player_id": doc.name,
        "display_name": doc.display_name or "",
        "avatar": doc.avatar or "default_avatar",
    }
    # Two-pronged invalidation (same pattern as catalog_sync.py)
    # 1. Direct SET with TTL (immediate effect)
    r.set(redis_key, json.dumps(profile_data), ex=CACHE_TTL)
    # 2. Pubsub for FastAPI in-process caches
    r.publish("memora:cache:invalidate", json.dumps({
        "type": "profile",
        "player_id": doc.name,
        "timestamp": time.time(),
    }))
    frappe.logger().info(f"Profile {doc.name} synced to Redis")
```

### Before vs After: Frappe API Pattern

**Before (current):**
```python
# purchase.py -- WRONG: looks up by {"user": user_id}
player_id = frappe.get_value("Memora Player Profile", {"user": user_id}, "name")
```

**After (migrated):**
```python
# purchase.py -- FIXED: user_id IS the docname (PLAYER-#####)
# Player identity is PLAYER-##### docname from JWT sub claim
if not frappe.db.exists("Memora Player Profile", user_id):
    frappe.throw("Player profile not found", frappe.DoesNotExistError)
player_id = user_id
```

### Before vs After: Subscription API Hydration

**Before (current):**
```python
# subscriptions.py -- WRONG: dual lookup with user field fallback
keys = frappe.get_all("Memora Player Subscription",
    filters={"player": player_id, "is_active": 1}, pluck="access_key")
if keys:
    return keys
# Fallback: look up via {"user": player_id}
profile_name = frappe.db.get_value("Memora Player Profile", {"user": player_id}, "name")
```

**After (migrated):**
```python
# subscriptions.py -- FIXED: player_id is always docname
# Player identity is PLAYER-##### docname (from JWT sub)
keys = frappe.get_all("Memora Player Subscription",
    filters={"player": player_id, "is_active": 1}, pluck="access_key")
return keys or []
```

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| Redis connection for Frappe->FastAPI shared data | Custom Redis connection | `get_fastapi_redis()` from `access_sync.py` | Established pattern, loads correct `REDIS_URL` from `.env` |
| Cache invalidation | Single-method invalidation | Two-pronged pattern (direct delete + pubsub) | Handles both Redis cache and FastAPI in-process caches |
| Schema migration | Manual SQL ALTER TABLE | Remove field from JSON, `bench migrate` | Frappe handles column removal automatically |

## Common Pitfalls

### Pitfall 1: `frappe.cache()` vs `get_fastapi_redis()` -- Wrong Redis Namespace
**What goes wrong:** `frappe.cache()` adds `{site_name}|` prefix to all keys. FastAPI reads keys without prefix. Data written by Frappe never appears in FastAPI's Redis namespace.
**Why it happens:** `frappe.cache()` is the "natural" Redis wrapper in Frappe code.
**How to avoid:** Always use `get_fastapi_redis()` for data consumed by FastAPI. Import from `access_sync.py`.
**Warning signs:** Redis keys that exist in Frappe's namespace (check with `redis-cli KEYS "x_conanacademy_com|memora:*"`) but not in plain namespace.

### Pitfall 2: `doc.user` Returns None/Empty for New Players (Silent Failure)
**What goes wrong:** `doc.user` on a Frappe Document returns `None` or empty string if the field doesn't exist in schema. Redis operations silently succeed with keys like `memora:access:None`.
**Why it happens:** Frappe Document access to non-existent fields doesn't raise `AttributeError`.
**How to avoid:** Replace ALL `doc.user` with `doc.name` before removing the `user` field from schema. Verify with grep.
**Warning signs:** Redis keys containing `:None` or empty segments.

### Pitfall 3: Schema Removal Before Code Migration
**What goes wrong:** If the `user` field is removed from the JSON schema before all code references are updated, event handlers silently fail (write to wrong Redis keys with None values).
**Why it happens:** Eager cleanup.
**How to avoid:** Update ALL code references first, verify with grep, THEN remove the field from schema. Or do both atomically in the same plan.

### Pitfall 4: Forgetting Scheduled Tasks
**What goes wrong:** `profile_cache.py` and `fsrs_processor.py` still query `pp.user` which returns NULL for PLAYER-##### profiles. Profile cache pre-warming fails silently; FSRS processor skips players.
**Why it happens:** Tasks run hourly/minutely and aren't manually tested.
**How to avoid:** Include scheduled tasks in the audit. Search for `"user"` across ALL Python files, not just `events/` and `api/`.

### Pitfall 5: Access Sync Indirection
**What goes wrong:** `access_sync.py` has `player_doc = frappe.get_doc("Memora Player Profile", player_id)` then `user_id = player_doc.user`. With PLAYER-##### naming, `doc.player` in the subscription IS the docname, so no lookup is even needed.
**Why it happens:** The indirection was necessary when `autoname` was `field:user` (docname = email, but Redis key needed email from the `user` field). Now `doc.name` IS the Redis identity.
**How to avoid:** Simplify `access_sync.py`: use `doc.player` directly as `user_id` instead of looking up the Player Profile document.

### Pitfall 6: `cache.delete_value()` vs `r.delete()`
**What goes wrong:** `frappe.cache().delete_value(key)` adds site prefix before deleting. Even with the correct key name, the delete targets the wrong key in Redis.
**Why it happens:** Using Frappe Redis wrapper methods.
**How to avoid:** Use `r.delete(key)` with `r = get_fastapi_redis()`.

## State of the Art

| Old Approach | Current Approach | When Changed | Impact |
|--------------|------------------|--------------|--------|
| `autoname: "field:user"` (email as docname) | `autoname: "PLAYER-.#####."` | Phase 29 (2026-02-12) | Identity decoupled from email |
| `doc.user` for Redis identity | `doc.name` for Redis identity | Phase 32 (this phase) | All Redis keys use PLAYER-##### |
| `{"user": player_id}` lookups | Direct docname lookup or `{"mobile": phone}` | Phase 32 (this phase) | No more indirect lookups |
| `frappe.cache()` for shared data | `get_fastapi_redis()` | Phase 16 (device_sync fix) | Correct Redis namespace |

## Specific Change Details

### access_sync.py (Simplification Opportunity)

The current `on_subscription_change` and `on_subscription_deleted` do unnecessary work:
```python
# Current: Looks up Player Profile to get user field
player_doc = frappe.get_doc("Memora Player Profile", player_id)
user_id = player_doc.user
```

With PLAYER-##### naming, `doc.player` in the subscription IS the PLAYER-##### docname. The simplification:
```python
# Simplified: doc.player is already the identity key
user_id = doc.player  # PLAYER-##### docname
```

This eliminates a `frappe.get_doc()` call per subscription change, improving performance.

### plan_change_sync.py (Full Rewrite Needed)

This file needs all three changes simultaneously:
1. `frappe.cache()` -> `get_fastapi_redis()`
2. `doc.user` -> `doc.name`
3. `cache.delete_value()` -> `r.delete()`
4. `cache.publish()` -> `r.publish()`

### profile_sync.py (Full Rewrite Needed)

Same as above, plus:
1. `cache.set_value()` -> `r.set(key, data, ex=TTL)`
2. `doc.user` -> `doc.name` in all 4 locations

### api/devices.py (profile.user -> profile.name)

Two functions affected:
- `sync_devices_from_redis()`: Line 51 `user_id = profile.user`
- `remove_device()`: Line 127 `user_id = profile.user`

Change to `user_id = profile.name` (or just `user_id = player_name` since the docname is passed directly).

### Schema Removal

Remove `user` field from `memora_player_profile.json`:
- Remove from `field_order` array
- Remove field object from `fields` array
- Run `bench migrate` to apply

## Open Questions

1. **Leaderboard player IDs**
   - What we know: Leaderboard ZSET members are player IDs from `user.sub` in JWT. For new PLAYER-##### players, these are correct. Old test data may have email-based IDs in leaderboard ZSETs.
   - What's unclear: Whether old leaderboard data needs cleanup.
   - Recommendation: Don't clean up. Per decision "leave test data alone." New players will use PLAYER-##### consistently. Old leaderboard entries with emails will naturally age out (daily/weekly reset) or remain as historical oddities in alltime.

2. **`profile_cache.py` Redis connection**
   - What we know: It uses `redis.from_url(frappe.conf.redis_cache)` which may or may not be the same as FastAPI's Redis.
   - What's unclear: Whether `frappe.conf.redis_cache` resolves to the same Redis as `REDIS_URL` in `.env`.
   - Recommendation: Change to use `get_fastapi_redis()` from `access_sync.py` for consistency. Profile cache keys MUST be in the same namespace as FastAPI reads them.

## Sources

### Primary (HIGH confidence)
- Direct codebase audit of all 20+ files
- `memora_admin/events/access_sync.py` -- canonical `get_fastapi_redis()` pattern
- `memora_admin/events/catalog_sync.py` -- canonical two-pronged invalidation pattern
- `memora_admin/events/build_trigger.py` -- `_invalidate_hierarchy_cache()` two-pronged pattern
- `fastapi_app/core/security.py` -- JWT `sub` claim documentation
- `fastapi_app/models/auth.py` -- TokenPayload `sub` field documentation
- `memora_admin/memora_admin/doctype/memora_player_profile/memora_player_profile.json` -- schema with `user` field
- Phase 29 VERIFICATION.md -- confirmed `doc.user` warnings for Phase 32

### Secondary (MEDIUM confidence)
- `.planning/research/PITFALLS_mobile-auth.md` -- pre-existing analysis of `doc.user` and `frappe.cache()` bugs
- `.planning/research/ARCHITECTURE.md` -- architectural analysis of identity migration

## Metadata

**Confidence breakdown:**
- Standard stack: HIGH -- all changes are within existing codebase, no new libraries
- Architecture: HIGH -- patterns already established in other event handlers
- Pitfalls: HIGH -- identified from actual codebase audit, verified against prior research

**Research date:** 2026-02-12
**Valid until:** Indefinite (codebase-specific findings, not library-dependent)
