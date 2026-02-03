# Architecture Patterns: v1.3 Profile Display Names & Admin Device Management

**Domain:** Gamified educational platform extensions
**Researched:** 2026-02-03
**Confidence:** HIGH (based on existing codebase analysis)

## Existing Architecture Overview

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                           CLIENT (React App)                                 │
└─────────────────────────────────────────────────────────────────────────────┘
                │                                    │
                │ /api/v1/* (Game API)              │ /api/method/* (Admin)
                ▼                                    ▼
┌─────────────────────────────┐       ┌─────────────────────────────┐
│      FastAPI Sidecar        │       │        Frappe v15           │
│        (Port 8001)          │       │       (Port 8000)           │
│  ┌───────────────────────┐  │       │  ┌───────────────────────┐  │
│  │ Leaderboard Endpoints │  │       │  │ Player Profile DocType│  │
│  │ Auth Endpoints        │  │       │  │ Device Child Table    │  │
│  │ Session Endpoints     │  │       │  │ Admin API Methods     │  │
│  └───────────────────────┘  │       │  └───────────────────────┘  │
│  ┌───────────────────────┐  │       │  ┌───────────────────────┐  │
│  │ LeaderboardService    │  │       │  │ device_sync.py hooks  │  │
│  │ DeviceService         │  │       │  │ access_sync.py hooks  │  │
│  └───────────────────────┘  │       │  └───────────────────────┘  │
└─────────────────────────────┘       └─────────────────────────────┘
                │                                    │
                │ Redis Operations                   │ Redis + MariaDB
                ▼                                    ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                              Redis                                           │
│  ┌────────────────────┐  ┌────────────────────┐  ┌────────────────────────┐ │
│  │ memora:lb:*        │  │ memora:devices:*   │  │ memora:player_names:*  │ │
│  │ (Leaderboard ZSET) │  │ (Device Hash)      │  │ (Profile Hash) [NEW]   │ │
│  └────────────────────┘  └────────────────────┘  └────────────────────────┘ │
└─────────────────────────────────────────────────────────────────────────────┘
                                     │
                                     ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                            MariaDB (Frappe ORM)                             │
│  ┌────────────────────────────┐  ┌────────────────────────────────────────┐ │
│  │ tabMemora Player Profile   │  │ tabMemora Player Device (child table) │ │
│  │ - user, display_name       │  │ - device_id, device_name, platform    │ │
│  │ - avatar, grade, major     │  │ - last_login, user_agent, push_token  │ │
│  └────────────────────────────┘  └────────────────────────────────────────┘ │
└─────────────────────────────────────────────────────────────────────────────┘
```

## Feature 1: Profile Display Names in Leaderboard

### Current State

The leaderboard endpoints (`/api/v1/leaderboard/{type}`) currently return `player_id` as `display_name`:

```python
# Current: fastapi_app/api/v1/endpoints/leaderboard.py:66
display_name=entry["player_id"],  # Placeholder: profile lookup in future phase
```

The LeaderboardEntry model expects:
```python
# fastapi_app/models/leaderboard.py:18-34
class LeaderboardEntry(BaseModel):
    rank: int
    player_id: str
    display_name: str
    xp: int
    avatar_url: str | None = None
    is_me: bool = False
```

### Integration Architecture

**Option A: Redis Hash Cache (RECOMMENDED)**

Store display names in Redis hash for O(1) bulk lookup:

```
Redis Key: memora:player_names
Structure: HASH { player_id -> display_name }

Flow:
1. Frappe hook on Memora Player Profile (on_update) syncs display_name to Redis
2. LeaderboardService.get_top() fetches player_ids from ZSET
3. ProfileService.get_display_names(player_ids) does HMGET for batch lookup
4. Endpoint assembles entries with real display_names
```

**Why Redis Hash:**
- O(1) per key lookup, O(N) for HMGET with N player_ids
- Leaderboard typically fetches 10-100 entries - HMGET handles this efficiently
- Existing pattern: device data in Redis hash (`memora:devices:{user_id}`)
- Avoids N+1 queries to MariaDB

**Data Flow:**

```
┌──────────────────────┐     on_update hook      ┌─────────────────────┐
│ Memora Player Profile│────────────────────────▶│ profile_sync.py     │
│ (Frappe DocType)     │                         │ (new events file)   │
└──────────────────────┘                         └─────────────────────┘
                                                           │
                                                           │ HSET
                                                           ▼
                                                 ┌─────────────────────┐
                                                 │ Redis               │
                                                 │ memora:player_names │
                                                 └─────────────────────┘
                                                           │
                                                           │ HMGET
                                                           ▼
┌──────────────────────┐     get_display_names   ┌─────────────────────┐
│ LeaderboardService   │◀────────────────────────│ ProfileService      │
│ (existing)           │                         │ (new service)       │
└──────────────────────┘                         └─────────────────────┘
```

### Component Boundaries

| Component | Responsibility | Location |
|-----------|---------------|----------|
| ProfileService | Cache management for player display names | `fastapi_app/services/profile.py` (NEW) |
| profile_sync.py | Frappe hook to sync display_name on update | `memora_admin/events/profile_sync.py` (NEW) |
| LeaderboardService | Leaderboard ZSET operations (unchanged) | `fastapi_app/services/leaderboard.py` |
| leaderboard.py endpoint | Orchestrates profile lookup for entries | `fastapi_app/api/v1/endpoints/leaderboard.py` (MODIFY) |

### New Redis Keys

| Key Pattern | Type | TTL | Purpose |
|-------------|------|-----|---------|
| `memora:player_names` | HASH | None | Maps player_id to display_name |

No TTL needed - data synced on profile update. Redis memory impact: ~100 bytes per player (player_id + display_name), ~10MB for 100K players.

## Feature 2: Admin Device Management

### Current State

Devices are stored in Redis with this structure:
```
Key: memora:devices:{user_id}
Type: HASH
Fields:
  device:{device_id}:name
  device:{device_id}:ua
  device:{device_id}:platform
  device:{device_id}:last_login
  device:{device_id}:fingerprint
  device:{device_id}:push_token
```

The Memora Player Device DocType exists as a child table:
```json
// memora_player_device.json
{
  "istable": 1,
  "fields": ["device_id", "device_name", "last_login", "user_agent", "platform", "push_token"]
}
```

Current device_sync.py hook handles **removal** when admin edits authorized_devices:
```python
# memora_admin/events/device_sync.py:9
def on_player_profile_update(doc, method):
    # Compares current vs previous authorized_devices
    # Removes deleted devices from Redis
    # Invalidates session
```

**GAP IDENTIFIED:** Devices in Redis are NOT synced TO MariaDB (authorized_devices child table). Admin sees empty device list in Frappe Desk.

### Integration Architecture

**Two-Way Sync Pattern:**

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                        DEVICE SYNC FLOW                                     │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                             │
│  LOGIN FLOW (FastAPI -> Redis):                                             │
│  ┌──────────┐    register_device    ┌──────────┐                           │
│  │ /auth/   │ ─────────────────────▶│ Redis    │                           │
│  │ login    │                       │ devices  │                           │
│  └──────────┘                       └──────────┘                           │
│                                           │                                 │
│                                           │ scheduled task                  │
│                                           ▼                                 │
│                                    ┌──────────┐                             │
│                                    │ MariaDB  │                             │
│                                    │ child tbl│                             │
│                                    └──────────┘                             │
│                                                                             │
│  ADMIN REMOVAL FLOW (Frappe -> Redis):                                     │
│  ┌──────────┐    on_update hook     ┌──────────┐                           │
│  │ Frappe   │ ─────────────────────▶│ Redis    │                           │
│  │ Desk UI  │                       │ devices  │                           │
│  └──────────┘                       │ + session│                           │
│                                     └──────────┘                           │
└─────────────────────────────────────────────────────────────────────────────┘
```

### Component Boundaries

| Component | Responsibility | Location |
|-----------|---------------|----------|
| DeviceService | Redis device operations (exists) | `fastapi_app/services/device.py` |
| device_sync.py | Admin removal hook (exists) | `memora_admin/events/device_sync.py` |
| sync_devices task | Scheduled sync Redis -> MariaDB | `memora_admin/tasks/sync.py` (MODIFY) |
| Player Profile JS | Enhanced device list display | `memora_player_profile.js` (MODIFY) |

### Sync Strategy

**Option A: Scheduled Sync (RECOMMENDED)**

Add device sync to existing 1-minute sync cycle:

```python
# memora_admin/tasks/sync.py (add new function)
def sync_dirty_devices():
    """Sync devices from Redis to MariaDB authorized_devices child table."""
    # Get players with dirty device flag
    # For each: fetch Redis devices, update child table
```

**Why scheduled sync (not real-time):**
- Matches existing pattern (progress, wallets use 1-minute sync)
- Device registration is infrequent (once per device)
- Reduces Frappe write load
- Admin can see devices within 1 minute of login

**Option B: Real-time sync on login**

Would require FrappeClient call on every login - adds latency (~50-100ms) to login response.

**Recommendation:** Option A - scheduled sync maintains sub-20ms login performance.

### Data Flow for Admin Device Management

```
VIEWING DEVICES:
1. Admin opens Player Profile in Frappe Desk
2. authorized_devices child table shows synced devices from MariaDB
3. Each row: device_id, device_name, platform, last_login

REMOVING DEVICE:
1. Admin deletes row from authorized_devices table
2. Clicks Save
3. on_player_profile_update hook fires (existing)
4. Hook compares before/after device sets
5. Removed devices deleted from Redis (existing)
6. Session invalidated (existing)
7. Player must re-login on that device
```

## Build Order Recommendation

Based on dependency analysis:

### Phase 14: Profile Display Names

**Plan 14-01: ProfileService + Frappe Hook**
1. Create `fastapi_app/services/profile.py` with ProfileService
2. Create `memora_admin/events/profile_sync.py` with on_profile_updated hook
3. Register hook in hooks.py
4. Add ProfileServiceDep to deps.py

**Plan 14-02: Leaderboard Integration**
1. Modify leaderboard endpoint to use ProfileService
2. Batch lookup display names for entries
3. Test with mock profiles

### Phase 15: Admin Device Management

**Plan 15-01: Device Sync Task**
1. Add `sync_dirty_devices()` to sync.py
2. Add dirty tracking for devices
3. Register in scheduler_events

**Plan 15-02: Enhanced Device Display (Optional)**
1. Add "Refresh Devices" button to Player Profile form
2. Custom fetch from Redis for real-time view (optional)

**Dependency rationale:**
- Profile display names are independent of device management
- Profile service may be useful for device display later (show who owns device)
- Device sync completes the Redis <-> MariaDB bidirectional pattern

## Anti-Patterns to Avoid

### Anti-Pattern 1: N+1 Profile Queries

**What:** Fetching display_name individually for each leaderboard entry
**Why bad:** 10 entries = 10 Redis calls = ~5ms overhead
**Instead:** Use HMGET for batch lookup in ProfileService

### Anti-Pattern 2: Real-time Device Sync on Login

**What:** Calling FrappeClient.insert() on every device registration
**Why bad:** Adds 50-100ms to login flow, may fail if Frappe down
**Instead:** Use dirty flag + scheduled sync (existing pattern)

### Anti-Pattern 3: Device Data Duplication

**What:** Storing device info redundantly in both Redis and MariaDB as source of truth
**Why bad:** Sync conflicts, data inconsistency
**Instead:** Redis is authoritative for hot data, MariaDB is display-only copy for admin viewing

### Anti-Pattern 4: Profile Cache Without Invalidation

**What:** Caching display_name without hook to update on profile change
**Why bad:** Admin changes display_name, leaderboard shows stale data
**Instead:** Frappe hook syncs Redis on every profile update

## Scalability Considerations

| Concern | At 1K players | At 100K players | At 1M players |
|---------|---------------|-----------------|---------------|
| Profile cache size | ~100KB | ~10MB | ~100MB |
| HMGET for 100 entries | <1ms | <1ms | <1ms |
| Device sync batch | 1 minute cycle | 1 minute cycle | Shard by player range |
| Redis hash operations | O(1) | O(1) | O(1) |

## Integration Points Summary

### New Components

| Component | Type | Purpose |
|-----------|------|---------|
| ProfileService | FastAPI Service | Display name cache management |
| profile_sync.py | Frappe Hook | Sync display_name to Redis |
| sync_dirty_devices | Scheduled Task | Sync devices Redis -> MariaDB |

### Modified Components

| Component | Change |
|-----------|--------|
| leaderboard.py endpoint | Use ProfileService for display names |
| sync.py tasks | Add device sync function |
| hooks.py | Register profile_sync hook |
| scheduler_events | Add device sync schedule |
| deps.py | Add ProfileServiceDep |

### New Redis Keys

| Key | Type | Purpose |
|-----|------|---------|
| `memora:player_names` | HASH | Player display name cache |
| `memora:dirty:devices` | SET | Players with pending device sync |

## Sources

- Existing codebase analysis: `fastapi_app/services/leaderboard.py`, `device.py`
- Existing hooks: `memora_admin/events/device_sync.py`
- DocType schema: `memora_player_profile.json`, `memora_player_device.json`
- Existing patterns: `sync.py` (dirty set tracking)
- PRD references: `docs/PRD-2.md` (display_name lookup examples)

---

*Architecture research: 2026-02-03*
