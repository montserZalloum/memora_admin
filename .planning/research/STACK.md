# Stack Research: Memora Game API Sidecar

**Project:** Memora - Gamified Educational Platform Backend
**Researched:** 2026-02-01
**Research Mode:** Ecosystem (Stack dimension)
**Overall Confidence:** HIGH

## Executive Summary

This research covers the technology stack for adding a FastAPI sidecar to an existing Frappe v15 application with 31 DocTypes. The stack targets sub-20ms game API responses at 100K concurrent users, with Redis for hot data (progress bitmaps, wallets, sessions, leaderboards).

**Key finding:** The Python async ecosystem has matured significantly. aioredis was merged into redis-py (use `redis.asyncio`), PyJWT is now the recommended JWT library (python-jose abandoned), and orjson provides 2-4x serialization speedup over standard json. FastAPI 0.128+ with uvicorn workers is production-ready without Gunicorn for containerized deployments.

---

## Recommended Stack

### Core API Framework

| Technology | Version | Purpose | Why | Confidence |
|------------|---------|---------|-----|------------|
| FastAPI | ^0.128.0 | Async API framework | Industry-standard for high-performance Python APIs. Native async, automatic OpenAPI docs, type hints with Pydantic, dependency injection. Released Dec 2025. | HIGH |
| Uvicorn | ^0.40.0 | ASGI server | Production-grade async server. With `[standard]` extras includes uvloop (Cython event loop) and httptools for maximum performance. Released Dec 2025. | HIGH |
| Pydantic | ^2.10.0 | Data validation | FastAPI's native validation layer. V2 is 5-50x faster than V1. Type-safe request/response models. | HIGH |

**Installation:**
```bash
pip install "fastapi>=0.128.0" "uvicorn[standard]>=0.40.0"
```

### Redis Client

| Technology | Version | Purpose | Why | Confidence |
|------------|---------|---------|-----|------------|
| redis | ^7.1.0 | Async Redis client | Official redis-py with merged aioredis. Use `import redis.asyncio as redis`. aioredis standalone is ABANDONED (last release Dec 2021). Released Nov 2025. | HIGH |

**Critical Note:** Do NOT use standalone `aioredis` package - it's archived. The async functionality was merged into `redis-py` 4.2.0+. Use:
```python
import redis.asyncio as redis
# NOT: import aioredis
```

**Installation:**
```bash
pip install "redis>=7.1.0"
```

### Authentication

| Technology | Version | Purpose | Why | Confidence |
|------------|---------|---------|-----|------------|
| PyJWT | ^2.11.0 | JWT encoding/decoding | Actively maintained, recommended by FastAPI docs. python-jose is effectively abandoned and has security concerns. Supports HS256/RS256. Released Jan 2026. | HIGH |
| passlib[bcrypt] | ^1.7.4 | Password hashing | Industry-standard password hashing with bcrypt. Only needed if FastAPI handles password verification (may delegate to Frappe). | MEDIUM |

**Why NOT python-jose:** FastAPI documentation was updated to recommend PyJWT. python-jose had no releases from 2021-2024 and known security issues. Version 3.5.0 (May 2025) exists but adoption has shifted.

**Installation:**
```bash
pip install "PyJWT>=2.11.0" "passlib[bcrypt]>=1.7.4"
```

### Configuration

| Technology | Version | Purpose | Why | Confidence |
|------------|---------|---------|-----|------------|
| pydantic-settings | ^2.12.0 | Environment/config management | Type-safe settings from env vars and .env files. Native FastAPI integration. Replaces old `BaseSettings` from Pydantic v1. Released Nov 2025. | HIGH |

**Usage Pattern:**
```python
from pydantic_settings import BaseSettings, SettingsConfigDict
from functools import lru_cache

class Settings(BaseSettings):
    redis_url: str = "redis://localhost:6379"
    jwt_secret: str
    jwt_algorithm: str = "HS256"

    model_config = SettingsConfigDict(env_file=".env", env_prefix="MEMORA_")

@lru_cache
def get_settings() -> Settings:
    return Settings()
```

**Installation:**
```bash
pip install "pydantic-settings>=2.12.0"
```

### Performance Optimization

| Technology | Version | Purpose | Why | Confidence |
|------------|---------|---------|-----|------------|
| orjson | ^3.11.6 | Fast JSON serialization | 2-4x faster than standard json, 10x faster than json.dumps(). Native dataclass/datetime support. Critical for <20ms target. Released Jan 2026. | HIGH |

**FastAPI Integration:**
```python
from fastapi import FastAPI
from fastapi.responses import ORJSONResponse

app = FastAPI(default_response_class=ORJSONResponse)
```

**Installation:**
```bash
pip install "orjson>=3.11.6"
```

### HTTP Client (Optional)

| Technology | Version | Purpose | Why | Confidence |
|------------|---------|---------|-----|------------|
| httpx | ^0.28.1 | Async HTTP client | For any calls to Frappe API or external services. Async-native, similar API to requests. | MEDIUM |

**Only needed if FastAPI needs to call Frappe's REST API. May not be required if all data flows through Redis.**

**Installation:**
```bash
pip install "httpx>=0.28.1"
```

### Production Deployment

| Technology | Version | Purpose | Why | Confidence |
|------------|---------|---------|-----|------------|
| Gunicorn | ^25.0.0 | Process manager | Multi-worker management with Uvicorn workers. New v25 has HTTP/2 beta. For non-containerized deployments. | HIGH |

**Deployment Options:**

**Option A: Direct Uvicorn (Kubernetes/Docker)**
```bash
# For containerized deployments - single process per container
uvicorn main:app --host 0.0.0.0 --port 8000 --workers 4
# OR using fastapi CLI
fastapi run --workers 4 main:app
```

**Option B: Gunicorn + Uvicorn (Traditional/VM)**
```bash
# For traditional deployments - Gunicorn manages workers
gunicorn main:app -k uvicorn.workers.UvicornWorker -w 4 -b 0.0.0.0:8000
```

**Worker Count:** For async workers, use `workers = CPU_cores` (not the sync formula of 2N+1). Async workers handle concurrency within each process.

---

## Redis Data Patterns

### Bitmap Operations (Progress Tracking)

**Confidence: HIGH** - Verified against official Redis docs.

Redis bitmaps use SETBIT/GETBIT for O(1) operations. Perfect for lesson completion tracking.

```python
import redis.asyncio as redis

async def mark_lesson_complete(r: redis.Redis, player_id: str, subject_id: str, bit_offset: int):
    key = f"progress:{player_id}:{subject_id}"
    await r.setbit(key, bit_offset, 1)

async def is_lesson_complete(r: redis.Redis, player_id: str, subject_id: str, bit_offset: int) -> bool:
    key = f"progress:{player_id}:{subject_id}"
    return bool(await r.getbit(key, bit_offset))

async def count_completed(r: redis.Redis, player_id: str, subject_id: str) -> int:
    key = f"progress:{player_id}:{subject_id}"
    return await r.bitcount(key)
```

**Memory:** 512MB for 4 billion bits. For Memora with ~1000 lessons per subject, each player-subject needs ~125 bytes.

**Warning:** Setting a very high offset (2^32-1) on a new key triggers memory allocation that can block Redis for ~300ms. Pre-allocate or validate offset bounds.

### Sorted Sets (Leaderboards)

**Confidence: HIGH** - Standard Redis pattern.

```python
async def update_leaderboard(r: redis.Redis, board: str, player_id: str, score: int):
    await r.zadd(board, {player_id: score})

async def get_top_players(r: redis.Redis, board: str, limit: int = 100):
    return await r.zrevrange(board, 0, limit - 1, withscores=True)

async def get_player_rank(r: redis.Redis, board: str, player_id: str) -> int:
    rank = await r.zrevrank(board, player_id)
    return rank + 1 if rank is not None else None
```

**Performance:** O(log N) for add/update, O(log N + M) for range queries where M is result count.

### Hash Operations (Wallets)

```python
async def get_wallet(r: redis.Redis, player_id: str) -> dict:
    key = f"wallet:{player_id}"
    return await r.hgetall(key)

async def award_xp(r: redis.Redis, player_id: str, amount: int):
    key = f"wallet:{player_id}"
    await r.hincrby(key, "xp", amount)
```

### Connection Pooling

**Confidence: HIGH** - Verified with redis-py 7.1 docs.

```python
from contextlib import asynccontextmanager
import redis.asyncio as redis

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup: Create connection pool
    app.state.redis = redis.from_url(
        settings.redis_url,
        encoding="utf-8",
        decode_responses=True,
        max_connections=50,  # Tune based on worker count
    )
    yield
    # Shutdown: Close connections
    await app.state.redis.aclose()

app = FastAPI(lifespan=lifespan)

async def get_redis(request: Request) -> redis.Redis:
    return request.app.state.redis
```

---

## FastAPI Patterns

### Lifespan Events (Modern Approach)

**Confidence: HIGH** - Official FastAPI docs.

```python
from contextlib import asynccontextmanager
from fastapi import FastAPI

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    app.state.redis = await create_redis_pool()
    yield
    # Shutdown
    await app.state.redis.aclose()

app = FastAPI(lifespan=lifespan)
```

**Do NOT use:** `@app.on_event("startup")` / `@app.on_event("shutdown")` - deprecated in favor of lifespan.

### Dependency Injection

**Confidence: HIGH** - Core FastAPI pattern.

```python
from fastapi import Depends, Request

async def get_redis(request: Request) -> redis.Redis:
    return request.app.state.redis

async def get_current_player(
    token: str = Depends(oauth2_scheme),
    redis: redis.Redis = Depends(get_redis),
) -> str:
    payload = jwt.decode(token, settings.jwt_secret, algorithms=[settings.jwt_algorithm])
    player_id = payload.get("sub")
    # Validate session in Redis
    session_key = f"session:{player_id}"
    if not await redis.exists(session_key):
        raise HTTPException(status_code=401, detail="Session expired")
    return player_id

@app.get("/wallet")
async def get_wallet(
    player_id: str = Depends(get_current_player),
    redis: redis.Redis = Depends(get_redis),
):
    return await redis.hgetall(f"wallet:{player_id}")
```

### Background Tasks (Without Celery)

**Confidence: MEDIUM** - Context-specific.

Since the PRD specifies using Frappe's scheduler (not Celery), FastAPI handles only in-request background tasks:

```python
from fastapi import BackgroundTasks

def log_interaction(player_id: str, action: str, data: dict):
    """Fire-and-forget: buffer to Redis for later sync"""
    # Sync function - runs in threadpool
    ...

@app.post("/stage/complete")
async def complete_stage(
    stage_data: StageComplete,
    background_tasks: BackgroundTasks,
    player_id: str = Depends(get_current_player),
):
    # Fast path: update Redis
    await update_progress(player_id, stage_data)

    # Background: buffer interaction log
    background_tasks.add_task(log_interaction, player_id, "stage_complete", stage_data.dict())

    return {"status": "ok"}
```

**Limitations of BackgroundTasks:**
- No retry mechanism
- No persistence (lost on crash)
- No status tracking

**For Memora:** Use FastAPI BackgroundTasks for buffering to Redis. Frappe scheduler handles Redis-to-MariaDB sync.

---

## JWT Authentication Pattern

**Confidence: HIGH** - Standard pattern, verified with PyJWT 2.11 docs.

```python
import jwt
from datetime import datetime, timedelta, timezone
from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="token")

def create_access_token(player_id: str, expires_delta: timedelta = timedelta(hours=1)) -> str:
    expire = datetime.now(timezone.utc) + expires_delta
    payload = {
        "sub": player_id,
        "exp": expire,
        "iat": datetime.now(timezone.utc),
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)

def decode_token(token: str) -> dict:
    try:
        return jwt.decode(
            token,
            settings.jwt_secret,
            algorithms=[settings.jwt_algorithm]
        )
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="Token expired")
    except jwt.InvalidTokenError:
        raise HTTPException(status_code=401, detail="Invalid token")

async def get_current_player(token: str = Depends(oauth2_scheme)) -> str:
    payload = decode_token(token)
    return payload["sub"]
```

**Algorithm Choice:**
- **HS256** (symmetric): Simpler, single secret. Good for single-service auth.
- **RS256** (asymmetric): Public/private keys. Better for distributed systems where services verify without signing.

For Memora (single FastAPI sidecar), HS256 is sufficient.

---

## Alternatives Considered

| Category | Recommended | Alternative | Why Not |
|----------|-------------|-------------|---------|
| Redis Client | redis>=7.1 (redis.asyncio) | aioredis (standalone) | **ABANDONED** - last release Dec 2021. Merged into redis-py. |
| Redis Client | redis>=7.1 | redis-om-python | Higher abstraction than needed. Bitmaps not well supported. Adds complexity. |
| JWT Library | PyJWT>=2.11 | python-jose | Effectively **ABANDONED** 2021-2024. Security concerns. FastAPI docs now recommend PyJWT. |
| JWT Library | PyJWT>=2.11 | joserfc | Newer, supports full JOSE spec. But PyJWT is sufficient for JWT-only needs and more widely used. |
| JSON Serializer | orjson | ujson | orjson is 2x faster than ujson in benchmarks. Better dataclass support. |
| JSON Serializer | orjson | standard json | 10x slower. Unacceptable for <20ms target. |
| ASGI Server | uvicorn[standard] | hypercorn | Uvicorn has better FastAPI integration, larger community, more battle-tested. |
| HTTP Client | httpx | aiohttp | httpx has cleaner API, similar to requests. aiohttp is older. Both work. |
| Process Manager | Gunicorn + Uvicorn workers | Uvicorn alone | For non-containerized: Gunicorn provides better worker management, graceful restarts. For K8s: Uvicorn alone is fine. |
| Settings | pydantic-settings | python-dotenv alone | pydantic-settings adds type validation, defaults, environment prefix support. |

---

## Installation Summary

### requirements.txt
```
# Core API
fastapi>=0.128.0
uvicorn[standard]>=0.40.0
pydantic-settings>=2.12.0

# Redis
redis>=7.1.0

# Authentication
PyJWT>=2.11.0

# Performance
orjson>=3.11.6

# Production (non-containerized)
gunicorn>=25.0.0

# Optional: HTTP client for Frappe API calls
httpx>=0.28.1

# Optional: Password hashing if needed
passlib[bcrypt]>=1.7.4
```

### pip install command
```bash
pip install "fastapi>=0.128.0" "uvicorn[standard]>=0.40.0" "pydantic-settings>=2.12.0" \
    "redis>=7.1.0" "PyJWT>=2.11.0" "orjson>=3.11.6" "gunicorn>=25.0.0"
```

---

## Confidence Levels Summary

| Component | Level | Reasoning |
|-----------|-------|-----------|
| FastAPI 0.128.0 | HIGH | Official PyPI, active development, industry standard |
| Uvicorn 0.40.0 | HIGH | Official PyPI, FastAPI recommended |
| redis-py 7.1.0 | HIGH | Official Redis client, verified aioredis merge |
| PyJWT 2.11.0 | HIGH | Official PyPI, FastAPI docs updated to recommend |
| orjson 3.11.6 | HIGH | Verified benchmarks, production-proven |
| pydantic-settings 2.12.0 | HIGH | Pydantic official, FastAPI native support |
| Gunicorn 25.0.0 | HIGH | Industry standard, latest release |
| Lifespan pattern | HIGH | Official FastAPI documentation |
| Redis bitmap patterns | HIGH | Official Redis documentation |
| Worker count (N cores) | MEDIUM | Multiple sources agree, but workload-dependent |
| Background tasks pattern | MEDIUM | Standard pattern, but Frappe scheduler integration untested |

---

## Integration Notes

### FastAPI + Frappe Coexistence

FastAPI runs as a separate service (different port). Communication patterns:

1. **Shared Redis:** FastAPI and Frappe both connect to the same Redis instance. FastAPI owns hot data (progress, wallets, sessions). Frappe scheduler syncs to MariaDB.

2. **Frappe Hooks -> Redis:** Frappe hooks (on_update, etc.) write to Redis directly using redis-py (sync client in Frappe context).

3. **Build Pipeline:** Frappe scheduler processes build queue, generates JSON files, uploads to CDN, publishes invalidation to Redis pub/sub. FastAPI subscribes and clears cache.

4. **Authentication Flow:**
   - Login via Frappe API (validates credentials against MariaDB)
   - Frappe issues JWT (or FastAPI issues JWT after Frappe validation)
   - FastAPI validates JWT statelessly
   - Sessions stored in Redis with TTL

### Frappe Redis Integration

Frappe already uses Redis (cache instance on port 13000). Options:

1. **Shared instance:** Use Frappe's Redis for Memora data. Pro: simpler. Con: may need key prefix discipline.

2. **Separate instance:** Dedicated Redis for game data. Pro: isolation. Con: more infrastructure.

Recommendation: Start with shared instance using `memora:` key prefix. Monitor and separate if needed.

---

## Sources

### Official Documentation (HIGH confidence)
- [FastAPI Official Documentation](https://fastapi.tiangolo.com/)
- [FastAPI Lifespan Events](https://fastapi.tiangolo.com/advanced/events/)
- [FastAPI Dependencies](https://fastapi.tiangolo.com/tutorial/dependencies/)
- [FastAPI Background Tasks](https://fastapi.tiangolo.com/tutorial/background-tasks/)
- [FastAPI Custom Responses (ORJSONResponse)](https://fastapi.tiangolo.com/advanced/custom-response/)
- [FastAPI Settings](https://fastapi.tiangolo.com/advanced/settings/)
- [FastAPI Server Workers](https://fastapi.tiangolo.com/deployment/server-workers/)
- [Uvicorn Deployment](https://www.uvicorn.org/deployment/)
- [redis-py Asyncio Examples](https://redis.readthedocs.io/en/stable/examples/asyncio_examples.html)
- [Redis Bitmaps](https://redis.io/docs/latest/develop/data-types/bitmaps/)
- [Redis Sorted Sets](https://redis.io/docs/latest/develop/data-types/sorted-sets/)
- [Redis SETBIT Command](https://redis.io/docs/latest/commands/setbit/)
- [PyJWT Documentation](https://pyjwt.readthedocs.io/en/latest/usage.html)
- [Pydantic Settings](https://docs.pydantic.dev/latest/concepts/pydantic_settings/)

### PyPI Version Verification (HIGH confidence)
- [FastAPI 0.128.0 on PyPI](https://pypi.org/project/fastapi/) - Released Dec 27, 2025
- [Uvicorn 0.40.0 on PyPI](https://pypi.org/project/uvicorn/) - Released Dec 21, 2025
- [redis-py 7.1.0 on PyPI](https://pypi.org/project/redis/) - Released Nov 19, 2025
- [PyJWT 2.11.0 on PyPI](https://pypi.org/project/PyJWT/) - Released Jan 30, 2026
- [orjson 3.11.6 on PyPI](https://pypi.org/project/orjson/) - Released Jan 29, 2026
- [pydantic-settings 2.12.0 on PyPI](https://pypi.org/project/pydantic-settings/) - Released Nov 10, 2025
- [Gunicorn 25.0.0 on PyPI](https://pypi.org/project/gunicorn/) - Released Feb 1, 2026
- [httpx 0.28.1 on PyPI](https://pypi.org/project/httpx/) - Released Dec 6, 2024

### Community Best Practices (MEDIUM confidence)
- [FastAPI Best Practices - zhanymkanov](https://github.com/zhanymkanov/fastapi-best-practices)
- [FastAPI Production Deployment Best Practices - Render](https://render.com/articles/fastapi-production-deployment-best-practices)
- [FastAPI Best Practices: Production-Ready Patterns for 2025](https://orchestrator.dev/blog/2025-1-30-fastapi-production-patterns/)
- [Dependency Injection in FastAPI: 2026 Playbook](https://thelinuxcode.com/dependency-injection-in-fastapi-2026-playbook-for-modular-testable-apis/)
- [Redis FAQ: aioredis vs redis-py asyncio](https://redis.io/faq/doc/26366kjrif/what-is-the-difference-between-aioredis-v2-0-and-redis-py-asyncio)
- [Setting Up Async Redis Client in FastAPI](https://medium.com/@geetansh2k1/setting-up-and-using-an-async-redis-client-in-fastapi-the-right-way-0409ad3812e6)
- [FastAPI GitHub Discussion: python-jose abandonment](https://github.com/fastapi/fastapi/discussions/9587)
- [orjson Benchmarks and FastAPI Integration](https://undercodetesting.com/boost-fastapi-performance-by-20-with-orjson/)
- [Redis Sorted Sets for Leaderboards](https://redis.io/solutions/leaderboards/)
- [Gunicorn + Uvicorn Guide](https://medium.com/@iklobato/mastering-gunicorn-and-uvicorn-the-right-way-to-deploy-fastapi-applications-aaa06849841e)

---

*Stack research completed: 2026-02-01*
