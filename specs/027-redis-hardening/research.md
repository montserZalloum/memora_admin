# Research: Redis Hardening

**Feature**: 027-redis-hardening | **Date**: 2026-02-25

## R1: Dedicated Redis Instance Isolation

**Decision**: Run Memora Redis on port 13001, Frappe cache remains on port 13000.

**Rationale**: Currently both Frappe cache and Memora game data share `redis://127.0.0.1:13000`. When `bench clear-cache` runs, it calls `FLUSHDB` on `redis_cache`, wiping ALL Memora keys (wallets, progress, dirty sets, interaction buffer). This has caused repeated data loss incidents.

**Alternatives considered**:
- **Redis DB index separation** (e.g., db0 vs db1): Rejected — Frappe's FLUSHDB targets the entire database, and Frappe doesn't support per-DB configuration natively. Also, `SELECT` on every command adds latency.
- **Key prefix isolation only**: Rejected — Frappe's `bench clear-cache` uses `FLUSHDB`, not selective key deletion. Prefixes wouldn't help.
- **Separate Redis on different server**: Overkill for dev; adds network latency. Port separation on same host is sufficient.

**Implementation approach**:
1. Create `/etc/redis/redis-memora.conf` with port 13001, AOF, memory limits
2. Create systemd service `redis-memora.service`
3. Update `.env` REDIS_URL to `redis://127.0.0.1:13001`
4. Add `redis_memora` key to `site_config.json`
5. Update all Frappe-side Redis connections to read from `redis_memora` config key

**Connection points to update** (complete inventory):

### FastAPI Side (Async)
| File | Current Source | Change |
|------|---------------|--------|
| `fastapi_app/core/config.py` | `.env` REDIS_URL | Update `.env` value to 13001 |
| `fastapi_app/core/redis.py` | `get_settings().redis_url` | No code change (reads from config) |
| `fastapi_app/tests/conftest.py` | Hardcoded `redis://127.0.0.1:13000` | Update to 13001 |

### Frappe Side (Sync) — Background Tasks
| File | Current Source | Change |
|------|---------------|--------|
| `memora_admin/tasks/sync.py:get_redis()` | `frappe.conf.redis_cache` | Change to `frappe.conf.get("redis_memora", frappe.conf.redis_cache)` |
| `memora_admin/tasks/leaderboard_reset.py:get_redis()` | `frappe.conf.redis_cache` | Same pattern |
| `memora_admin/tasks/session_cleanup.py` | `frappe.conf.redis_cache` | Same pattern |
| `memora_admin/tasks/streak_reset.py` | `frappe.conf.redis_cache` | Same pattern |
| `memora_admin/tasks/profile_cache.py` | `get_fastapi_redis()` | Already reads REDIS_URL from .env |
| `memora_admin/tasks/plan_sync.py` | `get_fastapi_redis()` | Already reads REDIS_URL from .env |

### Frappe Side (Sync) — Event Handlers
| File | Current Source | Change |
|------|---------------|--------|
| `memora_admin/events/access_sync.py:get_fastapi_redis()` | `.env` REDIS_URL | Already reads from .env (auto-picks up 13001) |
| `memora_admin/events/build_trigger.py` | Imports `get_fastapi_redis()` | Already reads from .env |
| `memora_admin/events/catalog_sync.py` | Imports `get_fastapi_redis()` | Already reads from .env |
| `memora_admin/events/plan_change_sync.py` | Imports `get_fastapi_redis()` | Already reads from .env |
| `memora_admin/events/profile_sync.py` | Imports `get_fastapi_redis()` | Already reads from .env |
| `memora_admin/events/level_sync.py` | Imports `get_fastapi_redis()` | Already reads from .env |
| `memora_admin/events/device_sync.py` | Imports `get_fastapi_redis()` | Already reads from .env |

### Frappe Side (Sync) — API Endpoints
| File | Current Source | Change |
|------|---------------|--------|
| `memora_admin/api/profile.py` | `frappe.conf.redis_cache` | Change to `get_memora_redis()` utility |
| `memora_admin/api/reviews.py` | `frappe.conf.redis_cache` | Change to `get_memora_redis()` utility |
| `memora_admin/api/utils.py` | `frappe.conf.redis_cache` (mastery_key) | Change to `get_memora_redis()` utility |
| `memora_admin/api/devices.py` | `get_fastapi_redis()` | Already reads from .env |

### Frappe Side (Sync) — Tests
| File | Current Source | Change |
|------|---------------|--------|
| `memora_admin/tests/sync_test_base.py` | `frappe.conf.redis_cache` | Change to `frappe.conf.get("redis_memora", frappe.conf.redis_cache)` |

**Key design decision**: Create a shared `get_memora_redis()` utility function to centralize the Frappe-side connection logic. This replaces the duplicated `get_redis()` functions across task files and the `frappe.conf.redis_cache` references in API files.

---

## R2: AOF Persistence Configuration

**Decision**: Enable `appendonly yes` with `appendfsync everysec`.

**Rationale**: Without AOF, a Redis crash loses ALL in-memory data. Dirty sets and interaction buffers contain data not yet synced to MariaDB (up to 1 minute of writes). AOF with `everysec` limits data loss to ~1 second.

**Alternatives considered**:
- **`appendfsync always`**: Rejected — writes to disk on every command, severe performance impact (100x slower). Violates sub-20ms performance target.
- **`appendfsync no`**: Rejected — OS decides when to flush (typically every 30s). Unacceptable data loss window.
- **RDB snapshots only**: Rejected — snapshots are periodic (minutes apart). Not suitable for dirty-set data that must survive crashes.
- **RDB + AOF hybrid**: Redis supports both simultaneously. AOF alone is sufficient since RDB provides no additional safety when AOF is enabled. Redis 7+ auto-compacts AOF via `aof-use-rdb-preamble yes`.

**Configuration values**:
```
appendonly yes
appendfsync everysec
auto-aof-rewrite-percentage 100
auto-aof-rewrite-min-size 64mb
aof-use-rdb-preamble yes
```

**Performance impact**: `appendfsync everysec` adds ~0 latency to individual commands (writes are batched by background thread). The fsync occurs in a background thread every second. No impact on sub-20ms target.

---

## R3: TTL Strategy for Synced Keys

**Decision**: Add 48-hour TTL to wallet and progress keys, 24-hour TTL to access keys, 12-hour TTL to plan free subject keys. All refresh on write.

**Rationale**: Without TTL, these keys persist forever. With 100k+ students over multiple seasons, memory grows linearly with *total* users rather than *active* users. Since all keys self-heal via `ensure_hydrated()`, TTL-based eviction is safe.

**Key analysis**:

| Key | Write frequency | Sync frequency | Safe TTL | Refresh trigger |
|-----|----------------|---------------|----------|-----------------|
| `wallet:{player}` | Every XP award (high) | 1 min dirty sync | 48h | HINCRBY in award_xp, Lua in streak |
| `progress:{user}:{subj}:v{ver}` | Every lesson complete | 1 min dirty sync | 48h | SETBIT in complete_lesson, Lua in session_complete |
| `access:{player}` | On subscription change (rare) | Event-driven | 24h | SADD in hydration |
| `plan:{plan}:free_subjects` | On plan subject change (rare) | 6h periodic | 12h | SADD in plan_sync |

**Critical concern — Dirty set race condition**: If a wallet/progress key expires while its player ID is in the dirty set, the sync task will try to read an empty key. This is safe because:
1. `sync_dirty_wallets()` calls `HGETALL` — returns empty dict for expired key
2. Empty wallet data means no update needed — player ID removed from dirty set
3. Data is preserved in MariaDB from the previous sync cycle
4. Next API call triggers `ensure_hydrated()` which rebuilds from MariaDB

**TTL refresh in Lua scripts**: The `SESSION_COMPLETE_SCRIPT` (game_session.py) writes to progress bitmap (SETBIT) but does NOT set TTL on the progress key. The TTL must be added atomically in the same Lua script to avoid a key existing without TTL.

**Implementation**: For non-Lua paths (service methods), add `EXPIRE` in a pipeline after each write. For Lua scripts, add `redis.call('EXPIRE', KEYS[2], 172800)` after SETBIT.

---

## R4: Leaderboard Cleanup Strategy

**Decision**: Scheduled task at 03:00 daily scans and deletes old daily (>30d) and weekly (>90d) leaderboard keys.

**Rationale**: Leaderboard keys already have TTL set by `update_leaderboards()` in leaderboard.py (30d for daily, 90d for weekly). However, alltime keys have no TTL (correct — never deleted). The cleanup task is a safety net for:
1. Keys that somehow lost their TTL
2. Plan-scoped variants that may accumulate
3. Archive keys past their 90-day retention

**Current TTL behavior** (already implemented):
- Daily: 30-day TTL, refreshed on every XP award
- Weekly: 90-day TTL, refreshed on every XP award
- Plan daily: 48-hour TTL
- Plan weekly: 8-day TTL
- Alltime: No TTL (persistent)
- Archive: 90-day TTL set at archive time

**Cleanup approach**: SCAN-based iteration with date parsing from key names. Delete keys where the embedded date is older than the retention threshold. This is idempotent and safe — worst case it deletes a key that Redis would have expired anyway.

**Key patterns to scan**:
- `memora:lb:daily:*` — extract date, delete if >30 days old
- `memora:lb:weekly:*` — extract date, delete if >90 days old
- `memora:lb:archive:daily:*` — extract date, delete if >90 days old
- `memora:lb:archive:weekly:*` — extract date, delete if >90 days old

---

## R5: Redis Memory Monitoring

**Decision**: Dual monitoring — FastAPI health endpoint (on-demand) + Frappe scheduled task (periodic logging).

**Rationale**: Need both real-time health checks (for load balancers/monitoring) and periodic alerting (for proactive detection of buffer backlog or memory pressure).

**Health endpoint design**:
- Path: `GET /api/v1/health/redis`
- No auth required (internal monitoring)
- Data sources: `INFO memory`, `LLEN` (buffer), `SCARD` (dirty sets), `INFO clients`
- Response time: <5ms (all Redis INFO commands)

**Monitoring task design**:
- Schedule: Every 5 minutes via `hooks.py`
- Checks: memory usage %, buffer length, dirty set sizes, key count
- Alerting: structlog WARNING for memory >80%, dirty sets >1000
- Alerting: structlog CRITICAL for buffer >10000

**Dynamic batch sizing for interaction buffer**:
- Current: Fixed 5000 batch size
- Proposed: 1000 default, scale to 5000 when buffer >50000
- This is a simple conditional in `flush_interaction_buffer()`, not a new task

---

## R6: Systemd Service Configuration

**Decision**: Standard Redis systemd service with `Restart=always`, `LimitNOFILE=65535`.

**Rationale**: Must survive process crashes and server reboots. systemd provides automatic restart with configurable delay.

**Service file**: `/etc/systemd/system/redis-memora.service`
**Config file**: `/etc/redis/redis-memora.conf`
**Data directory**: `/var/lib/redis-memora/`
**Log file**: `/var/log/redis/redis-memora.log`

**Key config values** (dev):
```
port 13001
bind 127.0.0.1
maxmemory 128mb
maxmemory-policy volatile-ttl
appendonly yes
appendfsync everysec
dir /var/lib/redis-memora
logfile /var/log/redis/redis-memora.log
```

**Key config values** (production differences):
| Setting | Dev | Production |
|---------|-----|-----------|
| `maxmemory` | 128mb | 512mb–1gb (based on user count) |
| `maxmemory-policy` | volatile-ttl | volatile-ttl |
| `tcp-backlog` | 511 (default) | 1024 |
| `timeout` | 0 | 300 |

---

## R7: Migration Strategy

**Decision**: Zero-downtime migration via flush-then-switch pattern.

**Rationale**: During the migration window, data exists on port 13000. After switching, all services auto-hydrate from MariaDB on cache miss.

**Steps**:
1. Start new Redis instance on 13001 (can run in parallel)
2. Trigger all sync tasks manually to flush dirty sets to MariaDB
3. Update all config files atomically:
   - `.env`: REDIS_URL=redis://127.0.0.1:13001
   - `site_config.json`: add `redis_memora` key
4. Restart all services:
   - `pkill -f "uvicorn fastapi_app.main:app"` (FastAPI)
   - `bench restart` (Frappe workers)
5. Verify via health check: `curl http://127.0.0.1:8002/api/v1/health/redis`
6. First API calls will trigger `ensure_hydrated()` — cache warming is automatic

**Risk**: Zero — the self-healing pattern handles empty Redis gracefully. The only cost is a brief period of slightly higher latency as caches warm up.
