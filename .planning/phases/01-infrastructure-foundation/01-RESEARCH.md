# Phase 01: Infrastructure Foundation - Research

**Researched:** 2026-02-01
**Domain:** FastAPI + Redis + Nginx infrastructure for sidecar API
**Confidence:** HIGH

## Summary

This research covers the infrastructure foundation for a FastAPI sidecar running alongside Frappe with shared Redis. The standard approach uses FastAPI's lifespan context managers for startup/shutdown, redis-py's async connection pooling, pydantic-settings for .env configuration, and nginx location blocks for request routing.

The infrastructure pattern is well-established: FastAPI with async Redis via `redis.asyncio`, configuration via `pydantic-settings` with `@lru_cache`, structured logging with `structlog` (JSON in production, colored in dev), and nginx upstream blocks for multi-backend routing. The key challenge is integrating with Frappe's existing nginx configuration template while adding the FastAPI backend.

**Primary recommendation:** Use FastAPI lifespan for Redis pool lifecycle, pydantic-settings with `SettingsConfigDict(env_file='.env')`, structlog with environment-based renderer selection, and extend Frappe's nginx template with an additional upstream block for the FastAPI backend on port 8001.

## Standard Stack

The established libraries/tools for this domain:

### Core
| Library | Version | Purpose | Why Standard |
|---------|---------|---------|--------------|
| FastAPI | 0.115+ | Async web framework | Official lifespan support, dependency injection, production-proven |
| redis-py | 5.0+ | Async Redis client | Official redis-py with full asyncio support via `redis.asyncio` |
| pydantic-settings | 2.0+ | Configuration management | Official Pydantic extension, .env loading, type validation |
| structlog | 24.0+ | Structured logging | Environment-aware output (JSON/console), request correlation |
| uvicorn | 0.27+ | ASGI server | Production-grade with --proxy-headers support |

### Supporting
| Library | Version | Purpose | When to Use |
|---------|---------|---------|-------------|
| tenacity | 9.0+ | Retry logic | Read operations with backoff (per CONTEXT.md decision) |
| python-dotenv | 1.0+ | .env file loading | Automatically loaded by pydantic-settings |

### Alternatives Considered
| Instead of | Could Use | Tradeoff |
|------------|-----------|----------|
| structlog | loguru | loguru simpler but structlog better for JSON + context binding |
| tenacity | backoff | tenacity more widely used, better async support |
| pydantic-settings | python-decouple | pydantic-settings integrates with Pydantic validation |

**Installation:**
```bash
pip install fastapi uvicorn[standard] redis pydantic-settings structlog tenacity python-dotenv
```

## Architecture Patterns

### Recommended Project Structure
```
fastapi_app/
├── main.py                  # FastAPI app with lifespan
├── core/
│   ├── __init__.py
│   ├── config.py            # Settings class with pydantic-settings
│   ├── logging.py           # structlog configuration
│   └── redis.py             # Redis pool and dependency
├── api/
│   ├── __init__.py
│   ├── deps.py              # Shared dependencies (get_redis, etc.)
│   └── v1/
│       ├── __init__.py
│       ├── router.py        # APIRouter aggregation
│       └── endpoints/
│           └── health.py    # Health check endpoints
├── models/                  # Pydantic models (future phases)
├── services/                # Business logic (future phases)
└── tests/
```

### Pattern 1: Lifespan Context Manager for Resource Lifecycle
**What:** Use `@asynccontextmanager` lifespan to manage Redis pool creation/cleanup
**When to use:** Always for async resources that need startup/shutdown handling

```python
# Source: Context7 FastAPI docs
from contextlib import asynccontextmanager
from fastapi import FastAPI
import redis.asyncio as redis

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup: Create Redis connection pool
    pool = redis.ConnectionPool.from_url(
        settings.redis_url,
        max_connections=20,
        decode_responses=True
    )
    app.state.redis_pool = pool

    # Verify connection (fail fast per CONTEXT.md)
    client = redis.Redis(connection_pool=pool)
    try:
        await client.ping()
    except redis.ConnectionError as e:
        await pool.disconnect()
        raise RuntimeError(f"Redis connection failed: {e}")
    finally:
        await client.aclose()

    yield  # App runs here

    # Shutdown: Clean up pool
    await pool.disconnect()

app = FastAPI(lifespan=lifespan)
```

### Pattern 2: Dependency Injection for Redis Client
**What:** Use FastAPI's `Depends()` with `request.app.state` for per-request Redis clients
**When to use:** Every endpoint that needs Redis access

```python
# Source: Context7 FastAPI + redis-py docs
from typing import Annotated
from fastapi import Depends, Request
import redis.asyncio as redis

async def get_redis(request: Request) -> redis.Redis:
    """Get Redis client from connection pool stored in app state."""
    return redis.Redis(connection_pool=request.app.state.redis_pool)

# Usage in endpoint
@app.get("/api/v1/health/ready")
async def health_ready(redis: Annotated[redis.Redis, Depends(get_redis)]):
    try:
        await redis.ping()
        return {"status": "healthy", "redis": "ok"}
    except redis.ConnectionError:
        return JSONResponse(
            status_code=503,
            content={"status": "unhealthy", "redis": "unreachable"}
        )
```

### Pattern 3: Environment-Based Logging Configuration
**What:** Configure structlog to output JSON in production, colored console in development
**When to use:** Always for log configuration

```python
# Source: Context7 structlog docs
import sys
import structlog

def configure_logging(environment: str):
    shared_processors = [
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.contextvars.merge_contextvars,  # For request_id
    ]

    if environment == "production":
        processors = shared_processors + [
            structlog.processors.dict_tracebacks,
            structlog.processors.JSONRenderer(),
        ]
    else:
        processors = shared_processors + [
            structlog.dev.ConsoleRenderer(colors=True),
        ]

    structlog.configure(
        processors=processors,
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=True,
    )
```

### Pattern 4: Settings with @lru_cache
**What:** Load settings once with caching, inject via dependency
**When to use:** Configuration access anywhere in the app

```python
# Source: Context7 pydantic-settings + FastAPI docs
from functools import lru_cache
from pydantic_settings import BaseSettings, SettingsConfigDict

class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore"
    )

    # Redis
    redis_url: str = "redis://localhost:6379/0"
    redis_key_prefix: str = "memora:"

    # JWT (for future phases)
    jwt_secret: str
    jwt_algorithm: str = "HS256"

    # Paths
    bitmap_json_path: str = "/home/corex/aurevia-bench/sites/x.conanacademy.com/private/memora_bitmaps"

    # Server
    environment: str = "development"
    api_version: str = "v1"

    # Logging
    slow_redis_threshold_ms: int = 50

@lru_cache
def get_settings() -> Settings:
    return Settings()
```

### Pattern 5: Request ID Middleware for Correlation
**What:** Generate unique request_id for every request, bind to log context
**When to use:** All production APIs for debugging and support

```python
# Source: structlog contextvars pattern
import uuid
from starlette.middleware.base import BaseHTTPMiddleware
import structlog

class RequestIDMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        request_id = str(uuid.uuid4())[:8]
        structlog.contextvars.clear_contextvars()
        structlog.contextvars.bind_contextvars(request_id=request_id)

        response = await call_next(request)
        response.headers["X-Request-ID"] = request_id
        return response
```

### Anti-Patterns to Avoid
- **Creating Redis client per request without pool:** Creates connection overhead; always use connection pool
- **Blocking operations in async endpoints:** Never use sync Redis client in async routes; always use `redis.asyncio`
- **Hardcoded configuration values:** Always load from environment; use pydantic-settings
- **Global mutable state for Redis:** Use `app.state` for lifecycle management, not module-level globals
- **Catching all exceptions in health checks:** Be specific about what constitutes "unhealthy"

## Don't Hand-Roll

Problems that look simple but have existing solutions:

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| Retry with backoff | Manual retry loops | tenacity | Handles edge cases, async support, customizable |
| Configuration loading | Custom .env parser | pydantic-settings | Validation, type coercion, nested config |
| Request correlation | Manual header passing | structlog contextvars | Thread-safe, automatic propagation |
| Connection pooling | Manual pool management | redis.ConnectionPool | Handles cleanup, max connections, health |
| ASGI server | Custom event loop | uvicorn | Production-tested, signal handling, reloading |

**Key insight:** Infrastructure code has many edge cases (connection drops, signal handling, cleanup ordering). Battle-tested libraries handle these; custom solutions will miss corner cases discovered over years of production use.

## Common Pitfalls

### Pitfall 1: Redis Connection Not Verified at Startup
**What goes wrong:** App starts successfully but Redis is unreachable; first request fails
**Why it happens:** Lifespan only creates pool, doesn't verify connectivity
**How to avoid:** Explicitly `ping()` Redis in lifespan before `yield`; raise RuntimeError on failure (fail fast per CONTEXT.md)
**Warning signs:** Health check returns 500 immediately after deploy

### Pitfall 2: Missing `await pool.disconnect()` on Shutdown
**What goes wrong:** Connections leak; Redis warns about abandoned connections
**Why it happens:** Lifespan shutdown code after `yield` skipped due to exception
**How to avoid:** Use try/finally in lifespan to ensure cleanup runs
**Warning signs:** Redis `INFO clients` shows increasing connections

### Pitfall 3: Nginx proxy_pass Without Trailing Slash
**What goes wrong:** URL paths doubled (e.g., `/api/v1/api/v1/health`)
**Why it happens:** Nginx path handling differs with/without trailing slash in proxy_pass
**How to avoid:** Test exact URL patterns; use `proxy_pass http://backend/;` with trailing slash when stripping location prefix
**Warning signs:** 404s with doubled path segments in logs

### Pitfall 4: Blocking Sync Code in Async Endpoints
**What goes wrong:** Single slow request blocks entire event loop; all concurrent requests delayed
**Why it happens:** Using sync Redis client or sync file I/O in async endpoint
**How to avoid:** Use `redis.asyncio` exclusively; verify all dependencies are async-compatible
**Warning signs:** P99 latency spikes when load increases

### Pitfall 5: Settings Loaded Multiple Times
**What goes wrong:** Each import reads .env file; slow startup, memory waste
**Why it happens:** Settings() called without caching
**How to avoid:** Always use `@lru_cache` on `get_settings()` function
**Warning signs:** Startup time increases; multiple "Loading settings" log lines

### Pitfall 6: Health Endpoint Caches Response
**What goes wrong:** Health check returns "healthy" when Redis is actually down
**Why it happens:** HTTP caching or application-level result caching
**How to avoid:** Set `Cache-Control: no-store` on health endpoints; never cache health check results
**Warning signs:** Stale "healthy" responses during actual outages

## Code Examples

Verified patterns from official sources:

### FastAPI Lifespan with Redis Pool (Complete Example)
```python
# Source: Context7 FastAPI + redis-py docs combined
from contextlib import asynccontextmanager
from fastapi import FastAPI, Depends, Request
from fastapi.responses import JSONResponse
import redis.asyncio as redis
from typing import Annotated

from .core.config import get_settings, Settings

@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()

    # Create Redis pool
    pool = redis.ConnectionPool.from_url(
        settings.redis_url,
        max_connections=20,
        decode_responses=True
    )
    app.state.redis_pool = pool

    # Fail fast: verify connection
    client = redis.Redis(connection_pool=pool)
    try:
        if not await client.ping():
            raise RuntimeError("Redis ping failed")
    except redis.ConnectionError as e:
        await pool.disconnect()
        raise RuntimeError(f"Cannot start without Redis: {e}")
    finally:
        await client.aclose()

    yield

    await pool.disconnect()

app = FastAPI(
    title="Memora Game API",
    version="1.0.0",
    lifespan=lifespan
)
```

### Kubernetes-Style Health Endpoints
```python
# Source: CONTEXT.md decisions + FastAPI patterns
from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
import redis.asyncio as redis

router = APIRouter(prefix="/health", tags=["health"])

@router.get("/live")
async def liveness():
    """Liveness check - fast, no dependencies."""
    return {"status": "alive", "api_version": get_settings().api_version}

@router.get("/ready")
async def readiness(redis_client: Annotated[redis.Redis, Depends(get_redis)]):
    """Readiness check - verifies Redis connection."""
    dependencies = {}
    overall_status = "ready"

    try:
        await redis_client.ping()
        dependencies["redis"] = "ok"
    except redis.ConnectionError:
        dependencies["redis"] = "unreachable"
        overall_status = "not_ready"

    status_code = 200 if overall_status == "ready" else 503
    return JSONResponse(
        status_code=status_code,
        content={
            "status": overall_status,
            "api_version": get_settings().api_version,
            "dependencies": dependencies
        },
        headers={"Cache-Control": "no-store"}
    )
```

### Nginx Configuration for FastAPI + Frappe
```nginx
# Add to existing Frappe nginx.conf
# New upstream for FastAPI (add alongside existing frappe upstream)
upstream memora-fastapi {
    server 127.0.0.1:8001 fail_timeout=0;
}

# Location block for FastAPI routes (add within server block)
location /api/v1/ {
    proxy_http_version 1.1;
    proxy_set_header Host $http_host;
    proxy_set_header X-Real-IP $remote_addr;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto $scheme;
    proxy_set_header X-Request-ID $request_id;
    proxy_pass http://memora-fastapi;
}

# Existing Frappe routes continue to work
location /api/method/ {
    # Existing Frappe proxy configuration
    proxy_pass http://{{ bench_name }}-frappe;
    # ... rest of existing config
}
```

### Tenacity Retry for Read Operations
```python
# Source: Context7 tenacity docs + CONTEXT.md decisions
from tenacity import (
    retry,
    stop_after_attempt,
    wait_exponential,
    retry_if_exception_type
)
import redis.asyncio as redis

# Read operations: retry 1-2 times with backoff (per CONTEXT.md)
@retry(
    stop=stop_after_attempt(2),
    wait=wait_exponential(multiplier=0.1, min=0.1, max=1),
    retry=retry_if_exception_type(redis.ConnectionError)
)
async def get_progress_with_retry(client: redis.Redis, key: str) -> bytes:
    """Fetch progress bitmap with retry for transient failures."""
    return await client.get(key)

# Write operations: NO retry (per CONTEXT.md - prevent duplicate processing)
async def set_progress(client: redis.Redis, key: str, bit: int) -> bool:
    """Set progress bit - fails immediately on error, client handles retry."""
    return await client.setbit(key, bit, 1)
```

### Slow Redis Operation Logging
```python
# Source: structlog patterns + CONTEXT.md decisions
import time
import structlog
from functools import wraps

logger = structlog.get_logger()

def log_slow_redis(threshold_ms: int = 50):
    """Log Redis operations that exceed threshold (per CONTEXT.md: ~50ms)."""
    def decorator(func):
        @wraps(func)
        async def wrapper(*args, **kwargs):
            start = time.perf_counter()
            try:
                return await func(*args, **kwargs)
            finally:
                duration_ms = (time.perf_counter() - start) * 1000
                if duration_ms > threshold_ms:
                    logger.warning(
                        "slow_redis_operation",
                        operation=func.__name__,
                        duration_ms=round(duration_ms, 2)
                    )
        return wrapper
    return decorator
```

## State of the Art

| Old Approach | Current Approach | When Changed | Impact |
|--------------|------------------|--------------|--------|
| `@app.on_event("startup")` | `lifespan` context manager | FastAPI 0.93+ (2023) | Single place for startup/shutdown, cleaner resource management |
| `aioredis` library | `redis.asyncio` module | redis-py 4.2+ (2022) | Official async support, no separate package needed |
| Pydantic v1 Settings | pydantic-settings 2.0+ | Pydantic v2 (2023) | Separate package, `model_config` instead of `class Config` |
| structlog dict processors | structlog stdlib integration | structlog 21.0+ (2021) | Better stdlib logging compatibility |

**Deprecated/outdated:**
- `aioredis`: Merged into redis-py as `redis.asyncio` - use redis-py 5.0+
- `@app.on_event("startup"/"shutdown")`: Deprecated in favor of lifespan context managers
- `class Config` in Pydantic Settings: Replaced by `model_config = SettingsConfigDict(...)`

## Open Questions

Things that couldn't be fully resolved:

1. **Frappe Nginx Template Integration**
   - What we know: Frappe uses Jinja2 templates in `bench/config/templates/nginx.conf`
   - What's unclear: Best approach to extend vs override (site-specific config vs template modification)
   - Recommendation: Create separate nginx include file for FastAPI, include it from main site config

2. **Redis Key Prefix Collision with Frappe**
   - What we know: Frappe uses Redis for caching; Memora needs `memora:*` prefix
   - What's unclear: Whether Frappe has existing keys that might conflict
   - Recommendation: Verify existing Redis keys with `KEYS *` before deployment; document key namespaces

3. **Uvicorn Worker Count for Sidecar**
   - What we know: Uvicorn supports multi-worker mode
   - What's unclear: Optimal worker count when running alongside Frappe workers
   - Recommendation: Start with 2 workers, monitor CPU usage, adjust based on load testing

## Sources

### Primary (HIGH confidence)
- `/websites/fastapi_tiangolo` (Context7) - Lifespan events, dependency injection, error handling
- `/redis/redis-py` (Context7) - Async connection pool, health checks, pipeline operations
- `/pydantic/pydantic-settings` (Context7) - .env loading, SettingsConfigDict, nested config
- `/hynek/structlog` (Context7) - JSON/console rendering, contextvars, stdlib integration
- `/websites/tenacity_readthedocs_io_en` (Context7) - Async retry, exponential backoff

### Secondary (MEDIUM confidence)
- [FastAPI docs - Behind a Proxy](https://fastapi.tiangolo.com/advanced/behind-a-proxy/) - Nginx proxy configuration
- [Frappe bench nginx template](https://github.com/frappe/bench/blob/develop/bench/config/templates/nginx.conf) - Existing nginx patterns
- [FastAPI production deployment guide (2026)](https://blog.greeden.me/en/2026/01/20/complete-guide-to-deploying-fastapi-in-production-reliable-operations-with-uvicorn-multi-workers-docker-and-a-reverse-proxy/) - Uvicorn + Nginx setup

### Tertiary (LOW confidence)
- WebSearch results for "FastAPI project structure best practices 2026" - General structure recommendations (need validation against actual production codebases)

## Metadata

**Confidence breakdown:**
- Standard Stack: HIGH - All libraries verified via Context7 with current documentation
- Architecture: HIGH - Patterns from official FastAPI and structlog docs, well-established
- Pitfalls: HIGH - Common issues documented in official sources and verified in production
- Nginx Integration: MEDIUM - General patterns clear, specific Frappe integration needs testing

**Research date:** 2026-02-01
**Valid until:** 2026-03-01 (30 days - stable domain, slow-moving stack)
