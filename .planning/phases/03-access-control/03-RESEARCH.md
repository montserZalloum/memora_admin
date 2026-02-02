# Phase 03: Access Control - Research

**Researched:** 2026-02-02
**Domain:** Double-Gate access control pattern (season validation + player grants), Redis-based access sets, Frappe doc_events hooks for real-time sync
**Confidence:** HIGH

## Summary

This research covers implementing a Double-Gate access control pattern for content access validation in the Memora platform. Gate 1 validates season status (active + not expired) via Redis hash lookup. Gate 2 validates player grants (direct grants + plan membership) via Redis set membership check. Free preview content (is_free=true at Unit/Topic level) bypasses Gate 2 entirely. The pattern uses FastAPI dependencies for enforcement, Redis sets for O(1) membership checks, and Frappe doc_events hooks for immediate grant propagation.

The standard approach uses FastAPI's dependency injection over middleware for access control (more flexible, easier to test, better route-level granularity). Redis sets (SADD/SISMEMBER) provide O(1) access checks which is critical for sub-second response times. Season metadata is stored in Redis hashes (HSET/HGETALL) for atomic multi-field updates. Frappe's doc_events hooks (on_update, after_insert) trigger Redis updates synchronously during document save, achieving sub-second propagation per CONTEXT.md requirements.

**Primary recommendation:** Use FastAPI dependencies for Double-Gate enforcement (not middleware), Redis sets for player access (SISMEMBER for O(1) checks), Redis hashes for season metadata, and Frappe doc_events hooks with direct Redis writes for immediate grant propagation.

## Standard Stack

The established libraries/tools for this domain:

### Core
| Library | Version | Purpose | Why Standard |
|---------|---------|---------|--------------|
| redis-py | 5.0+ | Access sets, season cache | Already in project, async support, SADD/SISMEMBER O(1) |
| FastAPI | 0.115+ | Dependency injection for gates | Route-level dependencies > middleware for auth patterns |
| frappe | 15+ | doc_events hooks, Redis wrapper | Native doc_events system, built-in frappe.cache() |

### Supporting
| Library | Version | Purpose | When to Use |
|---------|---------|---------|-------------|
| pydantic | 2.0+ | Request/response models | Grant request validation, webhook payloads |
| structlog | 24.0+ | Access control logging | Gate rejection logging, webhook audit trail |

### Alternatives Considered
| Instead of | Could Use | Tradeoff |
|------------|-----------|----------|
| Redis sets | Redis sorted sets | Sorted sets add scoring overhead; sets are O(1) for membership |
| FastAPI deps | Starlette middleware | Middleware lacks route-level flexibility, harder to test |
| frappe.cache | Direct redis-py | frappe.cache handles key prefixing automatically in Frappe context |

**Installation:**
No new dependencies required - all libraries already in project from Phase 1 and 2.

## Architecture Patterns

### Recommended Project Structure
```
fastapi_app/
├── api/
│   ├── deps.py                  # Add access control dependencies
│   └── v1/
│       └── endpoints/
│           ├── access.py        # Admin grant endpoints
│           └── webhooks.py      # Payment webhook endpoint
├── services/
│   ├── access.py                # AccessService (gate checks)
│   └── season.py                # SeasonService (metadata cache)
└── models/
    └── access.py                # Grant models, webhook payloads

memora_admin/memora_admin/
├── hooks.py                     # Add doc_events configuration
└── events/
    └── access_sync.py           # Grant sync handlers
```

### Pattern 1: Double-Gate via FastAPI Dependencies
**What:** Implement Gate 1 (season) and Gate 2 (grants) as composable FastAPI dependencies
**When to use:** All content access endpoints

```python
# Source: FastAPI dependency injection patterns + CONTEXT.md decisions
from typing import Annotated
from fastapi import Depends, HTTPException, status

async def gate_1_season_check(
    season_id: str,
    access_service: AccessServiceDep,
) -> SeasonMeta:
    """
    Gate 1: Validate season is active and not expired.
    Raises 403 if season fails validation.
    """
    season = await access_service.get_season_meta(season_id)

    if not season:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"code": "SEASON_NOT_FOUND", "message": "Season not available"},
        )

    if not season.is_published:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"code": "SEASON_INACTIVE", "message": "Season is not active"},
        )

    if season.is_expired:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"code": "SEASON_EXPIRED", "message": "Season has ended"},
        )

    return season


async def gate_2_grant_check(
    user: CurrentUser,
    content_key: str,  # e.g., "SUB-MATH" or "TRK-MATH-01"
    access_service: AccessServiceDep,
) -> bool:
    """
    Gate 2: Validate player has access (direct grant OR plan membership).
    Raises 403 if player lacks access.
    """
    has_access = await access_service.check_player_access(
        player_id=user.sub,
        content_key=content_key,
    )

    if not has_access:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"code": "NO_ACCESS", "message": "Content access required"},
        )

    return True


async def gate_free_or_grant(
    user: CurrentUser,
    content: ContentItem,  # Has is_free flag and access_key
    access_service: AccessServiceDep,
) -> bool:
    """
    Combined gate: Skip Gate 2 if content is free, otherwise check grant.
    Per CONTEXT.md: free content is identical to paid (full XP, no hints).
    """
    if content.is_free:
        return True  # Free preview - bypass Gate 2

    return await gate_2_grant_check(user, content.access_key, access_service)
```

### Pattern 2: Redis Set for Player Access
**What:** Store player access grants as Redis set members for O(1) lookup
**When to use:** Grant storage and validation

```python
# Source: Redis sets docs + CONTEXT.md decisions
# Key pattern: access:{player_id} -> set of access keys

class AccessService:
    """Manages player access grants via Redis sets."""

    def __init__(self, redis: redis.Redis, key_prefix: str = "memora:"):
        self.redis = redis
        self.prefix = key_prefix

    def _access_key(self, player_id: str) -> str:
        """Generate Redis key for player's access set."""
        return f"{self.prefix}access:{player_id}"

    async def check_player_access(
        self,
        player_id: str,
        content_key: str,
    ) -> bool:
        """
        Check if player has access to content.
        O(1) complexity via SISMEMBER.

        Args:
            player_id: Player's user ID
            content_key: Access key (e.g., "SUB-MATH", "TRK-MATH-01")

        Returns:
            True if player has direct grant for this content
        """
        key = self._access_key(player_id)
        return await self.redis.sismember(key, content_key)

    async def grant_access(
        self,
        player_id: str,
        content_keys: list[str],
    ) -> int:
        """
        Grant access to content.
        Idempotent - re-granting same key is safe (SADD ignores duplicates).

        Returns:
            Number of NEW grants added (0 if all existed)
        """
        key = self._access_key(player_id)
        if not content_keys:
            return 0
        return await self.redis.sadd(key, *content_keys)

    async def revoke_access(
        self,
        player_id: str,
        content_keys: list[str],
    ) -> int:
        """
        Revoke access to content.

        Returns:
            Number of grants removed
        """
        key = self._access_key(player_id)
        if not content_keys:
            return 0
        return await self.redis.srem(key, *content_keys)

    async def get_player_grants(self, player_id: str) -> set[str]:
        """
        Get all content keys player has access to.
        O(N) - use sparingly, prefer SISMEMBER for checks.
        """
        key = self._access_key(player_id)
        return await self.redis.smembers(key)
```

### Pattern 3: Redis Hash for Season Metadata
**What:** Cache season metadata in Redis hash for fast Gate 1 checks
**When to use:** Season status validation

```python
# Source: Redis hashes docs + CONTEXT.md decisions
# Key pattern: season:{season_id} -> hash {is_published, start_date, end_date}

from datetime import date
from pydantic import BaseModel

class SeasonMeta(BaseModel):
    """Season metadata for Gate 1 validation."""
    season_id: str
    is_published: bool
    start_date: date
    end_date: date

    @property
    def is_expired(self) -> bool:
        """Check if season has ended."""
        return date.today() > self.end_date

    @property
    def is_started(self) -> bool:
        """Check if season has started."""
        return date.today() >= self.start_date

class SeasonService:
    """Manages season metadata cache via Redis hashes."""

    def __init__(self, redis: redis.Redis, key_prefix: str = "memora:"):
        self.redis = redis
        self.prefix = key_prefix

    def _season_key(self, season_id: str) -> str:
        """Generate Redis key for season metadata."""
        return f"{self.prefix}season:{season_id}"

    async def get_season_meta(self, season_id: str) -> SeasonMeta | None:
        """
        Get season metadata from cache.
        Returns None if season not cached (fallback to MariaDB needed).
        """
        key = self._season_key(season_id)
        data = await self.redis.hgetall(key)

        if not data:
            return None

        return SeasonMeta(
            season_id=season_id,
            is_published=data.get("is_published") == "1",
            start_date=date.fromisoformat(data["start_date"]),
            end_date=date.fromisoformat(data["end_date"]),
        )

    async def set_season_meta(self, season: SeasonMeta) -> None:
        """
        Cache season metadata.
        Called from Frappe doc_events hook on season save.
        """
        key = self._season_key(season.season_id)
        await self.redis.hset(key, mapping={
            "is_published": "1" if season.is_published else "0",
            "start_date": season.start_date.isoformat(),
            "end_date": season.end_date.isoformat(),
        })

    async def delete_season_meta(self, season_id: str) -> None:
        """Remove season from cache (on delete)."""
        key = self._season_key(season_id)
        await self.redis.delete(key)
```

### Pattern 4: Frappe doc_events for Immediate Sync
**What:** Use Frappe's doc_events hooks to sync grants to Redis on document save
**When to use:** Admin grants from Frappe Desk

```python
# hooks.py - add to existing configuration
doc_events = {
    "Memora Player Subscription": {
        "after_insert": "memora_admin.events.access_sync.on_subscription_created",
        "on_update": "memora_admin.events.access_sync.on_subscription_updated",
        "on_trash": "memora_admin.events.access_sync.on_subscription_deleted",
    },
    "Memora Season": {
        "on_update": "memora_admin.events.access_sync.on_season_updated",
        "on_trash": "memora_admin.events.access_sync.on_season_deleted",
    },
}
```

```python
# memora_admin/events/access_sync.py
# Source: Frappe doc_events docs + CONTEXT.md decisions
import frappe

def on_subscription_created(doc, method):
    """
    Sync new subscription grant to Redis.
    Called after Memora Player Subscription is inserted.

    Per CONTEXT.md: immediate sync, sub-second propagation.
    """
    if not doc.is_active:
        return

    _sync_grant_to_redis(doc.player, doc.access_key, add=True)

def on_subscription_updated(doc, method):
    """
    Handle subscription status changes.
    Add grant if activated, remove if deactivated.
    """
    # Check if is_active changed
    old_active = doc.get_doc_before_save()
    old_is_active = old_active.is_active if old_active else False

    if doc.is_active and not old_is_active:
        # Activated - add grant
        _sync_grant_to_redis(doc.player, doc.access_key, add=True)
    elif not doc.is_active and old_is_active:
        # Deactivated - remove grant
        _sync_grant_to_redis(doc.player, doc.access_key, add=False)

def on_subscription_deleted(doc, method):
    """Remove grant when subscription is deleted."""
    _sync_grant_to_redis(doc.player, doc.access_key, add=False)

def on_season_updated(doc, method):
    """
    Sync season metadata to Redis on update.
    Per CONTEXT.md: immediate propagation for Gate 1.
    """
    cache = frappe.cache
    key = f"memora:season:{doc.name}"

    cache.hset(key, "is_published", "1" if doc.is_published else "0")
    cache.hset(key, "start_date", str(doc.start_date))
    cache.hset(key, "end_date", str(doc.end_date))

def on_season_deleted(doc, method):
    """Remove season from Redis cache."""
    cache = frappe.cache
    key = f"memora:season:{doc.name}"
    cache.delete_value(key)

def _sync_grant_to_redis(player_id: str, access_key: str, add: bool = True):
    """
    Sync individual grant to Redis.
    Uses frappe.cache for Frappe-context Redis access.
    """
    cache = frappe.cache
    key = f"memora:access:{player_id}"

    if add:
        cache.sadd(key, access_key)
    else:
        cache.srem(key, access_key)
```

### Pattern 5: Payment Webhook with Idempotency
**What:** Provider-agnostic webhook that creates subscription and grants access
**When to use:** Payment gateway integration

```python
# Source: Webhook best practices research + CONTEXT.md decisions
from pydantic import BaseModel
from fastapi import APIRouter, HTTPException, status, BackgroundTasks
import structlog

logger = structlog.get_logger()

class WebhookPayload(BaseModel):
    """Provider-agnostic webhook payload."""
    event_id: str           # Unique event ID for idempotency
    event_type: str         # e.g., "payment.completed"
    transaction_id: str     # Payment provider's transaction ID
    player_id: str          # Memora player ID
    product_grant_id: str   # Memora Product Grant DocType name
    amount: float
    currency: str
    timestamp: str

@router.post("/webhooks/payment")
async def payment_webhook(
    payload: WebhookPayload,
    redis: RedisClient,
    background_tasks: BackgroundTasks,
):
    """
    Handle payment completion webhook.

    Per CONTEXT.md:
    - Provider-agnostic interface
    - Idempotent via upsert (SADD ignores duplicates)
    - Transaction log for failure recovery
    """
    # Fast acknowledgment - queue processing
    logger.info(
        "webhook_received",
        event_id=payload.event_id,
        transaction_id=payload.transaction_id,
    )

    # Check idempotency (optional - SADD is already idempotent)
    idempotency_key = f"memora:webhook:{payload.event_id}"
    if await redis.exists(idempotency_key):
        return {"status": "already_processed"}

    # Process in background for fast acknowledgment
    background_tasks.add_task(
        process_payment_webhook,
        payload=payload,
        redis=redis,
    )

    # Mark as processing
    await redis.set(idempotency_key, "processing", ex=86400)  # 24hr TTL

    return {"status": "accepted"}

async def process_payment_webhook(payload: WebhookPayload, redis):
    """
    Background processing of payment webhook.
    Creates MariaDB subscription and Redis grant.
    """
    try:
        # 1. Get grant components from Product Grant
        grant_keys = await get_grant_components(payload.product_grant_id)

        # 2. Create MariaDB subscription record (via Frappe API)
        await create_subscription_record(payload)

        # 3. Add grants to Redis (idempotent via SADD)
        access_key = f"memora:access:{payload.player_id}"
        if grant_keys:
            await redis.sadd(access_key, *grant_keys)

        # 4. Mark as completed
        idempotency_key = f"memora:webhook:{payload.event_id}"
        await redis.set(idempotency_key, "completed", ex=86400)

        logger.info(
            "webhook_processed",
            event_id=payload.event_id,
            grants=grant_keys,
        )

    except Exception as e:
        logger.error(
            "webhook_failed",
            event_id=payload.event_id,
            error=str(e),
        )
        # Queue for retry (per CONTEXT.md: background job retries failed writes)
        await queue_for_retry(payload)
```

### Pattern 6: Retry Queue for Failed Redis Writes
**What:** Simple Redis list-based retry queue for webhook failures
**When to use:** Transaction log recovery per CONTEXT.md

```python
# Source: Redis list patterns + CONTEXT.md retry requirement
import json

RETRY_QUEUE_KEY = "memora:webhook:retry_queue"

async def queue_for_retry(payload: WebhookPayload, redis):
    """Add failed webhook to retry queue."""
    await redis.lpush(RETRY_QUEUE_KEY, json.dumps(payload.model_dump()))

async def process_retry_queue(redis, max_items: int = 10):
    """
    Process items from retry queue.
    Called by Frappe scheduled task.
    """
    for _ in range(max_items):
        item = await redis.rpop(RETRY_QUEUE_KEY)
        if not item:
            break

        try:
            payload = WebhookPayload(**json.loads(item))
            await process_payment_webhook(payload, redis)
        except Exception as e:
            # Re-queue on failure
            await redis.lpush(RETRY_QUEUE_KEY, item)
            logger.error("retry_failed", error=str(e))
            break  # Stop on failure to prevent infinite loop
```

### Anti-Patterns to Avoid
- **Middleware for access control:** Loses route-level flexibility; use FastAPI dependencies instead
- **SMEMBERS for access check:** O(N) operation; use SISMEMBER for O(1) membership check
- **Separate MariaDB + Redis writes:** Creates inconsistency window; write MariaDB first, then Redis
- **Blocking Redis calls in Frappe hooks:** Use frappe.cache (sync wrapper that matches Frappe's model)
- **Processing webhook synchronously:** Slow external calls can timeout; acknowledge fast, process async
- **Hardcoded access keys:** Use constants or enums for access key patterns (SUB-*, TRK-*)

## Don't Hand-Roll

Problems that look simple but have existing solutions:

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| Set membership check | List iteration | Redis SISMEMBER | O(1) vs O(N), handles millions of members |
| Multi-field cache | Multiple SET calls | Redis HSET/HGETALL | Atomic updates, single round trip |
| Idempotent grants | Check-then-add logic | Redis SADD | Atomic, ignores duplicates automatically |
| Doc change sync | Polling MariaDB | Frappe doc_events | Real-time, no polling overhead |
| Retry queue | Custom queue table | Redis list LPUSH/RPOP | Built-in, no schema needed |

**Key insight:** Redis data structures are purpose-built for access control patterns. Sets provide O(1) membership, hashes provide atomic multi-field updates, and lists provide reliable queuing. Don't replicate these with custom code.

## Common Pitfalls

### Pitfall 1: Race Condition Between MariaDB and Redis
**What goes wrong:** Subscription saved to MariaDB but Redis write fails; player has access in DB but not in cache
**Why it happens:** Separate write operations without transaction coordination
**How to avoid:** Write MariaDB first (source of truth), then Redis; on Redis failure, queue for retry
**Warning signs:** Player has subscription record but can't access content

### Pitfall 2: Checking Access with SMEMBERS
**What goes wrong:** Endpoint becomes slow with many grants per player
**Why it happens:** SMEMBERS returns ALL members (O(N)) instead of checking one
**How to avoid:** Always use SISMEMBER for access checks (O(1))
**Warning signs:** Access check latency increases with player's grant count

### Pitfall 3: Free Content Check After Grant Check
**What goes wrong:** Free content rejected for non-subscribers
**Why it happens:** Gate 2 runs before checking is_free flag
**How to avoid:** Check is_free FIRST; only proceed to Gate 2 if not free
**Warning signs:** Free preview content returns 403

### Pitfall 4: Stale Season Cache
**What goes wrong:** Season ends but content still accessible
**Why it happens:** Season metadata not updated in Redis when doc updated
**How to avoid:** doc_events hook on Memora Season on_update syncs to Redis
**Warning signs:** Content accessible after season end_date

### Pitfall 5: Blocking Frappe Event Loop
**What goes wrong:** Desk UI freezes when saving subscription
**Why it happens:** Expensive Redis operations in synchronous doc_events hook
**How to avoid:** Use frappe.cache (optimized for Frappe); operations are fast O(1)
**Warning signs:** Slow document save times in Frappe Desk

### Pitfall 6: Webhook Duplicate Processing
**What goes wrong:** Same payment creates multiple subscriptions or duplicate grants
**Why it happens:** Payment provider retries webhook, no idempotency check
**How to avoid:** Track event_id in Redis; SADD naturally ignores duplicates
**Warning signs:** Duplicate subscription records in MariaDB

## Code Examples

Verified patterns from official sources:

### Complete Access Check Dependency
```python
# Source: FastAPI dependency patterns + research findings
from typing import Annotated
from fastapi import Depends, HTTPException, status, Request
import redis.asyncio as redis

class AccessService:
    """Complete access control service."""

    def __init__(self, redis: redis.Redis, key_prefix: str = "memora:"):
        self.redis = redis
        self.prefix = key_prefix

    async def check_season(self, season_id: str) -> bool:
        """Gate 1: Check season is active and not expired."""
        key = f"{self.prefix}season:{season_id}"
        data = await self.redis.hgetall(key)

        if not data:
            return False

        if data.get("is_published") != "1":
            return False

        from datetime import date
        end_date = date.fromisoformat(data["end_date"])
        if date.today() > end_date:
            return False

        return True

    async def check_access(self, player_id: str, content_key: str) -> bool:
        """Gate 2: Check player has grant for content."""
        key = f"{self.prefix}access:{player_id}"
        return await self.redis.sismember(key, content_key)

async def get_access_service(request: Request) -> AccessService:
    """Dependency to get AccessService with Redis from app state."""
    redis_client = redis.Redis(connection_pool=request.app.state.redis_pool)
    return AccessService(redis_client)

AccessServiceDep = Annotated[AccessService, Depends(get_access_service)]
```

### Admin Grant Endpoint
```python
# Source: CONTEXT.md requirements + FastAPI patterns
from pydantic import BaseModel
from fastapi import APIRouter

router = APIRouter(prefix="/admin", tags=["admin"])

class GrantRequest(BaseModel):
    player_id: str
    content_keys: list[str]  # e.g., ["SUB-MATH", "TRK-MATH-01"]

class GrantResponse(BaseModel):
    granted: int  # Number of new grants added
    message: str

@router.post("/grants", response_model=GrantResponse)
async def create_grant(
    request: GrantRequest,
    user: CurrentUser,
    access_service: AccessServiceDep,
):
    """
    Admin endpoint to grant player access.
    Per CONTEXT.md: grants are additive and permanent until revoked.

    Requires admin role.
    """
    if user.role != "admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"code": "ADMIN_REQUIRED", "message": "Admin role required"},
        )

    granted = await access_service.grant_access(
        player_id=request.player_id,
        content_keys=request.content_keys,
    )

    return GrantResponse(
        granted=granted,
        message=f"Granted {granted} new access keys",
    )
```

### Frappe Hook Implementation
```python
# memora_admin/events/access_sync.py
# Source: Frappe doc_events research + CONTEXT.md requirements
import frappe

def on_subscription_change(doc, method):
    """
    Unified handler for subscription changes.
    Syncs grant state to Redis based on is_active flag.
    """
    player_id = doc.player
    access_key = doc.access_key

    # Get player's user_id from Player Profile
    player_doc = frappe.get_doc("Memora Player Profile", player_id)
    user_id = player_doc.user

    if not user_id:
        frappe.log_error(
            f"No user linked to player {player_id}",
            "Access Sync Error"
        )
        return

    cache = frappe.cache
    redis_key = f"memora:access:{user_id}"

    if doc.is_active:
        cache.sadd(redis_key, access_key)
        frappe.logger().info(f"Granted {access_key} to {user_id}")
    else:
        cache.srem(redis_key, access_key)
        frappe.logger().info(f"Revoked {access_key} from {user_id}")

# Hook configuration in hooks.py
doc_events = {
    "Memora Player Subscription": {
        "after_insert": "memora_admin.events.access_sync.on_subscription_change",
        "on_update": "memora_admin.events.access_sync.on_subscription_change",
        "on_trash": "memora_admin.events.access_sync.on_subscription_deleted",
    },
}

def on_subscription_deleted(doc, method):
    """Remove grant when subscription is deleted."""
    player_doc = frappe.get_doc("Memora Player Profile", doc.player)
    user_id = player_doc.user

    if user_id:
        cache = frappe.cache
        redis_key = f"memora:access:{user_id}"
        cache.srem(redis_key, doc.access_key)
```

## State of the Art

| Old Approach | Current Approach | When Changed | Impact |
|--------------|------------------|--------------|--------|
| Middleware auth | Dependency injection | FastAPI 0.100+ | Route-level control, better testing |
| Database lookup per request | Redis set membership | Standard practice | O(1) vs O(N) database query |
| Polling for changes | doc_events hooks | Frappe native | Real-time sync, no polling overhead |
| Manual idempotency checks | SADD natural idempotency | Redis design | Simpler code, atomic operation |

**Deprecated/outdated:**
- `@app.on_event("startup")`: Use lifespan context manager
- `frappe.cache()` function call: Use `frappe.cache` property directly in Frappe 15+
- Separate check-then-add logic: Use SADD's built-in duplicate handling

## Open Questions

Things that couldn't be fully resolved:

1. **Webhook Location Decision**
   - What we know: CONTEXT.md marks as Claude's discretion
   - Options: FastAPI endpoint (cleaner async, already has Redis) vs Frappe whitelisted method (native doc creation)
   - Recommendation: FastAPI endpoint - cleaner async handling, direct Redis access, better error handling
   - Rationale: Webhook processing is latency-sensitive; FastAPI's async model fits better

2. **Redis Key Expiration for Access Sets**
   - What we know: CONTEXT.md says grants are permanent until revoked
   - What's unclear: Should we set TTL on access sets for cleanup?
   - Recommendation: No TTL on access sets; grants persist until explicit SREM
   - Rationale: Permanent grants per CONTEXT.md; explicit revocation is cleaner

3. **Plan Membership Expansion**
   - What we know: Access valid if direct grant OR plan membership
   - What's unclear: When to expand plan membership to individual grants vs check at runtime
   - Recommendation: Expand on payment/subscription creation; store all granted keys in player's set
   - Rationale: O(1) lookup is critical; expansion cost is one-time at grant time

4. **Retry Queue Processing Trigger**
   - What we know: CONTEXT.md mentions background job for retry
   - Options: Frappe scheduler_events (cron-like) vs RQ job vs FastAPI BackgroundTasks
   - Recommendation: Frappe scheduler_events with "all" frequency (runs on each scheduler tick)
   - Rationale: Integrates with existing Frappe scheduler, no new infrastructure

## Sources

### Primary (HIGH confidence)
- [Redis Sets Documentation](https://redis.io/docs/latest/develop/data-types/sets/) - SADD, SISMEMBER, SMEMBERS operations and complexity
- [Redis Hashes Documentation](https://redis.io/docs/latest/develop/data-types/hashes/) - HSET, HGETALL for season metadata
- [FastAPI Dependencies](https://fastapi.tiangolo.com/tutorial/dependencies/) - Dependency injection patterns
- [Frappe doc_events Documentation](https://support.aakvatech.com/wiki/document-event-hooks-in-frappe) - Hook configuration and events

### Secondary (MEDIUM confidence)
- [FastAPI Auth with Dependency Injection](https://www.propelauth.com/post/fastapi-auth-with-dependency-injection) - Why dependencies > middleware for auth
- [Frappe Caching Guide](https://docs.frappe.io/framework/user/en/guides/caching) - frappe.cache operations including sets
- [Frappe Redis Wrapper Source](https://github.com/frappe/frappe/blob/develop/frappe/utils/redis_wrapper.py) - sadd, srem, sismember method signatures

### Tertiary (LOW confidence)
- [Webhook Idempotency Best Practices](https://hookdeck.com/webhooks/guides/implement-webhook-idempotency) - Event ID tracking pattern
- [Payment Webhook Handling](https://medium.com/@sohail_saifii/handling-payment-webhooks-reliably-idempotency-retries-validation-69b762720bf5) - Queue-first processing pattern

## Metadata

**Confidence breakdown:**
- Standard Stack: HIGH - Redis operations verified via official docs
- Architecture: HIGH - FastAPI dependency patterns from official tutorials
- Gate Pattern: HIGH - Redis set membership is O(1) per official docs
- Frappe Hooks: HIGH - doc_events verified via Frappe documentation
- Webhook Pattern: MEDIUM - Best practices from multiple community sources
- Retry Queue: LOW - Implementation pattern is standard but not officially documented

**Research date:** 2026-02-02
**Valid until:** 2026-03-02 (30 days - stable patterns, well-established Redis/FastAPI stack)
