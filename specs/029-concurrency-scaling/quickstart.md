# Quickstart: 100k Concurrency Scaling Optimizations

## What This Feature Does

Makes the FastAPI sidecar handle 100k concurrent users by:
1. Making Redis connection pool size configurable (was hardcoded at 20)
2. Replacing 500 individual Redis commands with 1 single-fetch bitmap decode
3. Parallelizing progress summary (8 subjects in ~10ms instead of ~80ms)
4. Per-user WebSocket locks (no cross-user contention)
5. Parallel WebSocket broadcasts (configurable)
6. Configurable rate limiter fail behavior
7. Configurable upstream HTTP client limits

## Development: Zero Changes Required

All defaults match current behavior. Development continues working identically:
```bash
# Same as before — no new env vars needed
uvicorn fastapi_app.main:app --reload --port 8002
```

## Production: Set Environment Variables

Create or add to `.env`:
```bash
# Redis pool (default: 20 — increase for production)
REDIS_MAX_CONNECTIONS=200

# WebSocket broadcast (0=sequential, >0=parallel with semaphore)
WS_BROADCAST_CONCURRENCY=50

# Rate limiter (true=pass-through on Redis failure, false=reject)
RATE_LIMIT_FAIL_OPEN=false

# Upstream HTTP client (tune for cache-miss hydration storms)
FRAPPE_TIMEOUT=10.0
FRAPPE_MAX_CONNECTIONS=200
FRAPPE_MAX_KEEPALIVE=50
```

## Verify Changes

```bash
# Health check — should show pool size in startup logs
curl http://127.0.0.1:8002/api/v1/health/live

# Check rate limit headers
curl -v http://127.0.0.1:8002/api/v1/progress/ -H "Authorization: Bearer ..."
# Look for: X-RateLimit-Limit, X-RateLimit-Remaining, X-RateLimit-Reset
```

## Files Changed

| File | Change |
|------|--------|
| `fastapi_app/core/config.py` | 6 new settings fields |
| `fastapi_app/core/redis.py` | Pool size from settings, log at startup |
| `fastapi_app/services/progress.py` | Single-fetch bitmap decode |
| `fastapi_app/api/v1/endpoints/progress.py` | Parallel subject summary |
| `fastapi_app/core/ws_manager.py` | Per-user locks + parallel broadcast |
| `fastapi_app/middleware/rate_limit.py` | Configurable fail behavior |
| `fastapi_app/services/frappe_client.py` | Configurable timeout/limits |
| `fastapi_app/main.py` | Pass new settings to middleware + ws_manager |
| `.env.example` | Document new env vars |
| `production.env.example` | New file — recommended production values |

## Rollback

Remove production environment variables → system reverts to development defaults. No code changes needed.
