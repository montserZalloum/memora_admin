# Quickstart: Global API Rate Limiting

**Branch**: `022-global-rate-limiting`

## What This Feature Does

Adds three layers of rate limiting to the FastAPI game API:

1. **Global per-IP limit**: 100 requests/min from any single IP (middleware)
2. **Per-player write limits**: 30/min reviews, 10/min session start/end (endpoint dependencies)
3. **WebSocket connection limit**: 5 concurrent connections per player (in-memory)

## Files to Create

| File | Purpose |
|------|---------|
| `fastapi_app/middleware/rate_limit.py` | `GlobalRateLimitMiddleware` — intercepts all requests |
| `fastapi_app/services/global_rate_limit.py` | `GlobalRateLimiter` — atomic Redis INCR + EXPIRE via Lua |
| `fastapi_app/tests/test_global_rate_limit.py` | Tests for all 3 rate limit layers |

## Files to Modify

| File | Change |
|------|--------|
| `fastapi_app/core/config.py` | Add rate limit settings (5 new env vars with defaults) |
| `fastapi_app/main.py` | Register `GlobalRateLimitMiddleware` after `RequestIDMiddleware` |
| `fastapi_app/api/deps.py` | Add `require_rate_limit()` dependency factory |
| `fastapi_app/api/v1/endpoints/reviews.py` | Add `Depends(require_rate_limit("reviews", 30))` to `submit_reviews` |
| `fastapi_app/api/v1/endpoints/sessions.py` | Add rate limit deps to `start_session` and `end_session` |
| `fastapi_app/core/ws_manager.py` | Add `max_connections_per_user` param, check in `connect()` |
| `fastapi_app/tests/conftest.py` | Add cleanup patterns for `memora:global_rl:*` and `memora:rl:*` |

## Implementation Order

### Phase 1: Global Middleware (P1 — highest value)
1. Add settings to `config.py`
2. Create `GlobalRateLimiter` service
3. Create `GlobalRateLimitMiddleware`
4. Register middleware in `main.py`
5. Write tests

### Phase 2: Per-Player Write Limits (P2)
1. Add `require_rate_limit()` factory to `deps.py`
2. Wire into `reviews.py`, `sessions.py`
3. Write tests

### Phase 3: WebSocket Connection Limit (P3)
1. Modify `ConnectionManager.connect()` in `ws_manager.py`
2. Write tests

## Key Patterns to Follow

### Existing Lua Script (from `services/rate_limit.py`)
```lua
local count = redis.call("INCR", KEYS[1])
if count == 1 then
    redis.call("EXPIRE", KEYS[1], ARGV[1])
end
return count
```

### Existing Middleware Pattern (from `middleware/request_id.py`)
```python
class RequestIDMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        # ... pre-processing ...
        response = await call_next(request)
        # ... post-processing ...
        return response
```

### Existing Rate Limiter Usage (from `endpoints/auth.py`)
```python
rate_limiter = RateLimiter(redis)
allowed, retry_after, limit_type = await rate_limiter.check_rate_limit(ip, account)
if not allowed:
    return JSONResponse(status_code=429, ...)
```

## Testing

```bash
# Run all rate limit tests
cd /home/corex/aurevia-bench/apps/memora_admin
python -m pytest fastapi_app/tests/test_global_rate_limit.py -v

# Run existing rate limiter tests (should still pass)
python -m pytest fastapi_app/tests/test_rate_limiter.py -v
```

## After Deployment

```bash
# Restart FastAPI sidecar (required after code changes)
pkill -f "uvicorn fastapi_app.main:app"
# Wait 2-3s, verify:
curl http://127.0.0.1:8002/api/v1/health/live

# Verify global rate limit headers on any response
curl -v http://127.0.0.1:8002/api/v1/health/live 2>&1 | grep X-RateLimit
# Should NOT show rate limit headers (health is exempt)

# Check a non-exempt endpoint
curl -v -H "Authorization: Bearer $TOKEN" http://127.0.0.1:8002/api/v1/settings/ 2>&1 | grep X-RateLimit
# Should show: X-RateLimit-Limit, X-RateLimit-Remaining, X-RateLimit-Reset
```

## Environment Variables (Optional Tuning)

| Variable | Default | Description |
|----------|---------|-------------|
| `GLOBAL_RATE_LIMIT` | 100 | Max requests per IP per window |
| `GLOBAL_RATE_LIMIT_WINDOW` | 60 | Window duration in seconds |
| `REVIEWS_RATE_LIMIT` | 30 | Max review submits per player per window |
| `SESSION_RATE_LIMIT` | 10 | Max session start/end per player per window |
| `WS_MAX_CONNECTIONS_PER_USER` | 5 | Max concurrent WebSocket connections |
