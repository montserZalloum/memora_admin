# Stack Research: v1.3 Leaderboard Profiles & Admin Device Management

**Milestone:** v1.3 Leaderboard Profiles & Admin Device Management
**Researched:** 2026-02-03
**Overall Confidence:** HIGH

## Executive Summary

The v1.3 milestone requires **ZERO new dependencies**. Both features (profile caching for leaderboard display names, admin device management UI) are implementable using the existing validated stack:

- **Profile caching:** Redis hash with pipeline batch fetch using existing redis-py 5.0+
- **Profile enrichment:** FrappeClient.call() for batch profile lookup
- **Admin device UI:** Frappe form scripts (JavaScript) with existing Dialog API
- **Device removal sync:** Existing doc_events hooks pattern (device_sync.py)

The architecture follows established patterns from v1.0-v1.2, requiring only new service classes and Frappe form script enhancements.

---

## Current Stack (Validated in v1.0-v1.2)

| Technology | Current Version | Status |
|------------|----------------|--------|
| FastAPI | >=0.115.0 | Retained |
| redis-py | >=5.0.0 | Retained |
| uvicorn | >=0.27.0 | Retained |
| Pydantic | 2.0+ (via pydantic-settings) | Retained |
| PyJWT | via fastapi | Retained |
| httpx | >=0.27.0 | Retained |
| structlog | >=24.0.0 | Retained |
| Frappe | v15 | Retained |

---

## Recommended Stack Changes

### None Required

All v1.3 features are implementable with existing dependencies.

| Feature | Implementation Approach | Existing Capability |
|---------|------------------------|---------------------|
| Profile caching | Redis hash per player | redis-py HSET/HGETALL |
| Batch profile fetch | Redis pipeline | redis-py async pipeline |
| Profile fallback | FrappeClient.call() | httpx via FrappeClient |
| Admin device UI | Frappe form scripts | frappe.ui.Dialog, frm.add_custom_button |
| Device removal sync | doc_events hooks | device_sync.py pattern |

---

## Feature Implementation with Existing Stack

### Profile Caching (Redis Hash + Pipeline)

**Required capabilities:**
- Cache player profile data (display_name, avatar)
- Batch fetch 10-100 profiles for leaderboard enrichment
- Invalidate on profile update

**Redis Key Pattern:**
```
memora:profile:{user_id}
```

**Data Structure:**
```
HSET memora:profile:user@example.com
  display_name "Ahmed"
  avatar "avatar_1"
```

**Why hash over string:**
- Individual field access (HGET for just display_name)
- Atomic field updates (HSET single field)
- Memory efficient for small field sets (2-3 fields)
- Consistent with existing patterns (devices, wallets)

**Batch Fetch with Pipeline:**

redis-py 5.0+ supports async pipelines for batching multiple commands in a single network round-trip:

```python
async def get_profiles_batch(self, player_ids: list[str]) -> dict[str, dict]:
    """Batch fetch profiles using pipeline for O(1) network round-trip."""
    pipe = self.redis.pipeline()
    for player_id in player_ids:
        pipe.hgetall(f"{self.prefix}profile:{player_id}")
    results = await pipe.execute()

    profiles = {}
    for player_id, data in zip(player_ids, results):
        if data:
            profiles[player_id] = {
                "display_name": data.get(b"display_name", b"").decode(),
                "avatar": data.get(b"avatar", b"").decode() or None,
            }
    return profiles
```

**Performance:**
- Single network round-trip regardless of batch size
- O(N) Redis operations batched into one
- Typical leaderboard fetch (10-100 profiles): <5ms additional latency

**Sources:**
- [Redis Pipelining](https://redis.io/docs/latest/develop/using-commands/pipelining/)
- [redis-py Async Examples](https://redis.readthedocs.io/en/stable/examples/asyncio_examples.html)
- [Pipelines and Transactions](https://redis.io/docs/latest/develop/clients/redis-py/transpipe/)

### Profile Cache Population

**Two strategies available:**

**Option A: Lazy Load (Recommended for v1.3)**
- On leaderboard request, batch fetch profiles from Redis
- Cache miss: fetch from Frappe via FrappeClient.call(), then cache
- Simple, no background task needed
- Acceptable first-request latency (<50ms for batch Frappe call)

```python
async def get_or_fetch_profiles(self, player_ids: list[str]) -> dict[str, dict]:
    # Try Redis first
    profiles = await self.get_profiles_batch(player_ids)

    # Find missing profiles
    missing = [pid for pid in player_ids if pid not in profiles]

    if missing:
        # Fetch from Frappe
        frappe_profiles = await self.frappe.call(
            "memora_admin.api.profiles.get_player_profiles",
            {"player_ids": missing}
        )

        # Cache and merge
        for profile in frappe_profiles:
            user_id = profile["user"]
            await self.cache_profile(user_id, profile["display_name"], profile["avatar"])
            profiles[user_id] = {
                "display_name": profile["display_name"],
                "avatar": profile["avatar"],
            }

    return profiles
```

**Option B: Proactive Sync (Future optimization)**
- Frappe doc_events hook on Memora Player Profile update
- Sync display_name/avatar to Redis immediately
- Better for high-traffic scenarios
- More complex, adds coupling

**Recommendation:** Option A for v1.3. Profile changes are infrequent. Upgrade to Option B only if cache misses cause noticeable latency.

### Cache TTL and Invalidation

**TTL:**
```python
PROFILE_CACHE_TTL = 3600  # 1 hour
```

**Rationale:**
- Matches HierarchyService pattern (established in v1.0)
- Display names change rarely (player chooses once)
- 1-hour staleness acceptable for leaderboards

**Invalidation via Pub/Sub:**

Reuse existing `memora:invalidation` channel (established in v1.0):

```python
# Message format
{"type": "profile", "player_id": "user@example.com"}
```

**Hook (in Frappe events):**
```python
def on_player_profile_update(doc, method):
    """Invalidate profile cache when display_name or avatar changes."""
    if doc.has_value_changed("display_name") or doc.has_value_changed("avatar"):
        cache = frappe.cache()
        cache.publish(
            "memora:invalidation",
            frappe.as_json({"type": "profile", "player_id": doc.user})
        )
```

**Pub/Sub Handler (in FastAPI pubsub.py):**
```python
elif msg_type == "profile":
    player_id = data.get("player_id")
    if player_id and hasattr(app_state, "profile_service"):
        await app_state.profile_service.invalidate(player_id)
```

---

### Frappe Admin Device Management UI

**Required capabilities:**
- View player's registered devices in profile form
- Remove device with confirmation dialog
- Sync removal to Redis (invalidate session)

**Existing Infrastructure:**

| Component | Status | Notes |
|-----------|--------|-------|
| `Memora Player Device` DocType | EXISTS | Child table with device_id, device_name, platform, etc. |
| `authorized_devices` field | EXISTS | Table field on Memora Player Profile |
| `device_sync.py` hook | EXISTS | `on_player_profile_update` syncs device removals to Redis |
| `memora_player_profile.js` | EXISTS | Form scripts with custom buttons |

**What Needs Adding:**

**1. Enhanced Device Grid Display**

The current authorized_devices child table shows devices but lacks admin-friendly actions. Add confirmation dialog for device removal:

```javascript
// In memora_player_profile.js
frappe.ui.form.on("Memora Player Profile", {
    refresh: function(frm) {
        // Existing Grant Access button...

        // Add device management UI
        if (!frm.is_new() && frm.doc.authorized_devices?.length > 0) {
            setup_device_grid_actions(frm);
        }
    }
});

function setup_device_grid_actions(frm) {
    // Add "Remove Device" button to actions
    frm.add_custom_button(__("Remove Device"), function() {
        show_device_removal_dialog(frm);
    }, __("Devices"));
}

function show_device_removal_dialog(frm) {
    const devices = frm.doc.authorized_devices || [];
    if (devices.length === 0) {
        frappe.msgprint(__("No devices to remove"));
        return;
    }

    const options = devices.map(d => ({
        label: `${d.device_name} (${d.platform})`,
        value: d.device_id
    }));

    let dialog = new frappe.ui.Dialog({
        title: __("Remove Device"),
        fields: [
            {
                fieldname: "device_id",
                fieldtype: "Select",
                label: __("Select Device"),
                options: options,
                reqd: 1
            },
            {
                fieldname: "warning",
                fieldtype: "HTML",
                options: `<p class="text-danger">${__("Warning: This will log the player out of this device immediately.")}</p>`
            }
        ],
        primary_action_label: __("Remove"),
        primary_action: function(values) {
            remove_device(frm, values.device_id, dialog);
        }
    });

    dialog.show();
}

function remove_device(frm, device_id, dialog) {
    // Remove from child table
    const idx = frm.doc.authorized_devices.findIndex(d => d.device_id === device_id);
    if (idx !== -1) {
        frm.doc.authorized_devices.splice(idx, 1);
        frm.dirty();
        frm.save().then(() => {
            dialog.hide();
            frappe.show_alert({
                message: __("Device removed successfully"),
                indicator: "green"
            });
        });
    }
}
```

**Frappe UI Components Used:**
- `frappe.ui.Dialog` - Modal dialog with form fields
- `frm.add_custom_button` - Action button in form header
- `frm.dirty()` / `frm.save()` - Standard document save flow

**Sources:**
- [Adding Custom Button To Form](https://docs.frappe.io/framework/user/en/guides/app-development/adding-custom-button-to-form)
- [Form Scripts API](https://docs.frappe.io/framework/v15/user/en/api/form)
- [Dialog API](https://docs.frappe.io/framework/v15/user/en/api/dialog)

**2. Device Sync from Redis to Frappe (Optional Enhancement)**

Currently, device registration flows one-way (FastAPI -> Redis). For admin visibility of devices registered since last sync:

**Option A: Sync on profile form load (simple)**
```python
# In memora_player_profile.py
def onload(self):
    """Sync device list from Redis on form load."""
    if self.user:
        sync_devices_from_redis(self)
```

**Option B: Sync on login (recommended)**

Extend existing login flow to sync device list to Frappe child table after successful device registration. Admin always sees current state.

**Recommendation:** Option B. Keeps data consistent without form-load overhead.

---

### What NOT to Add

| Technology | Why Considered | Why NOT Adding |
|------------|----------------|----------------|
| cachetools | In-process caching | Redis is the cache layer; in-process caching adds stale data risk and complexity |
| aioredis | Async Redis | ABANDONED; redis-py 5.0+ has native async support already in use |
| msgpack | Binary serialization | JSON sufficient for profile data (3 small fields); msgpack adds dependency for minimal gain |
| websocket libs | Real-time updates | Polling acceptable for leaderboards; WebSocket adds significant complexity |
| celery | Background tasks | Frappe scheduler handles background sync; no distributed worker needs |

---

## Integration Points

### 1. ProfileService (New)

Following established service patterns from HierarchyService:

```python
# services/profile.py
class ProfileService:
    """Cache player profiles for leaderboard enrichment."""

    CACHE_TTL = 3600  # 1 hour

    def __init__(
        self,
        redis_client: redis.Redis,
        frappe_client: FrappeClient,
        key_prefix: str = "memora:",
    ):
        self.redis = redis_client
        self.frappe = frappe_client
        self.prefix = key_prefix

    def _cache_key(self, player_id: str) -> str:
        return f"{self.prefix}profile:{player_id}"

    async def get_profiles_batch(self, player_ids: list[str]) -> dict[str, ProfileCache]:
        """Batch fetch profiles using pipeline."""
        ...

    async def cache_profile(self, player_id: str, display_name: str, avatar: str | None) -> None:
        """Cache profile with TTL."""
        ...

    async def invalidate(self, player_id: str) -> None:
        """Delete cached profile (called via pub/sub)."""
        ...
```

### 2. Dependency Injection (deps.py)

Add ProfileService following existing patterns:

```python
async def get_profile_service(request: Request) -> ProfileService:
    """Get ProfileService with Redis and FrappeClient."""
    redis_client = redis.Redis(connection_pool=request.app.state.redis_pool)
    frappe_client = await get_frappe_client()
    return ProfileService(redis_client, frappe_client)

ProfileServiceDep = Annotated[ProfileService, Depends(get_profile_service)]
```

### 3. Leaderboard Endpoint Enrichment

Modify endpoint (not service) to enrich entries with profiles:

```python
# In leaderboard.py endpoint
@router.get("/{lb_type}", response_model=LeaderboardResponse)
async def get_leaderboard(
    lb_type: LeaderboardTypeParam,
    user: CurrentUser,
    leaderboard_service: LeaderboardServiceDep,
    profile_service: ProfileServiceDep,  # NEW
    limit: int = Query(10, ge=1, le=100),
    subject_id: str | None = Query(None),
) -> LeaderboardResponse:
    raw_entries = await leaderboard_service.get_top(lb_type, limit, subject_id)

    # Batch fetch profiles
    player_ids = [e["player_id"] for e in raw_entries]
    profiles = await profile_service.get_or_fetch_profiles(player_ids)

    entries = [
        LeaderboardEntry(
            rank=entry["rank"],
            player_id=entry["player_id"],
            display_name=profiles.get(entry["player_id"], {}).get(
                "display_name", entry["player_id"]
            ),
            xp=entry["xp"],
            avatar_url=profiles.get(entry["player_id"], {}).get("avatar"),
            is_me=entry["player_id"] == user.sub,
        )
        for entry in raw_entries
    ]
    ...
```

### 4. Frappe API Endpoint

New whitelisted method for batch profile fetch:

```python
# memora_admin/api/profiles.py
import frappe

@frappe.whitelist()
def get_player_profiles(player_ids: list) -> list:
    """Batch fetch player profiles for leaderboard display.

    Args:
        player_ids: List of user IDs (Memora Player Profile name)

    Returns:
        List of dicts with user, display_name, avatar
    """
    if not player_ids:
        return []

    profiles = frappe.get_all(
        "Memora Player Profile",
        filters={"user": ["in", player_ids]},
        fields=["user", "display_name", "avatar"]
    )
    return profiles
```

### 5. Pub/Sub Registration

Register ProfileService for invalidation messages (in pubsub.py):

```python
# In start_pubsub_listener
async def handle_invalidation(message: dict, app_state):
    msg_type = message.get("type")

    if msg_type == "hierarchy":
        # Existing...
    elif msg_type == "plan":
        # Existing...
    elif msg_type == "profile":
        player_id = message.get("player_id")
        if player_id and hasattr(app_state, "profile_service"):
            await app_state.profile_service.invalidate(player_id)
```

---

## Pydantic Models

### ProfileCache Model

```python
# models/profile.py
from pydantic import BaseModel

class ProfileCache(BaseModel):
    """Cached profile data for leaderboard display."""
    display_name: str
    avatar: str | None = None
```

No new models needed beyond this simple cache representation.

---

## Performance Validation

### Expected Latencies

| Operation | Expected | Note |
|-----------|----------|------|
| Profile cache hit (single) | <1ms | HGETALL single key |
| Profile cache hit (batch 10) | <2ms | Pipeline with 10 HGETALL |
| Profile cache hit (batch 100) | <5ms | Pipeline with 100 HGETALL |
| Profile cache miss (Frappe fetch) | <50ms | HTTP call to Frappe API |
| Leaderboard with enrichment | <25ms | get_top (<20ms) + batch profiles (<5ms) |

**Assumptions:**
- Redis on same network (<1ms RTT)
- Frappe on same host (localhost:8000)
- Typical leaderboard: 10-50 entries

### Performance Target Validation

Current v1.2.1 targets maintained:
- Access check: <2ms (unchanged)
- Progress fetch: <20ms (unchanged)
- **Leaderboard fetch: <25ms** (was: <20ms for raw entries; +5ms for profile enrichment)

The 5ms overhead for profile enrichment is acceptable given the improved user experience.

---

## Migration Path

### Step 1: Add ProfileService

1. Create `fastapi_app/services/profile.py`
2. Create `fastapi_app/models/profile.py`
3. Add ProfileServiceDep to `fastapi_app/api/deps.py`
4. Register in app state (main.py lifespan)

### Step 2: Add Frappe API

1. Create `memora_admin/api/profiles.py`
2. Add to `memora_admin/__init__.py` if needed

### Step 3: Update Leaderboard Endpoints

1. Add ProfileServiceDep to endpoint signatures
2. Implement batch profile fetch
3. Update LeaderboardEntry construction

### Step 4: Add Cache Invalidation

1. Add profile event to pubsub handler
2. Add hook in Frappe events for profile updates

### Step 5: Enhance Frappe Device UI

1. Update `memora_player_profile.js` with device actions
2. Test device removal flow

**No new dependencies to install.**

---

## Confidence Assessment

| Decision | Confidence | Rationale |
|----------|------------|-----------|
| No new dependencies | HIGH | All capabilities exist in current stack |
| Redis hash for profiles | HIGH | Matches wallet/device patterns |
| Pipeline for batch fetch | HIGH | Official redis-py docs confirm pattern |
| 1-hour cache TTL | MEDIUM | Matches HierarchyService; may need tuning |
| Frappe form scripts for UI | HIGH | Existing codebase demonstrates pattern |
| Lazy load strategy | MEDIUM | Simpler; may need proactive sync if latency unacceptable |

---

## Summary

| Requirement | Solution | Stack Change |
|-------------|----------|--------------|
| Profile caching | Redis hash + pipeline batch fetch | None |
| Profile enrichment | FrappeClient.call() + lazy load | None |
| Cache invalidation | Existing pub/sub pattern | None |
| Admin device UI | Frappe form scripts (JS) | None |
| Device removal sync | Existing doc_events hook | None |

**Total new dependencies: ZERO**

The v1.3 milestone continues the pattern of implementing features using the validated stack established in v1.0-v1.2.

---

## Sources

### Official Documentation
- [Redis Pipelining](https://redis.io/docs/latest/develop/using-commands/pipelining/)
- [redis-py Async Examples](https://redis.readthedocs.io/en/stable/examples/asyncio_examples.html)
- [Pipelines and Transactions](https://redis.io/docs/latest/develop/clients/redis-py/transpipe/)
- [Frappe Form Scripts API](https://docs.frappe.io/framework/v15/user/en/api/form)
- [Frappe Dialog API](https://docs.frappe.io/framework/v15/user/en/api/dialog)
- [Adding Custom Button To Form](https://docs.frappe.io/framework/user/en/guides/app-development/adding-custom-button-to-form)
- [HGETALL Command](https://redis.io/docs/latest/commands/hgetall/)
- [MGET Command](https://redis.io/docs/latest/commands/mget/)

### Existing Codebase Patterns
- `fastapi_app/services/hierarchy.py` - Cache service pattern
- `fastapi_app/services/device.py` - Redis hash operations
- `fastapi_app/services/frappe_client.py` - Frappe API calls
- `fastapi_app/core/pubsub.py` - Invalidation message handling
- `memora_admin/events/device_sync.py` - Device removal sync
- `memora_player_profile.js` - Form script patterns

### Performance References
- [Python Redis Bulk Get](https://www.dragonflydb.io/code-examples/python-redis-bulk-get)
- [Redis Pipeline Best Practices](https://last9.io/blog/how-to-make-the-most-of-redis-pipeline/)
