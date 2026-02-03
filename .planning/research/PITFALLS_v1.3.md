# Pitfalls Research: v1.3 Profile Display Names & Admin Device Management

**Domain:** Adding profile display names to leaderboards + Frappe Desk admin device management UI
**Project:** Memora Admin (FastAPI + Redis + Frappe v15)
**Researched:** 2026-02-03
**Confidence:** HIGH

## Executive Summary

Adding profile display names to leaderboards and admin device management UI to Memora introduces **integration risks with existing systems**. Unlike v1.1 which added new subsystems, v1.3 **modifies existing endpoints** and adds **admin-facing UI**. The critical pitfalls relate to:

1. **N+1 Query Problem** - Fetching display names individually per leaderboard entry kills <20ms target
2. **Profile Cache Staleness** - Display name changes don't invalidate leaderboard caches
3. **Redis-MariaDB Device State Divergence** - Admin removes device from Frappe but Redis registry isn't cleared
4. **Session Invalidation Gap** - Device removed but active JWT sessions continue working
5. **Frappe Child Table Permission Pitfalls** - Admin can't access device child table without parent permission

These pitfalls are **specific to integration** - connecting the new ProfileService to existing LeaderboardService, and wiring Frappe Desk UI to existing Redis device registry.

**Critical recommendation:** Phase 1 must establish batch profile lookup pattern before Phase 2 adds caching complexity.

---

## Critical Pitfalls

### Pitfall 1: N+1 Query Problem in Leaderboard Profile Enrichment

**What goes wrong:**
Naive implementation fetches display names one-by-one for each leaderboard entry, turning O(1) Redis call into O(N) round-trips.

```python
# WRONG - N+1 queries
async def get_leaderboard_with_profiles(lb_type: str, limit: int = 10):
    raw_entries = await leaderboard_service.get_top(lb_type, limit)

    enriched = []
    for entry in raw_entries:
        # One Redis GET per player - 10 queries for top 10!
        profile = await redis.hgetall(f"memora:profile:{entry['player_id']}")
        enriched.append({
            **entry,
            "display_name": profile.get("display_name", entry["player_id"]),
            "avatar_url": profile.get("avatar"),
        })
    return enriched
```

With top 100 leaderboard and 2ms per Redis call: 200ms just for profiles, violating <20ms target.

**Why it happens:**
- Developers iterate over entries naturally
- Easy to miss performance impact at small scale (10 entries = 20ms seems OK)
- Breaks only when limit increases or latency spikes

**How to avoid:**

```python
# RIGHT - Batch fetch with pipeline
async def get_leaderboard_with_profiles(lb_type: str, limit: int = 10):
    raw_entries = await leaderboard_service.get_top(lb_type, limit)
    player_ids = [entry["player_id"] for entry in raw_entries]

    # Batch fetch all profiles in single pipeline
    pipeline = redis.pipeline()
    for player_id in player_ids:
        pipeline.hgetall(f"memora:profile:{player_id}")
    profile_results = await pipeline.execute()

    # Zip results with entries
    profiles_by_id = {
        player_ids[i]: profile_results[i]
        for i in range(len(player_ids))
    }

    return [
        LeaderboardEntry(
            **entry,
            display_name=profiles_by_id.get(entry["player_id"], {}).get(
                "display_name", entry["player_id"]
            ),
            avatar_url=profiles_by_id.get(entry["player_id"], {}).get("avatar"),
        )
        for entry in raw_entries
    ]
```

**Warning signs:**
- Leaderboard response time increases linearly with limit parameter
- Redis `MONITOR` shows sequential HGETALL calls for same request
- p99 latency for `/leaderboard/{type}` exceeds p50 by 10x+

**Phase to address:** Phase 1 (ProfileService) - Must be batch from day 1

---

### Pitfall 2: Profile Cache Staleness After Display Name Change

**What goes wrong:**
Player updates display name in profile, but leaderboard continues showing old name until cache TTL expires.

```
Timeline:
T0: Alice sets display_name = "Alice123", cached in Redis
T1: Alice appears on leaderboard as "Alice123"
T2: Alice changes display_name to "AliceGamer" via profile API
T3: Memora Player Profile updated in MariaDB
T4: Profile cache in Redis still has "Alice123" (TTL not expired)
T5: Leaderboard shows "Alice123" - stale data
T6: (1 hour later) Cache expires, next request shows "AliceGamer"
```

User experience: "I changed my name but leaderboard still shows old name!"

**Why it happens:**
- Cache invalidation is "one of the two hard problems"
- Profile update path (Frappe) is separate from cache (Redis)
- Easy to forget cache invalidation when writing MariaDB update hook

**How to avoid:**

**Option 1: Event-driven invalidation (recommended for Memora)**
```python
# memora_admin/events/profile_sync.py
def on_player_profile_update(doc, method):
    """Invalidate profile cache when display_name or avatar changes."""
    previous = doc.get_doc_before_save()

    if not previous:
        return

    # Check if display-relevant fields changed
    if (doc.display_name != previous.display_name or
        doc.avatar != previous.avatar):

        cache = frappe.cache()
        profile_key = f"memora:profile:{doc.user}"
        cache.delete_value(profile_key)

        frappe.logger().info(
            f"Invalidated profile cache for {doc.user} "
            f"(display_name: {previous.display_name} -> {doc.display_name})"
        )
```

**Option 2: Short TTL with read-through cache**
```python
# ProfileService with short TTL
PROFILE_CACHE_TTL = 300  # 5 minutes - acceptable staleness window

async def get_profile(self, player_id: str) -> dict:
    key = f"memora:profile:{player_id}"
    cached = await self.redis.hgetall(key)

    if cached:
        return cached

    # Cache miss - fetch from Frappe
    profile = await self.frappe.call(
        "memora_admin.api.profile.get_player_profile",
        {"player_id": player_id},
    )

    await self.redis.hset(key, mapping=profile)
    await self.redis.expire(key, PROFILE_CACHE_TTL)

    return profile
```

**Warning signs:**
- Support tickets: "My name shows wrong on leaderboard"
- Tickets cluster shortly after profile update times
- Profile update logs don't have corresponding cache invalidation logs

**Phase to address:** Phase 1 (ProfileService with invalidation), Phase 2 (wire to hooks.py)

---

### Pitfall 3: Redis-MariaDB Device State Divergence

**What goes wrong:**
Admin removes device from Frappe Desk (MariaDB), but Redis device registry isn't updated, causing inconsistent state.

```
Current device_sync.py handles this correctly for on_update hook.
But edge cases exist:

Scenario 1: Direct SQL modification
- DBA runs DELETE FROM `tabMemora Player Device` WHERE device_id = 'xyz'
- No hook fires (bypasses Frappe ORM)
- Redis still has device registered

Scenario 2: Frappe bulk delete
- Admin uses "Delete" action from list view on multiple devices
- on_update hook may not fire for all deletions
- Partial Redis cleanup

Scenario 3: Hook execution failure
- on_player_profile_update fires but Redis connection fails
- MariaDB device removed, Redis device remains
- No retry mechanism
```

**Why it happens:**
- Frappe hooks are best-effort, not transactional
- Direct database access bypasses ORM hooks
- No reconciliation mechanism between Redis and MariaDB

**How to avoid:**

**Pattern 1: Defensive Redis cleanup in admin endpoint (recommended)**
```python
# Add explicit sync before returning device list to admin
async def get_devices_for_admin(player_id: str) -> list[DeviceInfo]:
    """Get devices with MariaDB as source of truth."""
    # Fetch from MariaDB (authoritative)
    profile = frappe.get_doc("Memora Player Profile", player_id)
    mariadb_devices = {d.device_id for d in profile.authorized_devices}

    # Fetch from Redis
    redis_devices = await device_service.get_devices(player_id)
    redis_device_ids = {d.device_id for d in redis_devices}

    # Reconcile: remove Redis-only devices (orphaned)
    orphaned = redis_device_ids - mariadb_devices
    if orphaned:
        for device_id in orphaned:
            await device_service.remove_device(player_id, device_id)
        frappe.logger().warning(
            f"Removed {len(orphaned)} orphaned devices from Redis for {player_id}"
        )

    # Return MariaDB devices with Redis metadata
    return [d for d in redis_devices if d.device_id in mariadb_devices]
```

**Pattern 2: Scheduled reconciliation task**
```python
# tasks/device_reconcile.py
def reconcile_device_registries():
    """Hourly task to sync Redis device state with MariaDB."""
    profiles = frappe.get_all(
        "Memora Player Profile",
        fields=["user"],
    )

    for profile in profiles:
        player_id = profile.user
        # ... reconciliation logic per player
```

**Warning signs:**
- Player sees "3 devices registered" in app but Frappe shows 2
- Logs show device removal from MariaDB but no Redis deletion
- `redis-cli HGETALL memora:devices:{player_id}` shows more devices than MariaDB

**Phase to address:** Phase 2 (Admin Device Management) - Build reconciliation into admin view

---

### Pitfall 4: Session Invalidation Gap After Device Removal

**What goes wrong:**
Admin removes device via Frappe Desk, device is deleted from Redis registry, but user's active JWT session continues working until token expires.

```
Timeline:
T0: User logged in with device_id="abc", JWT expires in 24 hours
T1: Admin removes device "abc" via Frappe Desk
T2: device_sync.py removes device from Redis and invalidates session
T3: BUT - JWT is stateless, doesn't check session on every request
T4: User with old JWT can still make API calls for 24 more hours

Existing mitigation in device_sync.py:
cache.delete_value(session_key)  # Deletes memora:session:{user_id}

Problem: Current JWT validation doesn't check session existence!
```

Looking at existing `get_current_user` in deps.py:
```python
async def get_current_user(credentials: ...) -> TokenPayload:
    """Stateless JWT verification - no database lookup per CONTEXT.md."""
    token = credentials.credentials
    payload = decode_token(token, verify_type="access")
    return TokenPayload(**payload)
    # ^ No session validation! Completely stateless.
```

**Why it happens:**
- Stateless JWT design intentionally avoids Redis lookup per request
- Session invalidation was designed for token refresh, not active access tokens
- Admin device removal is new use case not covered in v1.0 design

**How to avoid:**

**Option 1: Token blocklist (recommended for admin actions)**
```python
# When admin removes device, add token family_id to blocklist
def on_player_profile_update(doc, method):
    """Invalidate session when admin removes device."""
    # ... existing device removal logic ...

    # Add family_id to short-lived blocklist (24 hours = access token lifetime)
    cache = frappe.cache()
    blocklist_key = f"memora:token_blocklist:{doc.user}"

    # Get current family_id from session (if exists)
    session_key = f"memora:session:{doc.user}"
    session_data = cache.hgetall(session_key)

    if session_data and session_data.get("family_id"):
        family_id = session_data["family_id"]
        cache.sadd(blocklist_key, family_id)
        cache.expire(blocklist_key, 86400)  # 24 hours

    cache.delete_value(session_key)

# In deps.py - add blocklist check
async def get_current_user(credentials: ...) -> TokenPayload:
    payload = decode_token(token, verify_type="access")
    token_payload = TokenPayload(**payload)

    # Check blocklist for admin-revoked tokens
    blocklist_key = f"memora:token_blocklist:{token_payload.sub}"
    if await redis.sismember(blocklist_key, token_payload.fid):
        raise HTTPException(status_code=401, detail="Token revoked")

    return token_payload
```

**Option 2: Force re-login with short access token (simpler)**
```python
# Reduce access token lifetime from 24h to 15 minutes
# Refresh token still 7 days
# When session invalidated, refresh will fail, forcing re-login within 15 min
```

**Warning signs:**
- After admin removes device, user reports "I can still use the app"
- Security audit finds removed device still making API calls
- Logs show API requests from device_id that was removed 2 hours ago

**Phase to address:** Phase 2 (Admin Device Management) - Critical security consideration

---

### Pitfall 5: Frappe Child Table Permission Issues

**What goes wrong:**
Admin user can view Memora Player Profile but gets permission error accessing authorized_devices child table, or worse, unauthorized users can query child table directly.

```
Frappe child table security model:
- Child DocTypes (istable=1) inherit permissions from parent
- BUT: frappe.db.get_list() on child table checks child permissions directly
- If no explicit permissions on child, can cause errors

Current Memora Player Device schema:
"permissions": []  # Empty! No explicit permissions

Potential issues:
1. Admin tries to list all devices: frappe.get_all("Memora Player Device")
   → May fail with permission error

2. Attacker queries child table directly via API:
   GET /api/resource/Memora Player Device?filters=[["device_id","=","known_id"]]
   → May expose device data without parent access
```

**Why it happens:**
- Child table permission model is confusing in Frappe
- Empty permissions array means "inherit from parent" but also "no direct access"
- API endpoints may not properly validate parent access

**How to avoid:**

**Option 1: Explicit child table permissions (recommended)**
```json
// memora_player_device.json
"permissions": [
  {
    "read": 1,
    "write": 1,
    "role": "System Manager"
  }
]
```

**Option 2: Access through parent only (current design, verify it works)**
```python
# Always access devices through parent profile
def get_player_devices(player_id: str) -> list:
    """Get devices via parent document."""
    profile = frappe.get_doc("Memora Player Profile", player_id)
    # Frappe checks permission on parent automatically
    return profile.authorized_devices
```

**Option 3: Custom permission check**
```python
@frappe.whitelist()
def get_devices_for_player(player_id: str):
    """Admin endpoint to get devices with explicit permission check."""
    if not frappe.has_permission("Memora Player Profile", "read", player_id):
        frappe.throw(_("Not permitted"), frappe.PermissionError)

    profile = frappe.get_doc("Memora Player Profile", player_id)
    return [
        {
            "device_id": d.device_id,
            "device_name": d.device_name,
            "platform": d.platform,
            "last_login": d.last_login,
        }
        for d in profile.authorized_devices
    ]
```

**Warning signs:**
- Admin clicks on player profile, devices table shows error
- API error logs show "Insufficient Permission for Memora Player Device"
- Security scan finds direct child table access possible without parent permission

**Phase to address:** Phase 2 (Admin Device Management) - Test permissions thoroughly

---

## Moderate Pitfalls

### Pitfall 6: Display Name Privacy Exposure

**What goes wrong:**
Player sets display_name expecting privacy, but leaderboard exposes it publicly. GDPR/privacy concerns for minors in educational platform.

**Memora context:**
- Target audience: Arabic-speaking students (potentially minors)
- Leaderboards are public within the platform
- Display names visible to all users

**Prevention:**
```python
# Option 1: Privacy flag on profile
class MemoraPlayerProfile:
    fields = [
        # ...
        {"fieldname": "leaderboard_visible", "fieldtype": "Check", "default": 1},
    ]

# In ProfileService
async def get_leaderboard_profile(self, player_id: str) -> dict:
    profile = await self.get_profile(player_id)

    if not profile.get("leaderboard_visible", True):
        # Anonymize
        return {
            "display_name": f"Player_{player_id[:8]}",
            "avatar_url": "/assets/default_avatar.png",
        }
    return profile

# Option 2: Default to anonymous, opt-in for display
# Safer for minors, per GDPR Privacy by Default
```

**Phase to address:** Phase 1 (Profile Model) - Consider privacy-by-default

---

### Pitfall 7: Avatar URL Injection/XSS

**What goes wrong:**
If avatar field allows URL input, malicious user injects XSS or tracks other users.

```
Current schema:
"avatar": {"fieldtype": "Select", "options": "avatar 1\navatar 2"}

This is SAFE - Select field with fixed options.

But if changed to Image/Attach field:
avatar = "javascript:alert(1)"  # XSS
avatar = "https://tracker.evil.com/pixel.gif?user=..."  # Tracking
```

**Prevention:**
- Keep avatar as Select with predefined options (current approach - SAFE)
- If allowing custom images later, use AttachImage with server-side validation
- Never render user-provided URLs directly in frontend

**Phase to address:** N/A (current design is safe, document for future changes)

---

### Pitfall 8: Leaderboard Response Size Bloat

**What goes wrong:**
Adding display_name + avatar_url to leaderboard entries increases response size, impacting mobile performance.

```
Before (current):
{
  "rank": 1,
  "player_id": "user_abc123",
  "xp": 5000,
  "is_me": false
}
# ~60 bytes per entry, 6KB for top 100

After (with profiles):
{
  "rank": 1,
  "player_id": "user_abc123",
  "display_name": "Ahmed Al-Rashid Gaming Pro 2026",  # Up to 50 chars
  "avatar_url": "/assets/memora_admin/images/avatars/avatar_15.png",  # ~60 chars
  "xp": 5000,
  "is_me": false
}
# ~200 bytes per entry, 20KB for top 100
```

For mobile users in Jordan with limited bandwidth, 3x response size matters.

**Prevention:**
```python
# Option 1: Truncate display_name in response
display_name=profile.get("display_name", "")[:20]

# Option 2: Separate endpoint for full profiles
GET /leaderboard/daily  # Basic data
GET /leaderboard/daily/profiles  # With full profile data

# Option 3: CDN-hosted avatar map
# Return avatar_id instead of URL, client resolves locally
{
  "avatar_id": "avatar_15",  # 10 chars vs 60 chars
}
```

**Phase to address:** Phase 1 (API Design) - Consider response size from start

---

## Technical Debt Patterns

| Shortcut | Immediate Benefit | Long-term Cost | When Acceptable |
|----------|-------------------|----------------|-----------------|
| Inline profile fetch (N+1) | Faster to implement | Performance death at scale | Never for leaderboards |
| No cache invalidation | Simpler code | Stale display names | Only with <5min TTL |
| Frappe-only device management | Uses existing UI | No Redis sync | Only if Redis is cache, not source |
| Stateless JWT with no blocklist | Simpler auth | Revoked devices still work | Only with <15min access token |

## Integration Gotchas

| Integration | Common Mistake | Correct Approach |
|-------------|----------------|------------------|
| ProfileService + LeaderboardService | Sequential calls in endpoint | Inject ProfileService into LeaderboardService, batch fetch |
| Frappe hooks + Redis | Assume hooks always fire | Scheduled reconciliation task as backup |
| Admin UI + FastAPI | Two separate device views | Single source of truth (MariaDB), Redis as cache |
| JWT + device removal | Assume session invalidation works | Add token blocklist for admin revocations |

## Performance Traps

| Trap | Symptoms | Prevention | When It Breaks |
|------|----------|------------|----------------|
| N+1 profile queries | Latency scales with limit | Pipeline/MGET batch | limit > 10 |
| Unbounded profile cache | Memory grows forever | TTL on all profile keys | 100K+ players |
| Profile cache miss storm | Frappe overloaded after Redis restart | Staggered TTL + rate limit on Frappe calls | Mass cache expiry |
| Large leaderboard response | Mobile timeout | Pagination + response size limits | Top 100 with full profiles |

## Security Mistakes

| Mistake | Risk | Prevention |
|---------|------|------------|
| Direct child table API access | Device data exposure | Explicit permissions on Memora Player Device |
| No token blocklist for admin actions | Revoked devices continue working | Add blocklist check for admin-revoked tokens |
| Display name as user-provided HTML | XSS in leaderboard | Escape all display_name in templates/JSON |
| Avatar URL from user input | XSS/tracking | Keep Select field, don't allow custom URLs |

## "Looks Done But Isn't" Checklist

- [ ] **Profile enrichment:** Test with `limit=100` - timing should be <20ms, not 200ms
- [ ] **Cache invalidation:** Change display_name in Frappe, verify leaderboard updates immediately
- [ ] **Device removal:** Remove via Frappe Desk, verify Redis `HGETALL memora:devices:{id}` reflects change
- [ ] **Session invalidation:** Remove device, verify user gets 401 on next API call (may need blocklist)
- [ ] **Permissions:** As non-admin user, try `GET /api/resource/Memora Player Device` - should fail
- [ ] **Privacy:** If privacy flag added, verify anonymous players show generic name on leaderboard

## Recovery Strategies

| Pitfall | Recovery Cost | Recovery Steps |
|---------|---------------|----------------|
| N+1 queries deployed | LOW | Refactor to batch fetch, deploy hotfix |
| Stale display names | LOW | Clear profile cache: `redis-cli KEYS "memora:profile:*" | xargs redis-cli DEL` |
| Redis-MariaDB device divergence | MEDIUM | Run reconciliation task, audit affected users |
| Revoked device still working | MEDIUM | Shorten access token TTL, force re-login |
| Child table permission error | LOW | Add explicit permissions to JSON, migrate |

## Pitfall-to-Phase Mapping

| Pitfall | Prevention Phase | Verification |
|---------|------------------|--------------|
| N+1 Query Problem | Phase 1: ProfileService | Load test with limit=100, measure timing |
| Profile Cache Staleness | Phase 1: Invalidation hooks | Update name in Frappe, check leaderboard immediately |
| Redis-MariaDB Device Divergence | Phase 2: Admin device view | Remove device via SQL, verify Redis updated |
| Session Invalidation Gap | Phase 2: Token blocklist | Remove device, verify 401 response |
| Frappe Permission Issues | Phase 2: Permission config | Test admin and non-admin access patterns |
| Display Name Privacy | Phase 1: Profile model | Verify opt-out hides name on leaderboard |

---

## Sources

### Profile Caching & N+1 Problem
- [Leaderboard System Design - System Design](https://systemdesign.one/leaderboard-system-design/) - Metadata caching patterns
- [Redis Leaderboards Official](https://redis.io/solutions/leaderboards/) - Profile data alongside sorted sets
- [Avoiding Common Database Caching Mistakes](https://moldstud.com/articles/p-avoid-common-database-caching-mistakes-for-performance) - Expiration and invalidation

### Cache Invalidation
- [Top 10 Common Caching Mistakes](https://moldstud.com/articles/p-top-10-common-caching-mistakes-to-avoid-for-enhanced-performance) - "70% of users frustrated by stale data"
- [Caching Part-1: Sync, Race Conditions, Cache Invalidation](https://gajabagi.medium.com/caching-part-1-a-deep-dive-into-sync-race-conditions-and-the-timeline-fallacy-41cb10bbffe8) - Event-driven invalidation

### Redis-MariaDB Consistency
- [How to Ensure Consistency Between Redis and Database](https://medium.com/better-programming/how-to-ensure-the-consistency-between-redis-and-database-62f09de0bdde) - Dual-write patterns
- [Preventing Database Race Conditions with Redis](https://iniakunhuda.medium.com/hands-on-preventing-database-race-conditions-with-redis-2c94453c1e47) - Atomic operations

### Session Revocation
- [Session Management - OWASP Cheat Sheet](https://cheatsheetseries.owasp.org/cheatsheets/Session_Management_Cheat_Sheet.html) - Server-side invalidation required
- [Broken Session Management Vulnerability](https://knowledge-base.secureflag.com/vulnerabilities/broken_authentication/broken_session_management_vulnerability.html) - Client-side only invalidation is insufficient
- [WorkOS Sessions API - Revocation](https://workos.com/blog/workos-sessions-api-session-revocation-sign-out-everywhere) - Blocklist patterns

### Frappe Permissions
- [Permission Issue for Child Table in v14](https://github.com/frappe/erpnext/issues/34925) - frappe.db.get_list permission errors
- [No Permissions on Child Tables](https://github.com/frappe/erpnext/issues/16008) - Security concern with direct access
- [Child / Table DocType Documentation](https://docs.frappe.io/framework/user/en/basics/doctypes/child-doctype) - Official guidance

### Privacy & GDPR
- [Privacy by Design & Default (GDPR) 2025](https://secureprivacy.ai/blog/privacy-by-design-gdpr-2025) - Default privacy settings requirement
- [GDPR Compliance with Public User Profiles](https://meta.discourse.org/t/gdpr-compliance-when-using-public-user-profiles/95059) - Opt-in for public visibility

---

**Research completed:** 2026-02-03
**Confidence level:** HIGH (verified against existing codebase patterns, Frappe documentation, industry sources)
**Downstream:** Use in roadmap creation for v1.3 milestone (phase ordering, success criteria)
