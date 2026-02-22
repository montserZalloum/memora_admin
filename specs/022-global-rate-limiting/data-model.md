# Data Model: Global API Rate Limiting

**Date**: 2026-02-22
**Branch**: `022-global-rate-limiting`

## Overview

Rate limiting uses ephemeral Redis counters with TTLs. No MariaDB tables are created. All state auto-expires and is fully reconstructable (counter resets to 0 on key expiry).

## Entities

### 1. Global Rate Limit Counter

**Storage**: Redis STRING with auto-increment
**TTL**: 60 seconds (configurable)
**Key pattern**: `memora:global_rl:ip:{ip_address}`

| Field | Type | Description |
|-------|------|-------------|
| value | int | Request count within current window |
| TTL | int | Seconds until key expires (set on first request) |

**Lifecycle**:
1. First request from IP: `INCR` creates key with value 1, `EXPIRE` sets 60s TTL
2. Subsequent requests: `INCR` increments atomically
3. After TTL expires: Key auto-deleted, counter resets

**Scale estimate**: At 100k concurrent users, worst case ~100k keys x ~50 bytes = ~5MB Redis memory.

### 2. Player Write Rate Limit Counter

**Storage**: Redis STRING with auto-increment
**TTL**: 60 seconds (configurable)
**Key patterns**:
- `memora:rl:reviews:{player_id}`
- `memora:rl:session_start:{player_id}`
- `memora:rl:session_end:{player_id}`

| Field | Type | Description |
|-------|------|-------------|
| value | int | Write request count within current window |
| TTL | int | Seconds until key expires |

**Lifecycle**: Same as global counter.

**Scale estimate**: Only active players who call write endpoints generate keys. At peak: ~10k active players x 3 keys x ~60 bytes = ~1.8MB.

### 3. WebSocket Connection Counter

**Storage**: In-memory (`ConnectionManager._connections` dict)
**No Redis key**: Connection state is inherently per-process.

| Field | Type | Description |
|-------|------|-------------|
| user_id | str | Player ID from JWT |
| connections | set[WebSocket] | Set of active WebSocket objects |
| count | int (derived) | `len(connections)` — checked against limit |

**Lifecycle**:
1. `connect()`: Check `len(self._connections[user_id]) < max_limit` before accepting
2. If at limit: Reject with close code 4029 before `websocket.accept()`
3. `disconnect()`: Remove from set (automatic via existing cleanup)

## Relationships

```
Global IP Counter  ──(1:1)──  IP Address (per 60s window)
Player Write Counter  ──(1:1)──  Player + Endpoint Scope (per 60s window)
Connection Counter  ──(1:N)──  Player to WebSocket objects (in-memory)
```

No foreign keys, no cross-entity dependencies. Each counter is independent and self-contained.

## Redis Key Naming Convention

All new keys follow existing `memora:` prefix convention:

| Key Pattern | Prefix | Purpose |
|-------------|--------|---------|
| `memora:global_rl:ip:{ip}` | `memora:global_rl:` | Global per-IP rate limit |
| `memora:rl:reviews:{player_id}` | `memora:rl:` | Per-player review submit limit |
| `memora:rl:session_start:{player_id}` | `memora:rl:` | Per-player session start limit |
| `memora:rl:session_end:{player_id}` | `memora:rl:` | Per-player session end limit |

No collision with existing keys:
- `memora:ratelimit:` — existing login rate limiter (unchanged)
- `memora:global_rl:` — new global rate limiter (distinct prefix)
- `memora:rl:` — new per-player rate limiter (distinct prefix)

## Lua Script (Atomic Increment)

Same script as existing `RateLimiter`, reused:

```lua
local count = redis.call("INCR", KEYS[1])
if count == 1 then
    redis.call("EXPIRE", KEYS[1], ARGV[1])
end
local ttl = redis.call("TTL", KEYS[1])
return {count, ttl}
```

**Atomicity guarantee**: INCR + conditional EXPIRE + TTL in a single Lua execution = no race conditions, single round-trip.
**Return value**: `{count, ttl}` — count used to compute `X-RateLimit-Remaining = limit - count`, ttl used to compute `X-RateLimit-Reset = now + ttl`.

## Configuration Settings

Added to `fastapi_app/core/config.py` `Settings` class:

| Setting | Type | Default | Environment Variable |
|---------|------|---------|---------------------|
| `global_rate_limit` | int | 100 | `GLOBAL_RATE_LIMIT` |
| `global_rate_limit_window` | int | 60 | `GLOBAL_RATE_LIMIT_WINDOW` |
| `reviews_rate_limit` | int | 30 | `REVIEWS_RATE_LIMIT` |
| `session_rate_limit` | int | 10 | `SESSION_RATE_LIMIT` |
| `ws_max_connections_per_user` | int | 5 | `WS_MAX_CONNECTIONS_PER_USER` |
