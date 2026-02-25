# Data Model: Redis Hardening

**Feature**: 027-redis-hardening | **Date**: 2026-02-25

## Entities

### 1. Redis Configuration (Infrastructure)

**Type**: Configuration file (not a DocType)
**Location**: `/etc/redis/redis-memora.conf`

| Field | Type | Value (Dev) | Value (Prod) | Notes |
|-------|------|-------------|-------------|-------|
| port | int | 13001 | 13001 | Separate from Frappe's 13000 |
| bind | string | 127.0.0.1 | 127.0.0.1 | Localhost only |
| maxmemory | string | 128mb | 512mb–1gb | Scale with user count |
| maxmemory-policy | string | volatile-ttl | volatile-ttl | Evict keys with shortest TTL first |
| appendonly | string | yes | yes | AOF persistence enabled |
| appendfsync | string | everysec | everysec | 1s data loss window |
| dir | string | /var/lib/redis-memora | /var/lib/redis-memora | AOF file storage |
| logfile | string | /var/log/redis/redis-memora.log | /var/log/redis/redis-memora.log | |
| databases | int | 1 | 1 | Only db0 needed |
| tcp-keepalive | int | 300 | 300 | Detect dead connections |
| aof-use-rdb-preamble | string | yes | yes | Faster AOF rewrite |

### 2. Systemd Service (Infrastructure)

**Type**: Systemd unit file
**Location**: `/etc/systemd/system/redis-memora.service`

| Field | Value | Notes |
|-------|-------|-------|
| ExecStart | `/usr/bin/redis-server /etc/redis/redis-memora.conf` | |
| User | redis | Standard Redis user |
| Group | redis | Standard Redis group |
| Restart | always | Auto-restart on crash |
| RestartSec | 3 | 3 second delay between restarts |
| LimitNOFILE | 65535 | Required for high connection count |

### 3. Site Configuration Update

**Type**: JSON config update
**Location**: `sites/x.conanacademy.com/site_config.json`

| Field | Value | Notes |
|-------|-------|-------|
| `redis_memora` | `redis://127.0.0.1:13001` | New key, read by all Frappe-side Memora code |

### 4. Redis Health Report (API Response)

**Type**: Pydantic model (new)
**Location**: `fastapi_app/models/health.py`

```python
class RedisHealthReport(BaseModel):
    status: Literal["healthy", "degraded", "unhealthy"]
    used_memory_mb: float
    max_memory_mb: float
    memory_usage_percent: float
    interaction_buffer_length: int
    dirty_wallets_count: int
    dirty_progress_count: int
    connected_clients: int
    aof_enabled: bool
    uptime_seconds: int
    total_keys: int
```

**Status logic**:
- `healthy`: memory <80%, buffer <10000, dirty sets <1000
- `degraded`: memory 80-95% OR buffer 10000-50000 OR dirty sets >1000
- `unhealthy`: memory >95% OR buffer >50000 OR Redis unreachable

### 5. TTL Policy (No New Entity — Applied to Existing Keys)

| Key Pattern | Current TTL | New TTL | Refresh Trigger |
|-------------|-------------|---------|-----------------|
| `memora:wallet:{player}` | None | 48h (172800s) | Every HINCRBY (award_xp), Lua (streak update) |
| `memora:progress:{user}:{subj}:v{ver}` | None | 48h (172800s) | Every SETBIT (complete_lesson), Lua (session_complete) |
| `memora:access:{player}` | None | 24h (86400s) | On hydration (ensure_hydrated) |
| `memora:plan:{plan}:free_subjects` | None | 12h (43200s) | On plan_sync task, on event hook |
| `memora:dirty:progress` | None | **None** | MUST NOT have TTL — loss = data loss |
| `memora:dirty:wallets` | None | **None** | MUST NOT have TTL — loss = data loss |
| `memora:buffer:interactions` | None | **None** | MUST NOT have TTL — loss = data loss |

**Critical**: Dirty sets and interaction buffer MUST NEVER have TTL. They contain data not yet written to MariaDB. Their loss means permanent data loss. The `volatile-ttl` eviction policy protects them because they have no TTL — only keys WITH TTL are evicted.

## Relationships

```
┌─────────────────────────────────────┐
│  Redis Instance (port 13001)        │
│                                     │
│  ┌─── Protected (no TTL) ────────┐  │
│  │ dirty:progress (SET)          │  │
│  │ dirty:wallets (SET)           │  │
│  │ buffer:interactions (LIST)    │  │
│  │ lb:alltime (ZSET)             │  │
│  └───────────────────────────────┘  │
│                                     │
│  ┌─── TTL-Managed (evictable) ───┐  │
│  │ wallet:{player} (HASH, 48h)   │  │
│  │ progress:{u}:{s}:v{v} (48h)   │  │
│  │ access:{player} (SET, 24h)    │  │
│  │ plan:{p}:free_subjects (12h)  │  │
│  │ hierarchy:{subject} (1h)      │  │
│  │ stats:{u}:{s}:v{v} (1h)      │  │
│  │ lb:daily:* (30d)              │  │
│  │ lb:weekly:* (90d)             │  │
│  └───────────────────────────────┘  │
│                                     │
│  volatile-ttl policy:               │
│  Only evicts keys WITH TTL.         │
│  Protected keys are never evicted.  │
└─────────────────────────────────────┘
         │
         │ ensure_hydrated() on cache miss
         ▼
┌─────────────────────────────────────┐
│  MariaDB (Source of Truth)          │
│  Memora Player Wallet               │
│  Memora Structure Progress           │
│  Memora Player Subscription          │
│  Memora Plan Subject                 │
└─────────────────────────────────────┘
```

## State Transitions

### Key Lifecycle

```
Key Created (via SETBIT/HSET/SADD)
    │
    ├── TTL set (EXPIRE)
    │       │
    │       ├── Write occurs → TTL refreshed (EXPIRE)
    │       │
    │       ├── TTL expires → Key evicted
    │       │       │
    │       │       └── Next read → ensure_hydrated() → Key recreated
    │       │
    │       └── Memory pressure + volatile-ttl → Key evicted early
    │               │
    │               └── Next read → ensure_hydrated() → Key recreated
    │
    └── No TTL (dirty sets, buffer, alltime lb)
            │
            └── Never evicted by volatile-ttl policy
                Only removed by explicit DEL/SREM/LTRIM
```

## Validation Rules

1. **TTL constants** must be defined in `fastapi_app/core/redis_keys.py` alongside key builders
2. **Protected keys** (dirty sets, buffer) must NEVER receive TTL
3. **Lua scripts** that write to TTL-managed keys MUST include `EXPIRE` atomically
4. **`get_memora_redis()`** utility MUST fall back to `frappe.conf.redis_cache` if `redis_memora` not configured
5. **Health endpoint** MUST handle Redis connection failure gracefully (return "unhealthy" status, not 500)
