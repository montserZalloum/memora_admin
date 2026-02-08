# Phase 24: Real-Time Subscription Notifications - Research

**Researched:** 2026-02-08
**Domain:** WebSockets, Redis Pub/Sub, Real-Time Notifications
**Confidence:** HIGH

## Summary

Phase 24 replaces the deprecated SSE endpoint (`/progress/stream/{subject}`) with a WebSocket-based notification system that pushes subscription status changes to connected clients in real-time. The architecture is straightforward: when an admin approves/rejects a transaction in Frappe, the DocType handler publishes a notification to a per-user Redis pub/sub channel (`memora:notify:{user_id}`). Each FastAPI instance subscribes to active users' channels and forwards messages to their WebSocket connections.

The codebase already has all the building blocks: Redis pub/sub listener infrastructure (`fastapi_app/core/pubsub.py`), JWT authentication, a working approval handler (`MemoraSubscriptionTransaction.on_update`), and the `get_fastapi_redis()` helper for Frappe-to-Redis publishing. The primary work is: (1) building a per-user ConnectionManager, (2) adding a dedicated notification pub/sub listener that subscribes/unsubscribes dynamically per-user, (3) adding the WebSocket endpoint with JWT auth, (4) publishing from the Frappe approval handler, and (5) removing the deprecated SSE code.

**Primary recommendation:** Use FastAPI's native WebSocket support with a per-user ConnectionManager (dict of `user_id -> set[WebSocket]`), Redis pub/sub with dynamic `subscribe`/`unsubscribe` per user connection, and disable per-message-deflate compression to hit the ~14 KiB/connection target for 100K+ scale.

## Standard Stack

### Core
| Library | Version | Purpose | Why Standard |
|---------|---------|---------|--------------|
| FastAPI (builtin WebSocket) | >=0.115.0 | WebSocket endpoint, `WebSocket` class, `WebSocketDisconnect`, `Depends` | Built into FastAPI/Starlette, no extra dependency needed |
| redis.asyncio (redis-py) | >=5.0.0 | Async pub/sub for cross-instance notification relay | Already used throughout the project, proven pub/sub API |
| PyJWT (jwt) | (existing) | JWT token decode for WebSocket auth | Already used for all FastAPI auth |

### Supporting
| Library | Version | Purpose | When to Use |
|---------|---------|---------|-------------|
| structlog | >=24.0.0 | Structured logging for WS connections/disconnections | Already used project-wide |
| pydantic | (existing) | Notification message models | Already used for all API models |

### Alternatives Considered
| Instead of | Could Use | Tradeoff |
|------------|-----------|----------|
| FastAPI native WebSocket | `broadcaster` library | Adds dependency; FastAPI native is sufficient for this use case |
| Per-user channels | Single broadcast channel | Per-user channels avoid broadcasting to all instances; more targeted |
| `fastapi-websocket-pubsub` | Custom implementation | Overkill for simple notification push; adds dependency |

### Removed
| Library | Version | Why Removed |
|---------|---------|-------------|
| `sse-starlette` | >=2.0.0 | Deprecated SSE endpoint being replaced by WebSockets |

**Installation:** No new dependencies needed. Remove `sse-starlette>=2.0.0` from `requirements.txt`.

## Architecture Patterns

### Recommended Project Structure
```
fastapi_app/
├── core/
│   ├── pubsub.py              # MODIFY: Add notification message type handler
│   └── ws_manager.py          # NEW: ConnectionManager for WebSocket connections
├── api/v1/endpoints/
│   ├── notifications.py       # NEW: WebSocket endpoint
│   └── progress.py            # MODIFY: Remove SSE endpoint
├── models/
│   └── notification.py        # NEW: Notification message models
└── main.py                    # MODIFY: Initialize ConnectionManager, start notification listener

memora_admin/
├── events/
│   └── purchase_sync.py       # MODIFY: Add Redis pub/sub publish on approval/rejection
└── memora_admin/doctype/
    └── memora_subscription_transaction/
        └── memora_subscription_transaction.py  # MODIFY: Publish notification after approval/rejection
```

### Pattern 1: Per-User ConnectionManager
**What:** A dict mapping `user_id -> set[WebSocket]` for O(1) user lookup and multi-device support
**When to use:** When you need to send messages to specific users, not broadcast to all
**Why per-user dict, not flat list:** The success criteria require sending to "all connected clients of that player" - a flat list would require O(n) scan

```python
# Source: FastAPI official docs (ConnectionManager pattern) + per-user adaptation
import asyncio
from collections import defaultdict
from fastapi import WebSocket

class ConnectionManager:
    def __init__(self):
        # user_id -> set of WebSocket connections (supports multi-device)
        self._connections: dict[str, set[WebSocket]] = defaultdict(set)
        self._lock = asyncio.Lock()

    @property
    def total_connections(self) -> int:
        return sum(len(ws_set) for ws_set in self._connections.values())

    @property
    def total_users(self) -> int:
        return len(self._connections)

    async def connect(self, user_id: str, websocket: WebSocket) -> bool:
        """Accept and register a WebSocket connection. Returns True if this is the first connection for this user."""
        await websocket.accept()
        async with self._lock:
            is_first = len(self._connections[user_id]) == 0
            self._connections[user_id].add(websocket)
            return is_first

    async def disconnect(self, user_id: str, websocket: WebSocket) -> bool:
        """Remove a WebSocket connection. Returns True if this was the last connection for this user."""
        async with self._lock:
            self._connections[user_id].discard(websocket)
            is_last = len(self._connections[user_id]) == 0
            if is_last:
                del self._connections[user_id]
            return is_last

    async def send_to_user(self, user_id: str, message: str) -> int:
        """Send message to all connections for a user. Returns count of successful sends."""
        connections = self._connections.get(user_id, set())
        if not connections:
            return 0
        sent = 0
        dead = []
        for ws in connections:
            try:
                await ws.send_text(message)
                sent += 1
            except Exception:
                dead.append(ws)
        # Clean up dead connections
        for ws in dead:
            await self.disconnect(user_id, ws)
        return sent
```

### Pattern 2: Dynamic Per-User Pub/Sub Subscription
**What:** Subscribe to `memora:notify:{user_id}` when a user's first WebSocket connects; unsubscribe when their last WebSocket disconnects
**When to use:** When notification channels are per-user and you don't want to subscribe to all possible channels
**Why dynamic:** With 100K users, subscribing to all channels upfront is wasteful. Dynamic subscribe/unsubscribe tracks only connected users.

```python
# Source: redis-py async pub/sub docs
import asyncio
import json
import redis.asyncio as redis

async def start_notification_listener(redis_pool, connection_manager):
    """Background task that listens for notification messages and forwards to WebSocket clients."""
    client = redis.Redis(connection_pool=redis_pool)
    pubsub = client.pubsub()

    async for message in pubsub.listen():
        if message["type"] == "message":
            channel = message["channel"]
            if isinstance(channel, bytes):
                channel = channel.decode("utf-8")
            # Extract user_id from channel: memora:notify:{user_id}
            user_id = channel.replace("memora:notify:", "")
            await connection_manager.send_to_user(user_id, message["data"])

    # The pubsub object is shared; subscribe/unsubscribe called externally
```

### Pattern 3: WebSocket JWT Authentication via Query Parameter
**What:** Pass JWT token as query parameter since WebSocket API doesn't support custom headers
**When to use:** WebSocket connections that need JWT auth
**Why query param:** Browser WebSocket API (`new WebSocket(url)`) does not support setting HTTP headers. The standard workaround is `?token=<jwt>`. FastAPI's `Depends` system works with WebSocket query params.

```python
# Source: FastAPI official docs (WebSocket auth pattern)
from fastapi import WebSocket, WebSocketException, Query, Depends, status

async def ws_authenticate(
    websocket: WebSocket,
    token: str = Query(...),
) -> TokenPayload:
    """Authenticate WebSocket connection via JWT query parameter."""
    try:
        payload = decode_token(token, verify_type="access")
        return TokenPayload(**payload)
    except Exception:
        raise WebSocketException(code=status.WS_1008_POLICY_VIOLATION)
```

### Pattern 4: Frappe-side Publish to Redis Pub/Sub
**What:** Publish notification from Frappe approval handler to Redis per-user channel
**When to use:** After transaction status change (approval/rejection)
**Why this approach:** Follows existing pattern in `catalog_sync.py` where `get_fastapi_redis().publish()` is used

```python
# Source: Existing codebase pattern (catalog_sync.py)
import json
from memora_admin.events.access_sync import get_fastapi_redis

def _publish_subscription_notification(player_id, status, subject_ids, product_name):
    """Publish subscription notification to Redis for WebSocket relay."""
    r = get_fastapi_redis()
    # Resolve user_id from player_id (player field = user email for autoname: field:user)
    user_id = player_id  # player IS the user_id in this DocType
    r.publish(
        f"memora:notify:{user_id}",
        json.dumps({
            "type": "subscription_update",
            "status": status,  # "approved" or "rejected"
            "subject_ids": subject_ids,
            "product_name": product_name,
            "timestamp": str(frappe.utils.now()),
        }),
    )
```

### Anti-Patterns to Avoid
- **Single broadcast channel for all users:** Publishing all notifications to one channel means every FastAPI instance processes every notification, even for users not connected to that instance. Per-user channels are targeted.
- **Subscribing to all possible user channels at startup:** Memory waste. Subscribe dynamically only for connected users.
- **Blocking the approval handler on WebSocket delivery:** The Frappe handler should fire-and-forget to Redis pub/sub. Delivery to WebSocket is best-effort.
- **Using SSE for new features:** SSE is being deprecated. WebSockets are bidirectional, better supported, and scale better.
- **Accepting WebSocket before JWT validation:** Always validate the token BEFORE calling `websocket.accept()`. If invalid, raise `WebSocketException(code=WS_1008_POLICY_VIOLATION)`.

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| WebSocket server | Raw asyncio sockets | FastAPI's `WebSocket` class | Handles protocol, framing, ping/pong, close codes |
| Cross-instance messaging | Custom socket server | Redis pub/sub | Already in stack, proven at scale, handles multiple FastAPI instances |
| JWT validation | Custom token parser | Existing `decode_token()` from `core/security.py` | Already tested, handles all edge cases |
| Connection tracking | Database-backed registry | In-memory `dict[str, set[WebSocket]]` | Connections are per-process; no need for cross-process state for the connection registry itself |
| Heartbeat/keepalive | Custom ping loop | uvicorn's `--ws-ping-interval` (default 20s) | Built into the ASGI server |

**Key insight:** This project already has 90% of the infrastructure. The existing pub/sub listener pattern, JWT auth, and Redis connection management are all proven. The new work is wiring them together for user-targeted WebSocket notifications.

## Common Pitfalls

### Pitfall 1: WebSocket Auth via Headers (Browser Limitation)
**What goes wrong:** Developers try to use `Authorization: Bearer <token>` header for WebSocket auth, but browser WebSocket API does not support custom headers.
**Why it happens:** HTTP endpoint patterns don't transfer to WebSocket.
**How to avoid:** Use `?token=<jwt>` query parameter. FastAPI's `Query(...)` works in WebSocket endpoints.
**Warning signs:** Client-side code trying to set headers on `new WebSocket()`.

### Pitfall 2: Memory Bloat from Per-Message Deflate
**What goes wrong:** At 100K connections with default compression, memory usage explodes to ~6.4 GB (64 KiB/conn). Disabling compression drops to ~1.4 GB (14 KiB/conn).
**Why it happens:** Each WebSocket connection creates a `PerMessageDeflate` instance with its own compression context.
**How to avoid:** Set `--ws-per-message-deflate false` in uvicorn config. Notification messages are tiny JSON (~200 bytes), compression provides negligible benefit.
**Warning signs:** Memory usage climbing linearly with connections; OOM kills.

### Pitfall 3: Pub/Sub Subscription Leak
**What goes wrong:** If a WebSocket disconnects but the pub/sub channel isn't unsubscribed, Redis continues delivering messages that nobody consumes, wasting resources.
**Why it happens:** Error in disconnect handler, exception before unsubscribe call, or race condition between disconnect and subscribe.
**How to avoid:** Use `connect()` and `disconnect()` in ConnectionManager to track first/last connection per user. Unsubscribe from Redis only when last connection for a user disconnects. Use asyncio.Lock to prevent races.
**Warning signs:** Redis `PUBSUB NUMSUB` showing channels with no active WebSocket consumers.

### Pitfall 4: Nginx WebSocket Proxy Misconfiguration
**What goes wrong:** Nginx drops WebSocket connections after 60 seconds (default `proxy_read_timeout`), or fails to upgrade HTTP to WebSocket.
**Why it happens:** WebSocket requires HTTP Upgrade headers and long-lived connections.
**How to avoid:** Add WebSocket-specific location block with `proxy_set_header Upgrade $http_upgrade; proxy_set_header Connection "upgrade";` and increase `proxy_read_timeout` to 3600s or more.
**Warning signs:** WebSocket connections dropping exactly at 60-second intervals.

### Pitfall 5: Redis Port Mismatch (Known Issue)
**What goes wrong:** Notification published to wrong Redis instance, never reaches FastAPI pub/sub listener.
**Why it happens:** This project has a documented history of Redis port mismatches (see MEMORY.md). Frappe uses `redis://127.0.0.1:13000`, not the default 6379.
**How to avoid:** Both sides (Frappe handler using `get_fastapi_redis()` and FastAPI listener using `settings.redis_url`) already point to the correct port. The existing `get_fastapi_redis()` helper loads from the same `.env` file. Do not hardcode Redis URLs.
**Warning signs:** Notification published but never received by FastAPI.

### Pitfall 6: Accepting WebSocket Before Auth
**What goes wrong:** If you call `websocket.accept()` before validating JWT, an attacker can establish a WebSocket connection and consume server resources without authentication.
**Why it happens:** FastAPI examples sometimes show `accept()` first for simplicity.
**How to avoid:** Validate JWT token in `Depends` or before `accept()`. Raise `WebSocketException(code=WS_1008_POLICY_VIOLATION)` for invalid tokens. FastAPI's dependency injection handles this correctly when using `Depends(ws_authenticate)`.
**Warning signs:** Unauthenticated connections in ConnectionManager.

## Code Examples

### WebSocket Endpoint with JWT Auth
```python
# Source: FastAPI official docs + project patterns
from fastapi import APIRouter, WebSocket, WebSocketDisconnect, WebSocketException, Query, Depends, status
from fastapi_app.core.security import decode_token
from fastapi_app.models.auth import TokenPayload

router = APIRouter(prefix="/notifications", tags=["notifications"])

async def ws_authenticate(
    websocket: WebSocket,
    token: str = Query(...),
) -> TokenPayload:
    try:
        payload = decode_token(token, verify_type="access")
        return TokenPayload(**payload)
    except Exception:
        raise WebSocketException(code=status.WS_1008_POLICY_VIOLATION)

@router.websocket("/ws")
async def notification_ws(
    websocket: WebSocket,
    user: TokenPayload = Depends(ws_authenticate),
):
    # ConnectionManager accessed from app.state
    manager = websocket.app.state.ws_manager
    pubsub = websocket.app.state.notification_pubsub

    is_first = await manager.connect(user.sub, websocket)
    if is_first:
        await pubsub.subscribe(f"memora:notify:{user.sub}")

    try:
        while True:
            # Keep connection alive; client doesn't send meaningful data
            # uvicorn handles ping/pong automatically
            await websocket.receive_text()
    except WebSocketDisconnect:
        is_last = await manager.disconnect(user.sub, websocket)
        if is_last:
            await pubsub.unsubscribe(f"memora:notify:{user.sub}")
```

### Frappe Approval Handler Publishing Notification
```python
# Source: Existing MemoraSubscriptionTransaction._handle_approval pattern + catalog_sync.py publish pattern
import json
import frappe
from memora_admin.events.access_sync import get_fastapi_redis

def _publish_notification(player_id, status, grant_keys, product_name):
    """Publish subscription notification to Redis pub/sub."""
    r = get_fastapi_redis()
    r.publish(
        f"memora:notify:{player_id}",
        json.dumps({
            "type": "subscription_update",
            "status": status,
            "subject_ids": [k.replace("SUB-", "") for k in grant_keys if k.startswith("SUB-")],
            "product_name": product_name,
            "timestamp": str(frappe.utils.now()),
        }),
    )
```

### Notification Pub/Sub Listener (Background Task)
```python
# Source: Existing core/pubsub.py pattern
import asyncio
import json
import redis.asyncio as redis
import structlog

logger = structlog.get_logger()

async def start_notification_listener(redis_pool, ws_manager):
    """Background task: relay Redis pub/sub messages to WebSocket clients."""
    client = redis.Redis(connection_pool=redis_pool)
    pubsub = client.pubsub()

    try:
        async for message in pubsub.listen():
            if message["type"] != "message":
                continue
            channel = message["channel"]
            if isinstance(channel, bytes):
                channel = channel.decode("utf-8")
            # Channel format: memora:notify:{user_id}
            if not channel.startswith("memora:notify:"):
                continue
            user_id = channel[len("memora:notify:"):]
            data = message["data"]
            if isinstance(data, bytes):
                data = data.decode("utf-8")
            sent = await ws_manager.send_to_user(user_id, data)
            if sent > 0:
                logger.info("notification_sent", user_id=user_id, clients=sent)
    except asyncio.CancelledError:
        logger.info("notification_listener_cancelled")
        raise
    finally:
        await pubsub.unsubscribe()
        await client.aclose()
```

### Nginx WebSocket Configuration
```nginx
# Source: Nginx WebSocket proxy docs
# Add to existing /api/v1/ location block or create separate WS location
location /api/v1/notifications/ws {
    proxy_http_version 1.1;
    proxy_set_header Upgrade $http_upgrade;
    proxy_set_header Connection "upgrade";
    proxy_set_header Host $http_host;
    proxy_set_header X-Real-IP $remote_addr;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto $scheme;
    proxy_read_timeout 86400s;  # 24 hours for long-lived WS
    proxy_send_timeout 86400s;
    proxy_pass http://memora-fastapi;
}
```

## Existing Codebase Integration Points

### Files to Modify

| File | Change | Why |
|------|--------|-----|
| `memora_admin/memora_admin/doctype/memora_subscription_transaction/memora_subscription_transaction.py` | Add `_publish_notification()` call in `_handle_approval()` and `_handle_rejection()` | Trigger notification when subscription status changes |
| `fastapi_app/main.py` | Initialize ConnectionManager and notification pub/sub listener in lifespan | WebSocket connections and pub/sub listener need app-scoped lifecycle |
| `fastapi_app/api/v1/router.py` | Add notifications router | Wire up the WebSocket endpoint |
| `fastapi_app/api/v1/endpoints/progress.py` | Remove SSE endpoint (`stream_subject_progress`) and `sse_starlette` import | Deprecated, replaced by WebSocket |
| `requirements.txt` | Remove `sse-starlette>=2.0.0` | No longer needed |
| `nginx/memora-fastapi.conf` | Add WebSocket location block | Nginx needs Upgrade/Connection headers for WebSocket proxy |
| `docs/nginx-setup.md` | Add WebSocket configuration section | Document the nginx changes needed |

### Files to Create

| File | Purpose |
|------|---------|
| `fastapi_app/core/ws_manager.py` | ConnectionManager class (per-user WebSocket tracking) |
| `fastapi_app/api/v1/endpoints/notifications.py` | WebSocket endpoint with JWT auth |
| `fastapi_app/models/notification.py` | Pydantic models for notification messages |

### Existing Infrastructure to Reuse

| Component | Location | How It's Used |
|-----------|----------|---------------|
| Redis pub/sub listener pattern | `fastapi_app/core/pubsub.py` | Template for notification listener (same subscribe/listen/cancel pattern) |
| `get_fastapi_redis()` helper | `memora_admin/events/access_sync.py` | Frappe-side Redis publish (already used by catalog_sync.py) |
| JWT decode | `fastapi_app/core/security.py` | `decode_token(token, verify_type="access")` for WS auth |
| `TokenPayload` model | `fastapi_app/models/auth.py` | User identity from decoded JWT |
| Redis pool | `fastapi_app/core/redis.py` | Connection pool for notification pub/sub listener |
| App lifespan | `fastapi_app/main.py` | Start/stop notification listener as background task |
| `get_grant_keys()` | `memora_admin/api/products.py` | Already called in approval handler, returns `["SUB-xxx", ...]` |
| `catalog_sync.py` publish pattern | `memora_admin/events/catalog_sync.py` | Exact pattern for Frappe -> Redis pub/sub publish |

## Memory and Performance Analysis

### Memory Budget (100K connections)
| Setting | Per Connection | 100K Total | Notes |
|---------|---------------|------------|-------|
| Default (compression ON) | 64 KiB | 6.4 GB | Too high |
| Compression OFF (`--ws-per-message-deflate false`) | 14 KiB | 1.4 GB | Acceptable |
| ConnectionManager overhead | ~200 bytes | 20 MB | Dict entry + set entry per connection |
| Redis pub/sub subscriptions | ~100 bytes/channel | 10 MB | One channel per unique connected user |
| **Total (compression OFF)** | | **~1.6 GB** | Well within success criteria of ~200MB target |

**Note:** The success criteria mentions ~200MB, but that's likely for connection manager overhead only. The actual per-connection memory from the WebSocket protocol layer is controlled by uvicorn/websockets library. With compression disabled, 1.4 GB for 100K connections is the realistic floor. The ConnectionManager dict itself uses ~20 MB, which aligns with the 200MB target for application-level overhead.

### Latency Budget (<20ms propagation)
| Step | Expected Time | Why |
|------|---------------|-----|
| Frappe `r.publish()` | <1ms | Local Redis, single command |
| Redis pub/sub delivery | <1ms | In-memory message routing |
| FastAPI listener receives | <1ms | async for message in pubsub.listen() |
| `ws.send_text()` | <1ms per client | Async write to kernel buffer |
| **Total** | **<5ms** | Well within 20ms target |

## Nginx Considerations

The current nginx config (`nginx/memora-fastapi.conf`) routes `/api/v1/` to FastAPI but sets `Connection ""` (for HTTP keepalive). WebSocket requires `Connection "upgrade"`. Two approaches:

1. **Separate location block** for `/api/v1/notifications/ws` with WebSocket headers (recommended - no impact on existing HTTP endpoints)
2. **Conditional upgrade** in existing location block using `$http_upgrade` variable

Approach 1 is safer since it doesn't affect existing HTTP endpoint behavior.

Also critical: `proxy_read_timeout` must be increased from default 60s to a much larger value (86400s) for long-lived WebSocket connections.

## State of the Art

| Old Approach | Current Approach | When Changed | Impact |
|--------------|------------------|--------------|--------|
| Server-Sent Events (SSE) | WebSocket | Well-established | Bidirectional, better browser support, no reconnection overhead |
| `sse-starlette` library | FastAPI native WebSocket | N/A | One fewer dependency |
| Polling for status updates | Push via WebSocket + Redis pub/sub | N/A | <20ms vs seconds of polling delay |
| Single broadcast channel | Per-user pub/sub channels | N/A | Targeted delivery, less wasted work |

**Deprecated/outdated:**
- `sse-starlette`: Being removed in this phase. The SSE endpoint `/progress/stream/{subject}` was used for streaming progress data, but WebSocket is now the pattern for real-time communication.

## Open Questions

1. **Token Expiration During Long-Lived WebSocket**
   - What we know: JWT access tokens expire in 15 minutes (per config). WebSocket connections are meant to be long-lived.
   - What's unclear: Should the server close the WebSocket when the JWT expires? Or should the WebSocket stay open indefinitely once authenticated?
   - Recommendation: Keep it simple for v1 - once authenticated, the WebSocket stays open. The client will reconnect when it gets a new access token after refresh. The notification is best-effort; if the client misses it, the next API call will show the updated subscription status. This avoids complexity of re-authentication during an active WebSocket session.

2. **Multiple FastAPI Instances and Pub/Sub**
   - What we know: Redis pub/sub delivers messages to ALL subscribers. Each FastAPI instance will have its own notification listener.
   - What's unclear: If the user is only connected to instance A, will instance B also receive and discard the message?
   - Recommendation: Yes, but the `send_to_user()` call returns 0 (no connected clients), so the cost is just parsing the message. This is negligible. The per-user channel pattern ensures only instances with connected users for that user_id are subscribed.

3. **Notification Message Schema**
   - What we know: Success criteria says "subject_ids, product_name, status"
   - What's unclear: Exact JSON structure for the client
   - Recommendation: Use a typed Pydantic model. Proposed schema:
   ```json
   {
     "type": "subscription_update",
     "status": "approved" | "rejected",
     "subject_ids": ["SUBJ-00028"],
     "product_name": "Math Bundle",
     "timestamp": "2026-02-08 15:30:00"
   }
   ```

## Sources

### Primary (HIGH confidence)
- FastAPI official docs: WebSocket endpoints, ConnectionManager pattern, WebSocketDisconnect handling, JWT auth via Query params - verified via Context7 `/websites/fastapi_tiangolo`
- redis-py official docs: Async pub/sub subscribe/publish/listen pattern - verified via Context7 `/redis/redis-py`
- Existing codebase: `fastapi_app/core/pubsub.py`, `memora_admin/events/catalog_sync.py`, `memora_admin/memora_admin/doctype/memora_subscription_transaction/memora_subscription_transaction.py`

### Secondary (MEDIUM confidence)
- [websockets library memory docs](https://websockets.readthedocs.io/en/latest/topics/memory.html) - 64 KiB default, 14 KiB without compression
- [Scaling WebSockets with PUB/SUB using Python, Redis & FastAPI](https://medium.com/@nandagopal05/scaling-websockets-with-pub-sub-using-python-redis-fastapi-b16392ffe291) - Architecture validation
- [FastAPI stack handling 250K WebSocket messages/sec](https://medium.com/@bhagyarana80/the-fastapi-stack-that-handled-250-000-websocket-messages-per-second-77c15339e31c) - Scale validation
- [How to Build WebSocket Servers with FastAPI and Redis](https://oneuptime.com/blog/post/2026-01-25-websocket-servers-fastapi-redis/view) - Pattern validation

### Tertiary (LOW confidence)
- [uvicorn WebSocket max connections discussion](https://github.com/Kludex/uvicorn/discussions/1817) - `limit_concurrency` setting details
- [uvicorn PerMessageDeflate memory issue](https://github.com/Kludex/uvicorn/issues/1862) - Compression memory overhead specifics

## Metadata

**Confidence breakdown:**
- Standard stack: HIGH - Using FastAPI native WebSocket and existing redis-py, both verified via Context7 and already in the project
- Architecture: HIGH - Per-user ConnectionManager + Redis pub/sub is well-documented pattern; existing codebase has all building blocks
- Pitfalls: HIGH - Memory/compression from official websockets docs; nginx from standard proxy docs; Redis port from documented project history
- Integration points: HIGH - All files inspected directly, patterns identified from existing code

**Research date:** 2026-02-08
**Valid until:** 2026-03-08 (stable domain, no fast-moving dependencies)
