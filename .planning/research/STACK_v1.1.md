# Stack Research: v1.1 Feature Expansion

**Milestone:** v1.1 Feature Expansion (Game Sessions, Leaderboards, Device Management, Scheduled Tasks)
**Researched:** 2026-02-02
**Overall Confidence:** HIGH

## Executive Summary

The v1.1 feature expansion requires **ZERO new dependencies**. All new features (game sessions with TTL, leaderboards using sorted sets, device management, and scheduled tasks) can be implemented using the existing validated stack:

- **redis-py 5.0+** already provides full async support for sorted sets (ZADD, ZRANGE, ZINCRBY, ZREVRANGE) and key expiration (SETEX, EXPIRE)
- **Frappe scheduler** already handles cron-based scheduling (daily, hourly) via hooks.py
- **FastAPI dependency injection** already supports session state management patterns

The only recommendation is to **upgrade redis-py from 5.0.0 to 7.1.0** for improved async performance, Python 3.14 support, and consistency improvements in sorted set commands (score_cast_func added to zrank, zrevrank, zunion in v7.0.0).

No additional libraries (APScheduler, Celery, device fingerprinting SDKs) are needed. The existing architecture is already optimized for the new features.

## Current Stack (Validated in v1.0)

| Technology | Current Version | Status |
|------------|----------------|--------|
| FastAPI | 0.115.0+ | Retained |
| redis-py | 5.0.0 | Upgrade recommended |
| uvicorn | 0.27.0+ | Retained |
| Pydantic | 2.0+ (via pydantic-settings) | Retained |
| PyJWT | 2.11.0 | Retained |
| orjson | Latest | Retained |
| structlog | 24.0.0+ | Retained |
| Frappe | v15 | Retained |
| Redis server | 6.0.16 | Retained (server version) |

## Recommended Stack Changes

### Core Change: Upgrade redis-py

| Technology | From | To | Purpose | Rationale |
|------------|------|-----|---------|-----------|
| redis-py | 5.0.0 | 7.1.0 | Redis async operations | Performance improvements, Python 3.14 support, sorted set consistency fixes |

**Why upgrade:**
- **Performance:** Version 5.3.0 introduced async.Lock in connection pool acquisition; v7.x optimizes this further
- **Sorted set consistency:** v7.0.0 added `score_cast_func` argument to zrank, zrevrank, zunion for consistency with other sorted set commands
- **Python compatibility:** v7.1.0 supports Python 3.10-3.14 (v5.0 only to 3.9)
- **Async improvements:** Better handling of timeout typehints (int → float) in async BlockingConnectionPool
- **Lock safety:** Replaced threading.Lock with RLock to avoid deadlocks

**Breaking changes:** None that affect current usage. The project already uses redis-py 5.0+ async patterns.

**Installation:**
```bash
pip install redis>=7.1.0
```

### What NOT to Add

| Technology | Why Considered | Why NOT Adding |
|------------|----------------|----------------|
| APScheduler | Python task scheduling | Frappe scheduler already handles cron-based scheduling via hooks.py. Adding APScheduler would introduce redundant dependency and require separate process management. |
| Celery + Celery Beat | Distributed task queue | Overkill for current scale. Frappe scheduler is sufficient for daily/hourly tasks. No distributed worker requirements. Would require message broker (RabbitMQ/Redis as broker) adding complexity. |
| fingerprintjs/fingerprint-pro | Device fingerprinting | Privacy concerns (lacks consent, regulatory risk). Simple device ID from client header is sufficient for 3-device limit. No fraud detection requirements. |
| redis-om-python | Object mapping for Redis | Unnecessary abstraction. Direct redis-py commands give better performance visibility and control. Project already has clean service layer patterns. |
| croniter | Cron expression parsing | Frappe scheduler already supports cron expressions natively in hooks.py scheduler_events. |

## Feature Implementation with Existing Stack

### Game Sessions (Redis Keys with TTL)

**Required capabilities:**
- Create session key with TTL (auto-expiration)
- Store session data (JSON serialized)
- Update session fields atomically
- Retrieve active session

**Existing stack coverage:**
```python
# redis-py 5.0+ async API (already available)
await redis_client.setex(
    f"memora:session:{user_id}:{lesson_id}",
    ttl=3600,  # 1 hour TTL
    value=orjson.dumps(session_data)
)

# Update session
await redis_client.hset(
    f"memora:session:{user_id}:{lesson_id}",
    mapping={
        "stage_index": stage_index,
        "last_activity": timestamp
    }
)

# Check TTL
ttl_remaining = await redis_client.ttl(session_key)
```

**Performance:** O(1) for all operations. TTL handled by Redis server (no cleanup needed).

**Sources:**
- [Redis EXPIRE command](https://redis.io/docs/latest/commands/expire/)
- [Best Practices for Using Redis EXPIRE and TTL Commands](https://devops.aibit.im/article/best-practices-redis-expire-ttl)

### Leaderboards (Redis Sorted Sets)

**Required capabilities:**
- Add player score to leaderboard (ZADD)
- Increment player score atomically (ZINCRBY)
- Get top N players (ZREVRANGE with scores)
- Get player rank (ZREVRANK)
- Daily reset (delete key or ZADD with new scores)

**Existing stack coverage:**
```python
# redis-py 5.0+ async sorted set API (already available)

# Add/update score
await redis_client.zadd(
    "memora:leaderboard:daily:2026-02-02",
    {user_id: xp_score}
)

# Atomic increment
await redis_client.zincrby(
    "memora:leaderboard:alltime",
    amount=50,
    value=user_id
)

# Get top 10 with scores
top_players = await redis_client.zrevrange(
    "memora:leaderboard:daily:2026-02-02",
    start=0,
    end=9,
    withscores=True
)

# Get player rank (0-indexed)
rank = await redis_client.zrevrank(
    "memora:leaderboard:alltime",
    user_id
)
```

**Performance:**
- ZADD/ZINCRBY: O(log N) where N is leaderboard size
- ZREVRANGE: O(log N + M) where M is returned elements
- ZREVRANK: O(log N)

**Best practices (from research):**
- Use ZREMRANGEBYRANK to cap leaderboard size (e.g., keep top 1000)
- Daily leaderboards: Key includes date, auto-expires after 7 days
- Use pipelining for bulk updates to reduce network round-trips

**Sources:**
- [Redis Leaderboards Official Guide](https://redis.io/solutions/leaderboards/)
- [Redis Sorted Sets: 9+ Proven Best Practices](https://www.dragonflydb.io/guides/redis-sorted-sets-best-practices)
- [Redis sorted sets documentation](https://redis.io/docs/latest/develop/data-types/sorted-sets/)

### Device Management (Redis Sets)

**Required capabilities:**
- Register device (SADD to user's device set)
- Check device count (SCARD)
- Enforce 3-device limit
- List user devices (SMEMBERS)
- Remove device (SREM)

**Existing stack coverage:**
```python
# redis-py 5.0+ async set API (already available)

# Check device count and enforce limit
device_count = await redis_client.scard(f"memora:devices:{user_id}")
if device_count >= 3:
    raise MaxDevicesExceeded

# Register new device
await redis_client.sadd(
    f"memora:devices:{user_id}",
    device_id
)

# Check if device is registered
is_registered = await redis_client.sismember(
    f"memora:devices:{user_id}",
    device_id
)
```

**Performance:** All operations O(1).

**Device ID strategy:**
- Client-provided device identifier (from app header or JWT claim)
- Simple string format: `{platform}:{device_uuid}` (e.g., "android:abc-123-def")
- No server-side fingerprinting needed (privacy-compliant)

**Sources:**
- [Understanding Mobile Device ID Tracking in 2026](https://ingestlabs.com/mobile-device-id-tracking-guide/)
- Note: Device fingerprinting has significant privacy/compliance risks in 2026; simple client-provided ID is sufficient

### Scheduled Tasks (Frappe Scheduler)

**Required capabilities:**
- Daily task execution (streak reset at midnight)
- Hourly task execution (session cleanup)
- Task error handling and logging

**Existing stack coverage:**

Already implemented in v1.0 via hooks.py scheduler_events (cron expressions):
```python
scheduler_events = {
    "cron": {
        # Existing (every 1 minute)
        "* * * * *": [
            "memora_admin.memora_admin.tasks.sync.sync_dirty_progress",
            "memora_admin.memora_admin.tasks.sync.sync_dirty_wallets",
            "memora_admin.memora_admin.tasks.sync.flush_interaction_buffer",
        ],
        # Existing (every 2 minutes)
        "*/2 * * * *": [
            "memora_admin.memora_admin.tasks.build_worker.process_pending_builds"
        ],
        # NEW for v1.1: Daily at midnight (0 0 * * *)
        # NEW for v1.1: Hourly (0 * * * *)
    }
}
```

**Implementation pattern (from existing code):**
1. Define cron schedule in hooks.py
2. Implement task function in `memora_admin/memora_admin/tasks/{module}.py`
3. Use `get_redis()` helper for Redis connection
4. Log to Memora Sync Log DocType for audit trail
5. Error handling with `frappe.log_error()`

**Advantages over APScheduler/Celery:**
- Zero additional dependencies
- Integrated with Frappe's process management
- Automatic error logging to Frappe
- No separate process/worker setup needed
- Croniter already built into Frappe

**Sources:**
- [Frappe Background Jobs Documentation](https://docs.frappe.io/framework/user/en/api/background_jobs)
- [Python Job Scheduling: Methods and Overview in 2026](https://research.aimultiple.com/python-job-scheduling/)

## Integration Points

### 1. FastAPI Service Layer

**Pattern (already established in v1.0):**
```python
# services/sessions.py
from redis.asyncio import Redis
from typing import Annotated
from fastapi import Depends

async def get_redis() -> Redis:
    """Dependency injected Redis client."""
    # Returns app.state.redis_pool client

async def create_session(
    user_id: str,
    lesson_id: str,
    redis: Annotated[Redis, Depends(get_redis)]
) -> SessionState:
    # Use redis parameter for all operations
    await redis.setex(...)
```

**No changes needed** - existing dependency injection pattern works for all new features.

**Sources:**
- [Dependency Injection in FastAPI: 2026 Playbook](https://thelinuxcode.com/dependency-injection-in-fastapi-2026-playbook-for-modular-testable-apis/)
- [FastAPI Dependencies Best Practices](https://fastapi.tiangolo.com/tutorial/dependencies/)

### 2. Redis Connection Pooling

**Current implementation (from main.py):**
```python
# Lifespan: creates pool once, shares across all requests
pool = await create_redis_pool()
app.state.redis_pool = pool

# Each request gets client from pool
redis_client = redis.Redis(connection_pool=pool)
```

**Compatibility:** redis-py 7.1.0 maintains backward compatibility with this pattern. No changes required.

### 3. Frappe ↔ Redis Sync

**Current pattern (from tasks/sync.py):**
```python
def get_redis():
    """Get Redis connection using Frappe site config."""
    return redis.from_url(frappe.conf.redis_cache)
```

**For v1.1:** Use same pattern for new scheduled tasks (streak reset, session cleanup). No architectural changes.

### 4. Lua Scripts for Atomicity

**When to use (from research):**
- Leaderboard updates with rank calculation in single operation
- Session state transitions with multiple field updates
- Device registration with automatic eviction of oldest device if limit exceeded

**Example (optional optimization):**
```python
# Atomic device registration with limit enforcement
device_limit_lua = """
local key = KEYS[1]
local device_id = ARGV[1]
local max_devices = tonumber(ARGV[2])

local count = redis.call('SCARD', key)
if count >= max_devices then
    return 0  -- Limit exceeded
end

redis.call('SADD', key, device_id)
return 1  -- Success
"""

# Register script once at startup
script_sha = await redis_client.script_load(device_limit_lua)

# Execute atomically
result = await redis_client.evalsha(
    script_sha,
    keys=[f"memora:devices:{user_id}"],
    args=[device_id, "3"]
)
```

**Recommendation:** Start with simple commands (SCARD → check → SADD). Add Lua scripts only if race conditions observed in production.

**Sources:**
- [Redis Lua Scripting for Atomic Operations](https://redis.io/docs/latest/develop/programmability/eval-intro/)
- [Fixing Race Conditions in Redis Counters](https://dev.to/silentwatcher_95/fixing-race-conditions-in-redis-counters-why-lua-scripting-is-the-key-to-atomicity-and-reliability-38a4)

## Migration Path

### Step 1: Upgrade redis-py

```bash
# Update requirements.txt
redis>=7.1.0  # was: redis>=5.0.0

# Install
pip install -r requirements.txt

# Verify
python -c "import redis; print(redis.__version__)"
# Expected: 7.1.0 or higher
```

**Risk:** LOW. No breaking changes for async sorted sets or basic operations.

**Testing:** Run existing v1.0 integration tests to verify backward compatibility.

### Step 2: Implement New Features

All new features use existing redis-py async API:
- Sessions: `setex`, `hset`, `ttl`, `get`
- Leaderboards: `zadd`, `zincrby`, `zrevrange`, `zrevrank`
- Devices: `sadd`, `scard`, `sismember`, `srem`
- Scheduled tasks: Add to hooks.py, implement in tasks/ module

**No new dependencies to install.**

## Performance Validation

### Expected Latencies (based on v1.0 achievements)

| Operation | Expected | Note |
|-----------|----------|------|
| Create session | <5ms | Single SETEX |
| Update session | <3ms | HSET with 2-3 fields |
| Get session | <2ms | Single GET or HGETALL |
| Update leaderboard | <10ms | ZINCRBY on sorted set with ~10K users |
| Get top 10 leaderboard | <5ms | ZREVRANGE 0-9 with scores |
| Check device count | <2ms | SCARD |
| Register device | <3ms | SADD |

**Assumptions:**
- Redis server: 6.0.16 on same network (existing infrastructure)
- Leaderboard size: <100K active users per leaderboard
- Connection pooling: 10 connections in pool (existing config)

**Validation:** Use structlog metrics (already configured in v1.0) to track actual latencies.

## Testing Strategy

### Unit Tests

All new features testable with existing pytest + fakeredis setup:
```python
# tests/test_sessions.py
import pytest
from fakeredis import FakeAsyncRedis

@pytest.fixture
async def redis_client():
    return FakeAsyncRedis()

async def test_create_session(redis_client):
    # Test session creation with TTL
    await redis_client.setex("session:123", 3600, "data")
    assert await redis_client.ttl("session:123") > 0
```

**No new testing dependencies needed.**

### Integration Tests

Use existing FastAPI TestClient with redis override:
```python
# tests/test_leaderboards.py
from fastapi.testclient import TestClient

def test_leaderboard_update():
    # Override redis dependency with fakeredis
    # Test full leaderboard flow
```

**Pattern already established in v1.0.**

## Open Questions / Validation Needed

None. All required capabilities verified in existing stack.

## Summary

| Requirement | Solution | Stack Change |
|-------------|----------|--------------|
| Game sessions with TTL | redis-py SETEX, EXPIRE, TTL | None (upgrade recommended) |
| Leaderboards | redis-py sorted sets (ZADD, ZREVRANGE, ZINCRBY) | None (upgrade recommended) |
| Device management | redis-py sets (SADD, SCARD, SISMEMBER) | None |
| Scheduled tasks | Frappe scheduler hooks.py cron | None |
| Performance | Connection pooling, async operations | None (already optimized) |

**Total new dependencies: ZERO**

**Recommended upgrade: redis-py 5.0.0 → 7.1.0** for performance, consistency, and Python 3.14 support.

## Sources

### Official Documentation
- [Redis Sorted Sets Documentation](https://redis.io/docs/latest/develop/data-types/sorted-sets/)
- [Redis EXPIRE Command](https://redis.io/docs/latest/commands/expire/)
- [Redis Leaderboards Guide](https://redis.io/solutions/leaderboards/)
- [FastAPI Dependencies](https://fastapi.tiangolo.com/tutorial/dependencies/)
- [Frappe Background Jobs](https://docs.frappe.io/framework/user/en/api/background_jobs)

### redis-py Releases
- [redis-py Releases on GitHub](https://github.com/redis/redis-py/releases)
- [redis-py 7.1.0 Release Notes](https://github.com/redis/redis-py/releases) - Latest stable version

### Best Practices (2026)
- [Redis Sorted Sets: 9+ Proven Best Practices](https://www.dragonflydb.io/guides/redis-sorted-sets-best-practices)
- [Best Practices for Redis EXPIRE and TTL](https://devops.aibit.im/article/best-practices-redis-expire-ttl)
- [Dependency Injection in FastAPI: 2026 Playbook](https://thelinuxcode.com/dependency-injection-in-fastapi-2026-playbook-for-modular-testable-apis/)
- [Python Job Scheduling: Methods and Overview in 2026](https://research.aimultiple.com/python-job-scheduling/)

### Performance & Atomicity
- [Redis Lua Scripting for Atomic Operations](https://redis.io/docs/latest/develop/programmability/eval-intro/)
- [Fixing Race Conditions in Redis Counters with Lua](https://dev.to/silentwatcher_95/fixing-race-conditions-in-redis-counters-why-lua-scripting-is-the-key-to-atomicity-and-reliability-38a4)

### Device Management
- [Understanding Mobile Device ID Tracking in 2026](https://ingestlabs.com/mobile-device-id-tracking-guide/)
- Note: Device fingerprinting has privacy/compliance concerns; simple client-provided IDs recommended
