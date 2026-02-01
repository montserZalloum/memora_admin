# Architecture Research: FastAPI Sidecar Integration with Frappe

**Domain:** Gamified Education Platform Backend
**Researched:** 2026-02-01
**Overall Confidence:** MEDIUM-HIGH

## Executive Summary

This research addresses how a FastAPI sidecar should integrate with the existing Frappe v15 application for Memora. The architecture follows a "sidecar" pattern where FastAPI handles high-performance game mechanics (sub-20ms responses) while Frappe manages admin, content management, and persistent storage. The two applications share a Redis instance for hot data and communicate via Redis pub/sub for cache invalidation.

---

## Component Boundaries

### Frappe Application (Port 8000)
**Owns:**
- Content management (Subject, Track, Unit, Topic, Lesson, Stage DocTypes)
- Academic structure (Grade, Major, Season, Academic Plan)
- Player master data (Profile creation, subscription management)
- Business logic (Product Grant, Plan Overrider)
- Build queue orchestration (triggers builds, tracks status)
- Cold data persistence (MariaDB as source of truth)
- Admin UI and content editing workflows

**Does NOT own:**
- Real-time game state (sessions, live progress)
- High-frequency read operations (progress checks, access validation)
- Leaderboard calculations
- Real-time wallet queries

### FastAPI Sidecar (Port 8001)
**Owns:**
- Game API endpoints (progress, wallet, leaderboard)
- Session management (start/end lesson, stage completion)
- Hot data layer (Redis reads/writes)
- Access control validation (double-gate checks)
- Interaction buffering (pre-batch collection)
- JWT verification (stateless, no Frappe dependency per request)

**Does NOT own:**
- Content creation/editing
- User registration/profile creation
- Subscription purchases
- Build execution (only receives invalidation signals)

### Redis (Shared Instance)
**Partitioned by key prefix:**

| Prefix | Owner | Purpose |
|--------|-------|---------|
| `frappe:*` | Frappe | Framework cache, sessions, RQ queues |
| `memora:cache:*` | Frappe | Content cache (lesson info) |
| `memora:progress:*` | FastAPI (write) / Frappe (sync read) | Player progress bitmaps |
| `memora:wallet:*` | FastAPI (write) / Frappe (sync read) | Player XP, streak data |
| `memora:access:*` | FastAPI (read) / Frappe (write) | Access grant sets |
| `memora:season:*` | FastAPI (read) / Frappe (write) | Season meta (status, end_ts) |
| `memora:session:*` | FastAPI | Active game sessions |
| `memora:leaderboard:*` | FastAPI | Sorted sets for rankings |
| `memora:dirty:*` | FastAPI (write) / Frappe (read) | Sync queue markers |
| `memora:buffer:*` | FastAPI (write) / Frappe (read) | Interaction buffer lists |
| `memora:build:*` | Frappe | Build queue and status |
| `memora:invalidate` | Frappe (pub) / FastAPI (sub) | Cache invalidation channel |

**Confidence:** HIGH - Based on [Redis documentation](https://redis.io/docs/latest/develop/data-types/) and standard key namespacing practices.

### MariaDB (Frappe-Owned)
**Data stored:**
- All DocType records (source of truth)
- Synced progress (hex-encoded bitmaps from Redis)
- Synced wallet state (periodic snapshots)
- Interaction logs (batch-inserted from Redis buffer)
- Sync logs, build queue history

**FastAPI access:** Read-only for initial hydration, no direct writes.

### Mock CDN Layer
**Purpose:** Abstract storage for generated JSON files.
**Interface:**
- `upload(key, content)` - Store JSON file
- `get_url(key)` - Return accessible URL
- `invalidate(key)` - Remove from cache

**Swap strategy:** Mock implementation stores locally; production swaps to Cloudflare R2 client.

---

## Data Flow

### 1. Content Change to CDN Build

```
[Admin edits Lesson in Frappe UI]
         |
         v
[Frappe doc_events.on_update hook]
         |
         v
[frappe.enqueue() to build queue]  ------> [Redis: memora:build:pending SET]
         |
         v
[Scheduled task: process_pending_builds (every 2 min)]
         |
         v
[Build worker generates JSON files]
    |-- _h.json (hierarchy)
    |-- _b.json (bitmap structure with bit_range, excluded_bits)
    |-- *_c.json (unit content)
    |-- lesson JSON (stages, XP, hearts)
         |
         v
[Upload to CDN (mock/R2)]
         |
         v
[Publish to Redis: memora:invalidate channel]
         |
         v
[FastAPI subscriber clears local cache]
```

**Confidence:** MEDIUM - Pattern based on [Netlify build pipeline](https://www.netlify.com/guide-to-composable-architecture/ci-cd-building-deploying-hosting/build-pipeline/) and Redis pub/sub documentation. Frappe hooks pattern from [Frappe docs](https://docs.frappe.io/framework/user/en/api/background_jobs).

### 2. Game Session Flow (Hot Path)

```
[Student starts lesson]
         |
         v
[POST /api/v1/lessons/{id}/start] --> FastAPI
         |
         v
[Verify JWT (stateless)]
         |
         v
[Double-gate access check]
    |-- Gate 1: Redis GET memora:season:{season_id} (status + end_ts)
    |-- Gate 2: Redis SISMEMBER memora:access:{player_id} (subject check)
         |
         v
[Create session: Redis HSET memora:session:{session_id} + EXPIRE]
         |
         v
[Return session_id to client]


[Student completes stage]
         |
         v
[POST /api/v1/sessions/{id}/stages/{stage}/complete]
         |
         v
[Verify session exists]
         |
         v
[Record interaction: Redis RPUSH memora:buffer:interactions]
         |
         v
[Update progress: Redis SETBIT memora:progress:{player}:{subject} {bit_position} 1]
         |
         v
[Mark dirty: Redis SADD memora:dirty:progress {player}:{subject}]
         |
         v
[Return success (<10ms target)]


[Student ends lesson]
         |
         v
[POST /api/v1/sessions/{id}/complete]
         |
         v
[Calculate XP (base + heart bonus)]
         |
         v
[Update wallet: Redis HINCRBY memora:wallet:{player} xp {amount}]
         |
         v
[Update streak: Redis HSET memora:wallet:{player} streak, streak_date]
         |
         v
[Mark dirty: Redis SADD memora:dirty:wallets {player}]
         |
         v
[Delete session: Redis DEL memora:session:{session_id}]
         |
         v
[Return completion summary (<30ms target)]
```

**Confidence:** HIGH - Based on [Redis SETBIT/GETBIT documentation](https://redis.io/docs/latest/commands/setbit/) and [FastAPI background tasks patterns](https://fastapi.tiangolo.com/tutorial/background-tasks/).

### 3. Sync Flow (Cold Path)

```
[Frappe scheduled task: every 1 min]
         |
         v
[Read dirty sets from Redis]
    |-- SMEMBERS memora:dirty:progress
    |-- SMEMBERS memora:dirty:wallets
         |
         v
[For each dirty progress:]
    |-- GET memora:progress:{player}:{subject} (raw bitmap bytes)
    |-- Convert to hex string
    |-- UPDATE Memora Structure Progress SET progress_hex = ... WHERE ...
         |
         v
[For each dirty wallet:]
    |-- HGETALL memora:wallet:{player}
    |-- UPDATE Memora Player Wallet SET xp = ..., streak = ... WHERE ...
         |
         v
[Flush interaction buffer:]
    |-- LRANGE memora:buffer:interactions 0 -1
    |-- Batch INSERT INTO `tabMemora Interaction Log` (...)
    |-- LTRIM memora:buffer:interactions {count} -1
         |
         v
[Clear dirty sets:]
    |-- DEL memora:dirty:progress
    |-- DEL memora:dirty:wallets
         |
         v
[Log to Memora Sync Log]
```

**Confidence:** MEDIUM - Sync pattern derived from standard [Redis pipeline patterns](https://redis.io/docs/latest/develop/clients/jedis/transpipe/) and batch insert best practices.

### 4. Authentication Flow

```
[User logs in via Frappe]
         |
         v
[Frappe validates credentials]
         |
         v
[Generate JWT with player_id, exp, iat]
    |-- Sign with shared secret (from Memora Settings)
         |
         v
[Store refresh token: Redis SET memora:refresh:{token} {player_id} EX {30 days}]
         |
         v
[Return access_token (short-lived) + refresh_token]


[Game API request]
         |
         v
[FastAPI extracts JWT from Authorization header]
         |
         v
[Verify signature with shared secret (no Redis/DB call)]
         |
         v
[Extract player_id from claims]
         |
         v
[Proceed to endpoint handler]
```

**Confidence:** HIGH - Standard JWT stateless verification pattern per [FastAPI JWT documentation](https://fastapi.tiangolo.com/tutorial/security/oauth2-jwt/).

---

## Integration Patterns

### Nginx Reverse Proxy Configuration

```nginx
upstream frappe_backend {
    server 127.0.0.1:8000;
}

upstream fastapi_backend {
    server 127.0.0.1:8001;
}

server {
    listen 80;
    server_name memora.example.com;

    # FastAPI game API - prefix routing
    location /api/v1/ {
        proxy_pass http://fastapi_backend;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;

        # Important for FastAPI to know its root path
        proxy_set_header X-Forwarded-Prefix /api/v1;
    }

    # Frappe admin and REST API
    location / {
        proxy_pass http://frappe_backend;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;

        # WebSocket support for Frappe realtime
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
    }

    # Static CDN assets (if self-hosting mock CDN)
    location /cdn/ {
        alias /var/www/memora-cdn/;
        expires 1y;
        add_header Cache-Control "public, immutable";
    }
}
```

**Confidence:** HIGH - Based on [Nginx reverse proxy documentation](https://www.getpagespeed.com/server-setup/nginx/nginx-reverse-proxy) and [FastAPI behind a proxy guide](https://fastapi.tiangolo.com/advanced/behind-a-proxy/).

### Shared Redis Configuration

**Current Frappe configuration (from common_site_config.json):**
```json
{
    "redis_cache": "redis://127.0.0.1:13000",
    "redis_queue": "redis://127.0.0.1:11000",
    "redis_socketio": "redis://127.0.0.1:13000"
}
```

**FastAPI configuration strategy:**
```python
# FastAPI settings.py
from pydantic_settings import BaseSettings

class Settings(BaseSettings):
    # Use Frappe's cache Redis for shared data
    REDIS_URL: str = "redis://127.0.0.1:13000"

    # Connection pooling for high-performance
    REDIS_MAX_CONNECTIONS: int = 50
    REDIS_DECODE_RESPONSES: bool = True

    # Key prefixes for isolation
    REDIS_PREFIX: str = "memora:"

# FastAPI Redis client initialization
import redis.asyncio as redis

async def get_redis_pool():
    return redis.ConnectionPool.from_url(
        settings.REDIS_URL,
        max_connections=settings.REDIS_MAX_CONNECTIONS,
        decode_responses=settings.REDIS_DECODE_RESPONSES,
    )
```

**Key isolation:** Use `memora:` prefix for all Memora-specific keys to avoid collision with Frappe's `frappe:` prefixed keys.

**Confidence:** HIGH - Based on [Redis connection pooling best practices](https://redis.io/docs/latest/develop/clients/pools-and-muxing/) and existing Frappe configuration.

### Cache Invalidation via Pub/Sub

**Publisher (Frappe side):**
```python
# In Frappe build worker after CDN upload
import redis

def publish_invalidation(subject_id: str, invalidation_type: str):
    r = redis.Redis.from_url(frappe.conf.redis_cache)
    message = json.dumps({
        "type": invalidation_type,  # "hierarchy", "content", "lesson"
        "subject_id": subject_id,
        "timestamp": time.time()
    })
    r.publish("memora:invalidate", message)
```

**Subscriber (FastAPI side):**
```python
# FastAPI background task on startup
import asyncio
import redis.asyncio as redis

async def cache_invalidation_listener():
    r = await redis.from_url(settings.REDIS_URL)
    pubsub = r.pubsub()
    await pubsub.subscribe("memora:invalidate")

    async for message in pubsub.listen():
        if message["type"] == "message":
            data = json.loads(message["data"])
            await invalidate_local_cache(data)

async def invalidate_local_cache(data: dict):
    """Clear in-memory caches based on invalidation message."""
    subject_id = data.get("subject_id")
    inv_type = data.get("type")

    if inv_type == "hierarchy":
        hierarchy_cache.pop(subject_id, None)
    elif inv_type == "content":
        content_cache.clear()  # More aggressive for content changes
    elif inv_type == "lesson":
        lesson_cache.pop(subject_id, None)
```

**Confidence:** MEDIUM - Based on [Redis pub/sub for cache invalidation patterns](https://www.milanjovanovic.tech/blog/solving-the-distributed-cache-invalidation-problem-with-redis-and-hybridcache). Note: Redis pub/sub is fire-and-forget; messages are lost if subscriber is down. For critical invalidations, consider Redis Streams.

---

## Patterns to Follow

### Pattern 1: Bitmap-Based Progress Tracking

**What:** Store lesson completion as bits in a Redis bitmap. Bit position = lesson's `bit_index` from the _b.json file.

**When:** Any progress read/write operation.

**Why:** O(1) completion check, minimal memory (1 bit per lesson vs. 1 row per lesson).

**Example:**
```python
# Check if lesson at bit_index 42 is complete
is_complete = await redis.getbit(f"memora:progress:{player_id}:{subject_id}", 42)

# Mark lesson complete
await redis.setbit(f"memora:progress:{player_id}:{subject_id}", 42, 1)

# Count total completions
total = await redis.bitcount(f"memora:progress:{player_id}:{subject_id}")
```

**Handling deleted lessons:** The `excluded_bits` array in _b.json lists bit positions that were deleted. When calculating progress percentage, exclude these from the total.

**Confidence:** HIGH - Based on [Redis bitmap documentation](https://redis.io/docs/latest/develop/data-types/bitmaps/).

### Pattern 2: Double-Gate Access Control

**What:** Two-phase access validation before any content access.

**When:** Every content request in FastAPI.

**Structure:**
```
Gate 1: Season Validation (Global)
    - Is the season active? (status == "active")
    - Has the season expired? (end_ts > now)
    - Fail fast: If season invalid, reject immediately

Gate 2: Player Access (Individual)
    - Does player have direct access? (SISMEMBER memora:access:{player} {subject})
    - Does player have plan access? (SISMEMBER memora:access:{player} plan:{plan_id})
    - Is content free? (is_free flag at Unit/Topic level)
```

**Example:**
```python
async def check_access(player_id: str, subject_id: str, season_id: str) -> bool:
    # Gate 1: Season check (~1ms)
    season = await redis.hgetall(f"memora:season:{season_id}")
    if season.get("status") != "active":
        return False
    if float(season.get("end_ts", 0)) < time.time():
        return False

    # Gate 2: Player access check (~1ms)
    if await redis.sismember(f"memora:access:{player_id}", subject_id):
        return True

    # Check plan membership
    plans = await redis.smembers(f"memora:access:{player_id}:plans")
    for plan_id in plans:
        if await redis.sismember(f"memora:plan:{plan_id}:subjects", subject_id):
            return True

    return False
```

**Confidence:** MEDIUM - Pattern derived from PRD requirements. Specific Redis structure may need adjustment during implementation.

### Pattern 3: Interaction Buffering with Batch Flush

**What:** Buffer interactions in Redis list, batch flush to MariaDB.

**When:** Every stage completion (high-frequency write).

**Why:** Reduces database write load from N individual INSERTs to 1 batch INSERT.

**Example:**
```python
# FastAPI: Buffer interaction
async def record_interaction(interaction: dict):
    await redis.rpush(
        "memora:buffer:interactions",
        json.dumps({
            **interaction,
            "created_at": datetime.utcnow().isoformat()
        })
    )

# Frappe: Batch flush (scheduled task)
def flush_interactions():
    r = get_redis()
    interactions = r.lrange("memora:buffer:interactions", 0, 499)  # Max 500 at a time

    if interactions:
        # Batch insert
        values = [json.loads(i) for i in interactions]
        frappe.db.bulk_insert("Memora Interaction Log", values)

        # Remove flushed items
        r.ltrim("memora:buffer:interactions", len(interactions), -1)

        frappe.db.commit()
```

**Confidence:** HIGH - Standard [Redis list pattern](https://redis.io/docs/latest/develop/data-types/lists/) for buffering.

---

## Anti-Patterns to Avoid

### Anti-Pattern 1: Direct Database Access from FastAPI

**What:** FastAPI directly queries/writes MariaDB.

**Why bad:**
- Introduces coupling to Frappe's database schema
- Bypasses Frappe's validation and hooks
- Creates dual-write consistency issues
- Violates data ownership boundaries

**Instead:** FastAPI writes to Redis only. Frappe scheduled tasks sync to MariaDB.

### Anti-Pattern 2: Synchronous Redis in Request Path

**What:** Using blocking Redis operations in async FastAPI endpoints.

**Why bad:**
- Blocks event loop
- Degrades throughput under load
- Defeats purpose of async framework

**Instead:** Use `redis.asyncio` client with connection pooling.

### Anti-Pattern 3: Per-Request Redis Connections

**What:** Creating new Redis connection for each request.

**Why bad:**
- Connection overhead (TCP handshake, auth)
- Resource exhaustion under load
- "Too many connections" errors

**Instead:** Create connection pool on startup, inject pool into routes.

### Anti-Pattern 4: Ignoring TTL on Session Data

**What:** Creating Redis keys for sessions without expiration.

**Why bad:**
- Memory leak over time
- Orphaned sessions accumulate
- No natural cleanup

**Instead:** Always set TTL on session keys:
```python
await redis.hset(f"memora:session:{session_id}", mapping=session_data)
await redis.expire(f"memora:session:{session_id}", 3600)  # 1 hour
```

### Anti-Pattern 5: Pub/Sub as Reliable Queue

**What:** Relying on Redis pub/sub for critical operations that must not be lost.

**Why bad:**
- Fire-and-forget: No message persistence
- Subscriber misses messages if disconnected
- No acknowledgment or retry

**Instead:**
- For cache invalidation: Pub/sub is acceptable (eventual consistency OK)
- For critical operations: Use Redis Streams or RQ (Redis Queue)

---

## Suggested Build Order

Based on component dependencies, build in this order:

### Phase 1: Infrastructure Foundation
**Build:**
1. FastAPI project scaffold with async Redis client
2. Shared Redis key schema (prefixes, structures)
3. Nginx reverse proxy configuration
4. JWT authentication middleware

**Why first:** All other components depend on Redis access and routing.

**Dependencies:** None (uses existing Frappe Redis)

### Phase 2: Read Path (Progress/Access)
**Build:**
1. Season meta sync (Frappe on_update -> Redis)
2. Access grant sync (Frappe on_update -> Redis)
3. Progress fetch endpoint (Redis bitmap read)
4. Access check middleware (double-gate)

**Why second:** Read path is simpler, validates integration pattern.

**Dependencies:** Phase 1 (Redis client, auth middleware)

### Phase 3: Write Path (Game Mechanics)
**Build:**
1. Session management (start/end lesson)
2. Stage completion (progress bitmap write)
3. Interaction buffering
4. Wallet updates (XP, streak)

**Why third:** Write path builds on read path, adds complexity.

**Dependencies:** Phase 2 (access check before writes)

### Phase 4: Sync Mechanisms
**Build:**
1. Dirty set tracking
2. Progress sync task (Redis -> MariaDB)
3. Wallet sync task
4. Interaction flush task
5. Sync Log DocType integration

**Why fourth:** Sync requires both read and write paths working.

**Dependencies:** Phase 3 (dirty sets populated by writes)

### Phase 5: Build Pipeline
**Build:**
1. Frappe hooks for content changes
2. Build queue management
3. JSON generation (hierarchy, bitmap, content)
4. Mock CDN upload
5. Pub/sub invalidation

**Why fifth:** Build pipeline is independent of game API but requires content structure.

**Dependencies:** Phase 1 (pub/sub), existing DocTypes

### Phase 6: Leaderboards
**Build:**
1. Leaderboard sorted sets (daily, weekly, monthly, alltime)
2. Leaderboard update on lesson complete
3. Leaderboard query endpoints

**Why last:** Leaderboards are self-contained, low priority for core functionality.

**Dependencies:** Phase 3 (wallet updates trigger leaderboard updates)

---

## Scalability Considerations

| Concern | At 1K users | At 100K users | Notes |
|---------|-------------|---------------|-------|
| Redis memory | ~10MB | ~1GB | Bitmaps: ~64KB per subject per user |
| Redis connections | 50 pooled | 500 pooled | Scale pool with traffic |
| MariaDB writes | Real-time OK | Batch required | Sync interval critical |
| CDN bandwidth | Local OK | R2 required | Swap mock for production |
| FastAPI workers | 4 | 16+ (multi-node) | Horizontal scaling |

**Confidence:** MEDIUM - Estimates based on bitmap size calculations and typical load patterns.

---

## Sources

**HIGH Confidence:**
- [Redis Bitmaps Documentation](https://redis.io/docs/latest/develop/data-types/bitmaps/)
- [Redis SETBIT Command](https://redis.io/docs/latest/commands/setbit/)
- [FastAPI Behind a Proxy](https://fastapi.tiangolo.com/advanced/behind-a-proxy/)
- [FastAPI JWT Auth Tutorial](https://fastapi.tiangolo.com/tutorial/security/oauth2-jwt/)
- [Redis Connection Pools](https://redis.io/docs/latest/develop/clients/pools-and-muxing/)
- [Frappe Background Jobs](https://docs.frappe.io/framework/user/en/api/background_jobs)

**MEDIUM Confidence:**
- [Nginx Reverse Proxy Guide](https://www.getpagespeed.com/server-setup/nginx/nginx-reverse-proxy)
- [Redis Pub/Sub Cache Invalidation](https://www.milanjovanovic.tech/blog/solving-the-distributed-cache-invalidation-problem-with-redis-and-hybridcache)
- [FastAPI Production Deployment 2026](https://blog.greeden.me/en/2026/01/20/complete-guide-to-deploying-fastapi-in-production-reliable-operations-with-uvicorn-multi-workers-docker-and-a-reverse-proxy/)
- [Frappe Docker Configuration](https://github.com/frappe/frappe_docker/blob/main/docs/getting-started.md)
- [FastAPI Redis Connection Pooling](https://hoop.dev/blog/the-simplest-way-to-make-fastapi-redis-work-like-it-should/)

**LOW Confidence (needs validation during implementation):**
- Double-gate access control specific Redis structure
- Exact sync interval tuning for 100K users
- Memory estimates for bitmap storage at scale

---

*Architecture research completed: 2026-02-01*
