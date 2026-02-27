# Data Model: 100k Concurrency Scaling Optimizations

**Feature Branch**: `029-concurrency-scaling` | **Date**: 2026-02-27

## Overview

This feature modifies no database tables or DocTypes. All changes are to in-memory configuration objects, service classes, and middleware. The "data model" here refers to configuration entities and runtime state structures.

---

## Entity: Settings (Modified)

**Source**: `fastapi_app/core/config.py` → `Settings` class
**Type**: Pydantic BaseSettings (env-var loaded, `@lru_cache` singleton)

### New Fields

| Field | Type | Default | Env Var | Purpose |
|-------|------|---------|---------|---------|
| `redis_max_connections` | `int` | `20` | `REDIS_MAX_CONNECTIONS` | Redis connection pool size per worker |
| `ws_broadcast_concurrency` | `int` | `0` | `WS_BROADCAST_CONCURRENCY` | 0=sequential, >0=parallel with semaphore |
| `rate_limit_fail_open` | `bool` | `True` | `RATE_LIMIT_FAIL_OPEN` | Fail behavior when Redis unavailable |
| `frappe_timeout` | `float` | `30.0` | `FRAPPE_TIMEOUT` | Upstream HTTP client timeout (seconds) |
| `frappe_max_connections` | `int` | `100` | `FRAPPE_MAX_CONNECTIONS` | Upstream HTTP client pool size |
| `frappe_max_keepalive` | `int` | `20` | `FRAPPE_MAX_KEEPALIVE` | Upstream HTTP client keepalive pool |

### Validation Rules

- `redis_max_connections` ≥ 1
- `ws_broadcast_concurrency` ≥ 0 (0 = disabled/sequential)
- `frappe_timeout` > 0
- `frappe_max_connections` ≥ 1
- `frappe_max_keepalive` ≥ 0 and ≤ `frappe_max_connections`

### Unchanged Fields (for reference)

All existing fields remain with identical defaults:
- `redis_url`, `jwt_secret`, `bitmap_json_path`, `frappe_url`, `frappe_site` (required)
- `global_rate_limit=100`, `global_rate_limit_window=60`, `reviews_rate_limit=30`, etc.

---

## Entity: ConnectionManager (Modified)

**Source**: `fastapi_app/core/ws_manager.py` → `ConnectionManager` class
**Type**: In-memory singleton (created during app lifespan)

### State Changes

| Field | Before | After | Purpose |
|-------|--------|-------|---------|
| `_lock` | `asyncio.Lock()` (global) | Removed | Eliminated single contention point |
| `_user_locks` | N/A | `dict[str, asyncio.Lock]` | Per-user operation serialization |
| `_lock_guard` | N/A | `asyncio.Lock()` | Lock creation/deletion guard |
| `_broadcast_concurrency` | N/A | `int` (from settings) | Controls parallel vs sequential sends |

### Lifecycle

- **Lock creation**: On first `connect()` for a user, create `asyncio.Lock()` in `_user_locks[user_id]`
- **Lock cleanup**: On last `disconnect()` for a user (when connection set becomes empty), delete lock from `_user_locks[user_id]`
- **Guard lock**: Held only briefly for dict mutation (lock create/delete), never during actual WebSocket operations

---

## Entity: ProgressService (Modified)

**Source**: `fastapi_app/services/progress.py` → `ProgressService` class

### Method Changes

| Method | Before | After | Impact |
|--------|--------|-------|--------|
| `get_completed_bits()` | Pipeline of N GETBIT commands | Single GET + client-side bitmap decode | N commands → 1 command |

### Bitmap Decode Logic

```
Input: Redis GET returns text string (decode_responses=True, latin-1 lossless)
Step 1: Encode string to bytes via latin-1
Step 2: For each bit_index in range(bit_range):
  - byte_idx = bit_index // 8
  - bit_offset = bit_index % 8
  - Check: bitmap_bytes[byte_idx] & (0x80 >> bit_offset)
Output: set[int] of completed bit indexes
```

---

## Entity: GlobalRateLimitMiddleware (Modified)

**Source**: `fastapi_app/middleware/rate_limit.py`

### Constructor Changes

| Parameter | Before | After |
|-----------|--------|-------|
| `limit` | int | int (unchanged) |
| `window` | int | int (unchanged) |
| `fail_open` | N/A | bool (new) |

### Behavior Matrix

| Redis Available | `fail_open=True` | `fail_open=False` |
|-----------------|-------------------|---------------------|
| Yes, under limit | Allow + headers | Allow + headers |
| Yes, over limit | 429 + Retry-After | 429 + Retry-After |
| No (error) | Allow (warn log) | 503 + Retry-After: 5 |

---

## Entity: Redis Connection Pool (Modified)

**Source**: `fastapi_app/core/redis.py` → `create_redis_pool()`

### Changes

| Aspect | Before | After |
|--------|--------|-------|
| `max_connections` | Hardcoded `20` | `settings.redis_max_connections` |
| Startup logging | URL only | URL + pool size |

---

## Entity: FrappeClient (Modified)

**Source**: `fastapi_app/services/frappe_client.py`

### Constructor Changes

| Parameter | Before | After |
|-----------|--------|-------|
| `timeout` | Hardcoded `30.0` | `settings.frappe_timeout` |
| `max_connections` | Hardcoded `100` | `settings.frappe_max_connections` |
| `max_keepalive_connections` | Hardcoded `20` | `settings.frappe_max_keepalive` |

---

## No New Redis Keys

This feature does not introduce new Redis key patterns. All existing key patterns remain unchanged.

## No New Database Tables / DocTypes

This feature does not modify any MariaDB tables or Frappe DocTypes.
