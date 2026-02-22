# Research: Global API Rate Limiting

**Date**: 2026-02-22
**Branch**: `022-global-rate-limiting`

## Research Summary

All technical unknowns resolved. The codebase has clear patterns to follow for every component.

## Decision 1: Global Rate Limit — Middleware vs Dependency

**Decision**: Starlette `BaseHTTPMiddleware` (same as `RequestIDMiddleware`)

**Rationale**: The global rate limit must apply to ALL requests before routing, including unauthenticated ones. A middleware intercepts at the ASGI level before FastAPI dependency resolution. A FastAPI dependency would require injection into every endpoint individually.

**Alternatives considered**:
- Raw ASGI middleware: More performant but harder to implement/test. `BaseHTTPMiddleware` adds negligible overhead and matches existing pattern (`request_id.py`).
- FastAPI dependency on router: Would miss WebSocket upgrade requests and require manual addition to every new endpoint.

## Decision 2: Per-Player Rate Limit — Middleware vs Dependency

**Decision**: FastAPI `Depends()` factory function in `deps.py`

**Rationale**: Per-player limits require `player_id` from the JWT, which is only available after `get_current_user()` dependency resolves. A middleware cannot access this without duplicating JWT parsing. A reusable dependency factory keeps it DRY and composable.

**Pattern**:
```python
def require_rate_limit(scope: str, limit: int, window: int = 60):
    async def _check(user: CurrentUser, redis: RedisClient):
        # Lua script INCR + EXPIRE
        ...
    return _check
```

**Alternatives considered**:
- Decorator on endpoint functions: Loses FastAPI dependency injection benefits. Harder to test in isolation.
- Second middleware with JWT parsing: Duplicates auth logic, violates DRY.

## Decision 3: Lua Script — Reuse Existing or New

**Decision**: New `GlobalRateLimiter` service with the same Lua script pattern but returning count for header computation.

**Rationale**: The existing `RateLimiter` class in `services/rate_limit.py` is tightly coupled to the dual-key (IP + account) login pattern. Extending it would muddy its API. A new `GlobalRateLimiter` service with a single-key design is cleaner and returns `(allowed, count, ttl)` for computing `X-RateLimit-Remaining` headers.

The Lua script itself is identical (INCR + conditional EXPIRE) — just the Python wrapper differs.

**Alternatives considered**:
- Extend `RateLimiter` with optional `account` param: Works but creates a confusing API where `account_limit` is meaningless for global use.
- Inline Lua in middleware: Works but violates separation of concerns.

## Decision 4: IP Extraction — X-Forwarded-For Handling

**Decision**: Extract client IP from `X-Forwarded-For` header (rightmost untrusted entry) with fallback to `request.client.host`.

**Rationale**: The app sits behind nginx which sets `X-Forwarded-For`. Using `request.client.host` alone would always see `127.0.0.1` (the proxy). The "rightmost untrusted" approach is standard and avoids spoofing via client-injected headers.

**Implementation**: Since there's a single trusted proxy (nginx), take the last entry in `X-Forwarded-For` before the proxy. In practice with a single-proxy setup: use `request.client.host` if `X-Forwarded-For` is absent, otherwise take the first `X-Forwarded-For` entry (nginx appends the real client IP as the first entry).

**Alternatives considered**:
- `X-Real-IP` header: nginx can set this, but `X-Forwarded-For` is more standard and what the existing `auth.py` already uses.
- `request.client.host` only: Would always see proxy IP, making rate limiting useless.

## Decision 5: WebSocket Connection Limit — In-Memory vs Redis

**Decision**: In-memory counter in `ConnectionManager` (no Redis)

**Rationale**: `ConnectionManager` already tracks `_connections: dict[str, set[WebSocket]]`. Checking `len(self._connections[user_id])` against the limit before `websocket.accept()` is O(1) and requires zero Redis calls. The connection count is inherently per-process state (WebSocket objects can't be shared across processes).

**Alternatives considered**:
- Redis counter: Adds unnecessary latency and complexity. The counter would drift if the process crashes without cleanup. In-memory is simpler and more accurate for a single-process sidecar.

## Decision 6: Rate Limit Response Headers

**Decision**: Global middleware adds `X-RateLimit-Limit`, `X-RateLimit-Remaining`, `X-RateLimit-Reset` to every non-exempt response using data already returned by the Lua script.

**Rationale**: Per clarification session, the Lua script already returns the current count from INCR. Computing remaining = limit - count is trivial. The `EXPIRE` TTL gives the reset time. No extra Redis round-trip needed.

**Headers on 429 responses**: Both global headers AND `Retry-After` header (plus JSON body with `error` and `retry_after`).

## Decision 7: Exempt Path Detection

**Decision**: Check `request.url.path` prefix against a frozen set of exempt prefixes.

**Exempt paths**:
- `/api/v1/health/` — load balancer probes must always respond
- `/api/v1/webhooks/payment` — trusted payment provider callbacks (per clarification)

**Implementation**: `path.startswith()` check in middleware `dispatch()` before any Redis call.

## Decision 8: Fail-Open Behavior

**Decision**: Wrap Redis calls in try/except, allow request on any Redis error, log warning via structlog.

**Rationale**: FR-008 requires fail-open. A Redis connection failure should not block legitimate traffic. The warning log enables operators to detect degraded rate limiting.

**Implementation**:
```python
try:
    count = await self._script(keys=[key], args=[window])
except Exception:
    logger.warning("rate_limit_redis_unavailable", ...)
    return True, 0, 0  # allowed, count=0, ttl=0
```

## Decision 9: Configuration Approach

**Decision**: Add rate limit settings to `Settings` class in `core/config.py` with sensible defaults.

**New settings**:
- `global_rate_limit`: int = 100
- `global_rate_limit_window`: int = 60
- `reviews_rate_limit`: int = 30
- `session_rate_limit`: int = 10
- `ws_max_connections_per_user`: int = 5

**Rationale**: Environment variables via pydantic-settings. Configurable without code changes per spec assumption. Matches existing pattern (all settings in one `Settings` class).

## Decision 10: Test Strategy

**Decision**: Real Redis tests (per constitution VIII) in `test_global_rate_limit.py`.

**Test structure**:
- Global middleware tests: Use `app_client` fixture, send N+1 requests, verify 429.
- Per-player dependency tests: Use `authed_client` fixture, test each endpoint.
- WebSocket limit tests: Test `ConnectionManager.connect()` directly with mock WebSockets.
- Fail-open tests: Mock Redis to raise `ConnectionError`, verify request passes through.

**Test isolation**: Use `test_prefix` fixture for unique Redis key namespaces. Cleanup via existing `cleanup_keys` fixture pattern.
