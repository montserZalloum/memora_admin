# Feature Research: v1.3 Profile Display Names & Admin Device Management

**Milestone:** v1.3 Leaderboard Profiles & Admin Device Management
**Features:** Profile display names in leaderboards, Admin device management via Frappe Desk
**Researched:** 2026-02-03
**Overall Confidence:** HIGH (based on existing codebase analysis and industry patterns)

---

## Executive Summary

This research covers expected behavior for two feature areas in v1.3: profile display names in leaderboards and admin device management. Key findings:

**Profile Display Names:** Leaderboards currently return `player_id` as placeholder for `display_name`. Users expect human-readable names, not UUIDs. The Memora Player Profile DocType already has `display_name` and `avatar` fields. Implementation requires a ProfileService with Redis caching for sub-2ms lookups and batch operations to avoid N+1 queries.

**Admin Device Management:** Device registration (3-device limit) is already built in FastAPI. The Memora Player Profile DocType has an `authorized_devices` child table (Memora Player Device). Admin needs: (1) view devices synced from Redis, (2) remove devices via child table deletion triggering Redis removal. User-facing device management is explicitly out of scope for v1.3.

---

## Profile Display Names in Leaderboards

### Table Stakes

Features users expect when viewing leaderboards.

| Feature | Description | Complexity | Notes |
|---------|-------------|------------|-------|
| **Display name instead of player_id** | Human-readable names on leaderboard entries | LOW | Placeholder already exists at line 66 in leaderboard.py |
| **Consistent naming across leaderboard types** | Same display_name in daily/weekly/alltime | LOW | Single ProfileService handles all lookups |
| **Graceful handling of missing profiles** | New users or data gaps don't break leaderboard | LOW | Fallback to "Player XXXX" or truncated player_id |
| **Avatar indicator** | Optional avatar_url for visual distinction | LOW | DocType has `avatar` Select field ("avatar 1", "avatar 2") |
| **Self-identification marker** | User finds themselves via `is_me` flag | ALREADY BUILT | Endpoint sets `is_me=True` for requesting user |

**Implementation Pattern (Recommended):**
```
ProfileService:
  - batch_get(player_ids: list[str]) -> dict[str, ProfileData]
  - get(player_id: str) -> ProfileData | None

Cache Key: memora:profile:{user_id}
Structure: Redis hash
TTL: 3600 seconds (1 hour)
Fields:
  - display_name: string
  - avatar: string (avatar identifier, not URL)
  - cached_at: ISO timestamp

Population:
  - On login success (after Frappe auth)
  - On cache miss (query Frappe, populate with TTL)
```

**Current State in Codebase:**
```python
# fastapi_app/api/v1/endpoints/leaderboard.py lines 62-72
entries = [
    LeaderboardEntry(
        rank=entry["rank"],
        player_id=entry["player_id"],
        display_name=entry["player_id"],  # Placeholder: profile lookup in future phase
        xp=entry["xp"],
        avatar_url=None,  # Placeholder: profile lookup in future phase
        is_me=entry["player_id"] == user.sub,
    )
    for entry in raw_entries
]
```

### Differentiators

Features that add polish beyond table stakes.

| Feature | Value Proposition | Complexity | Notes |
|---------|-------------------|------------|-------|
| **Profile cache in Redis** | Sub-2ms lookup vs ~50ms Frappe query | MEDIUM | Essential at scale |
| **Batch profile lookup** | Single round-trip for 10-100 entries | LOW | Redis MGET or pipeline |
| **Cache warm-up on login** | Profile always cached for active users | LOW | Write to cache after successful auth |

### Anti-Features

Features to explicitly NOT build for v1.3.

| Anti-Feature | Why Requested | Why Problematic | Alternative |
|--------------|---------------|-----------------|-------------|
| **Real-time profile updates** | "Name should update instantly" | Cache invalidation complexity; profile changes are rare | 1-hour TTL is acceptably stale |
| **Profile lookup per entry (no cache)** | "Always show latest name" | N+1 query pattern kills performance | Batch lookup with cache |
| **Anonymous leaderboard option** | "Privacy-conscious users" | Complexity, abuse potential, reduces engagement | Out of scope; use display_name anonymization if needed later |
| **User-settable display names** | "Let users pick their name" | Requires moderation, profanity filter | Admin-set in DocType for v1.3 |

---

## Admin Device Management

### Table Stakes

Features admins expect when managing player devices.

| Feature | Description | Complexity | Notes |
|---------|-------------|------------|-------|
| **View all devices for a player** | See device list before taking action | LOW | DeviceService.get_devices() exists; child table in DocType |
| **Remove specific device** | Free up slot when user contacts support | LOW | DeviceService.remove_device() exists; needs Frappe hook |
| **Device identification info** | Device name, platform, last_login visible | ALREADY BUILT | Memora Player Device has all fields |
| **Confirmation before removal** | Prevent accidental device deletion | LOW | Standard Frappe confirm dialog |
| **Sync devices from Redis to Frappe** | Admin view reflects current Redis state | MEDIUM | Sync on profile form load |

**Current DocType Structure (Memora Player Device):**
```json
{
  "fields": [
    {"fieldname": "device_id", "fieldtype": "Data", "label": "Device ID", "reqd": 1},
    {"fieldname": "device_name", "fieldtype": "Data", "label": "Device Name"},
    {"fieldname": "last_login", "fieldtype": "Datetime", "label": "Last Login"},
    {"fieldname": "user_agent", "fieldtype": "Small Text", "label": "User Agent"},
    {"fieldname": "platform", "fieldtype": "Select", "options": "Web\niOS\nAndroid"},
    {"fieldname": "push_token", "fieldtype": "Text", "label": "Push Token"}
  ],
  "istable": 1  // Child table, not standalone
}
```

**Current DeviceService Methods (Already Built):**
```python
# fastapi_app/services/device.py

async def get_devices(self, user_id: str) -> list[DeviceInfo]:
    """Get all registered devices for user."""
    # Parses Redis hash into DeviceInfo objects

async def remove_device(self, user_id: str, device_id: str) -> bool:
    """Remove a specific device from user's registry."""
    # Deletes device fields from Redis hash

async def validate_device(self, user_id: str, device_id: str) -> bool:
    """Check if device is registered for user."""
    # O(1) HEXISTS check
```

**Implementation Pattern (Recommended):**
```
Sync Flow (on profile form load):
1. Frappe form loads Memora Player Profile
2. Client script calls API to get devices from Redis
3. API calls DeviceService.get_devices(user_id)
4. Response populates child table (replacing existing rows)

Removal Flow (on child table row delete):
1. Admin deletes row in child table
2. Before delete hook extracts device_id
3. Hook calls FastAPI endpoint or internal service
4. FastAPI calls DeviceService.remove_device()
5. (Optional) FastAPI calls SessionService.invalidate_session()
```

### Differentiators

| Feature | Value Proposition | Complexity | Notes |
|---------|-------------------|------------|-------|
| **Session invalidation on removal** | Removed device loses access immediately | MEDIUM | Call SessionService from Frappe hook |
| **Device count badge** | Quick visibility of devices used vs limit | LOW | Display in Frappe form header |
| **Last login sorting** | Most/least recent devices for removal decisions | LOW | Order child table by last_login desc |

### Anti-Features

| Anti-Feature | Why Requested | Why Problematic | Alternative |
|--------------|---------------|-----------------|-------------|
| **User-facing device management** | "Let users remove their own devices" | Security risk, support burden for accidents | Admin-only for v1.3; defer to future milestone |
| **Fingerprint display to admin** | "Show fingerprint for debugging" | Implementation detail, no admin action needs it | Show device_name + platform + last_login only |
| **Remove all devices button** | "Quick reset" | Too destructive, no confirmation prevents misuse | Remove one device at a time |
| **Push notification on removal** | "Notify user when device removed" | Push infra not built yet | Future milestone after push notifications |
| **Bidirectional sync (Frappe -> Redis)** | "Admin adds device manually" | Devices only registered via login flow | Redis is source of truth; Frappe is view + delete |

---

## Feature Dependencies

```
[Profile Display Names]
    |
    +--requires--> [ProfileService]
    |                  |
    |                  +--requires--> [Profile Cache in Redis]
    |                                     |
    |                                     +--populated-by--> [Login flow]
    |
    +--enhances--> [Leaderboard Endpoints] (already built)

[Admin Device Management]
    |
    +--requires--> [DeviceService.get_devices()] (already built)
    |
    +--requires--> [DeviceService.remove_device()] (already built)
    |
    +--requires--> [Frappe Hook on Child Table Delete] (needs build)
    |
    +--requires--> [Redis-to-Frappe Sync on Form Load] (needs build)
    |
    +--optionally--> [SessionService.invalidate_session()] (P2)

[Profile Cache] --independent-of-- [Admin Device Management]
```

### Dependency Notes

- **ProfileService requires Profile Cache:** Looking up profiles without caching creates N+1 queries. Cache must be populated before leaderboard requests at scale.
- **Admin Device Removal requires Frappe Hook:** DeviceService.remove_device() only handles Redis. Frappe needs hook when child table row deleted.
- **Device Sync enhances Admin UX:** Without sync, admin sees stale child table data. Not strictly required but causes confusion.
- **Session Invalidation is P2:** Removed device can wait for token expiry (15 min default). Immediate invalidation is polish, not critical.

---

## MVP Definition

### v1.3 Must-Have (P1)

| Feature | Component | Why Essential |
|---------|-----------|---------------|
| ProfileService with Redis cache | FastAPI service | Required for display name lookup |
| Batch profile lookup | ProfileService method | Avoid N+1 queries on leaderboard |
| Profile cache on login | Auth endpoint | Ensure cache populated for active users |
| Leaderboard profile integration | Endpoint update | Replace placeholders with real names |
| Admin view devices | Frappe form refresh | Visibility before removal |
| Admin remove device | Frappe hook + API | Core admin action |
| Redis-to-Frappe sync | Form load script | Keep admin view current |

### v1.3 Should-Have (P2)

| Feature | Component | Why Valuable |
|---------|-----------|--------------|
| Session invalidation on removal | Hook enhancement | Immediate access revocation |
| Device count in form header | Frappe form script | Quick visibility |

### v1.x Future (P3)

| Feature | Component | Why Defer |
|---------|-----------|-----------|
| Profile cache warm-up task | Scheduled task | Not needed until scale |
| Profile update cache invalidation | Frappe hook | Profile changes rare |
| User-facing device management | Mobile/web UI | Security review needed |

---

## Feature Prioritization Matrix

| Feature | User Value | Implementation Cost | Priority |
|---------|------------|---------------------|----------|
| Display names in leaderboard | HIGH | LOW | P1 |
| Batch profile lookup | HIGH | LOW | P1 |
| Profile cache (Redis) | HIGH | MEDIUM | P1 |
| Cache population on login | HIGH | LOW | P1 |
| Admin view devices | MEDIUM | LOW | P1 |
| Admin remove device | MEDIUM | LOW | P1 |
| Device sync to Frappe | MEDIUM | MEDIUM | P1 |
| Session invalidation on removal | LOW | LOW | P2 |
| Device count badge | LOW | LOW | P2 |
| User-facing device management | MEDIUM | HIGH | P3 (defer) |

---

## Technical Implementation Notes

### Profile Cache Key Design

```
Key: memora:profile:{user_id}
Type: Redis Hash
TTL: 3600 seconds (1 hour)

Fields:
  display_name: str  (from Memora Player Profile.display_name)
  avatar: str        (from Memora Player Profile.avatar, e.g., "avatar 1")
  cached_at: str     (ISO timestamp for debugging)
```

### Batch Lookup Pattern

```python
async def batch_get(self, player_ids: list[str]) -> dict[str, ProfileData]:
    """Fetch profiles for multiple players, using cache where available."""

    # 1. Build cache keys
    keys = [f"memora:profile:{pid}" for pid in player_ids]

    # 2. MGET all keys (or pipeline HGETALL)
    cached = await self.redis.mget(keys)  # Returns list of values or None

    # 3. Identify cache misses
    misses = [pid for pid, val in zip(player_ids, cached) if val is None]

    # 4. Query Frappe for misses (batch)
    if misses:
        frappe_profiles = await self.frappe_client.get_profiles(misses)
        # Populate cache for each miss
        for profile in frappe_profiles:
            await self._cache_profile(profile)

    # 5. Return combined results
    return {pid: self._parse_profile(val) for pid, val in zip(player_ids, cached) if val}
```

### Device Sync Trigger (Frappe Client Script)

```javascript
// memora_player_profile.js

frappe.ui.form.on('Memora Player Profile', {
    refresh: function(frm) {
        if (!frm.is_new()) {
            // Sync devices from Redis via API
            frappe.call({
                method: 'memora_admin.api.device.get_player_devices',
                args: { user_id: frm.doc.user },
                callback: function(r) {
                    if (r.message) {
                        // Clear and repopulate child table
                        frm.clear_table('authorized_devices');
                        r.message.forEach(device => {
                            let row = frm.add_child('authorized_devices');
                            row.device_id = device.device_id;
                            row.device_name = device.device_name;
                            row.platform = device.platform;
                            row.last_login = device.last_login;
                            row.user_agent = device.user_agent;
                        });
                        frm.refresh_field('authorized_devices');
                    }
                }
            });
        }
    }
});
```

### Device Removal Hook (Frappe)

```python
# memora_admin/events/device_sync.py

import frappe
import requests

def before_authorized_devices_remove(doc, method):
    """Hook called when admin deletes device from child table."""
    # Get the parent profile to find user_id
    parent = frappe.get_doc("Memora Player Profile", doc.parent)
    user_id = parent.user
    device_id = doc.device_id

    # Call FastAPI to remove from Redis
    response = requests.delete(
        f"{frappe.conf.fastapi_url}/api/v1/admin/devices/{user_id}/{device_id}",
        headers={"Authorization": f"Bearer {get_admin_token()}"}
    )

    if response.status_code != 200:
        frappe.throw(f"Failed to remove device from Redis: {response.text}")
```

---

## Competitor Feature Analysis

| Feature | Duolingo | Khan Academy | Netflix | Our Approach |
|---------|----------|--------------|---------|--------------|
| Leaderboard names | Display name + avatar | Username | N/A | Display name + avatar ID |
| Name source | User-settable | Account | Account | Admin-set in DocType |
| Device management | User-facing | Not visible | User-facing | Admin-only (v1.3) |
| Device limit | Unknown | Unknown | Plan-based (1-4) | 3 devices (configurable) |
| Device removal UX | In-app settings | N/A | Account settings | Frappe Desk child table |

---

## Sources

### Codebase Analysis (HIGH confidence)
- `/home/corex/aurevia-bench/apps/memora_admin/fastapi_app/api/v1/endpoints/leaderboard.py` - Existing endpoint with display_name placeholder
- `/home/corex/aurevia-bench/apps/memora_admin/fastapi_app/services/device.py` - DeviceService with get_devices() and remove_device()
- `/home/corex/aurevia-bench/apps/memora_admin/fastapi_app/services/leaderboard.py` - LeaderboardService returning player_ids
- `/home/corex/aurevia-bench/apps/memora_admin/memora_admin/memora_admin/doctype/memora_player_profile/memora_player_profile.json` - DocType with display_name, avatar, authorized_devices
- `/home/corex/aurevia-bench/apps/memora_admin/memora_admin/memora_admin/doctype/memora_player_device/memora_player_device.json` - Child table structure

### Official Documentation (HIGH confidence)
- [Redis Leaderboards](https://redis.io/solutions/leaderboards/) - ZSET patterns for rankings
- [Frappe Desk](https://docs.frappe.io/framework/user/en/desk) - Admin interface patterns
- [AWS Database Caching](https://docs.aws.amazon.com/whitepapers/latest/database-caching-strategies-using-redis/caching-patterns.html) - Cache-aside pattern

### Industry Research (MEDIUM confidence)
- [System Design - Leaderboard](https://systemdesign.one/leaderboard-system-design/) - Architecture patterns
- [Game Developer - Leaderboard Design](https://www.gamedeveloper.com/design/design-and-safety-tips-for-leaderboard) - UX best practices
- [Netflix Device Management](https://help.netflix.com/en/node/128180) - Multi-device limit patterns
- [Heroic Labs - Usernames and Leaderboards](https://forum.heroiclabs.com/t/usernames-and-leaderboards/535/2) - Display name vs username patterns

---
*Feature research for: v1.3 Profile Display Names & Admin Device Management*
*Researched: 2026-02-03*
