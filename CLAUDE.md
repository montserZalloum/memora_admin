# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Memora Admin is a gamified educational platform backend for Arabic-speaking students. It uses a **dual architecture**:
- **Frappe v15**: Admin panel, content management, ORM, 31 DocTypes
- **FastAPI sidecar**: High-performance game API (sub-20ms responses)

Key data stores: **Dual Redis** architecture — dedicated Memora Redis on port 13001 (hot data: progress bitmaps, wallets, sessions) isolated from Frappe cache Redis on port 13000. MariaDB remains source of truth (cold data via Frappe). `bench clear-cache` only affects Frappe's Redis (13000), leaving game data safe on 13001.

## Development Commands

```bash
# Install app in Frappe bench
cd $PATH_TO_YOUR_BENCH
bench get-app $URL_OF_THIS_REPO --branch develop
bench install-app memora_admin

# our site
x.conanacademy.com

# Enable pre-commit hooks
cd apps/memora_admin
pre-commit install

# Run Frappe development server
bench start

# Run FastAPI sidecar (from bench root)
cd apps/memora_admin
uvicorn fastapi_app.main:app --reload --port 8002

# Install FastAPI dependencies
pip install -r apps/memora_admin/requirements.txt
```

## FastAPI Server Management

**Current deployment**: FastAPI is running on **port 8002** at `http://127.0.0.1:8002`

**IMPORTANT**: The FastAPI server is managed by a process supervisor and does NOT auto-reload on code changes.

**When to restart**:
- After adding new endpoints or routes
- After modifying endpoint logic, dependencies, or middleware
- After changing models, services, or core configuration
- Basically, after ANY code change to `fastapi_app/*`

**How to restart**:
```bash
# Find and kill the uvicorn process
pkill -f "uvicorn fastapi_app.main:app"

# Or kill by port
fuser -k 8002/tcp

# The process supervisor will automatically restart it
# Wait 2-3 seconds and verify it's running:
curl http://127.0.0.1:8002/api/v1/health/live

# Expected response: {"status":"alive","api_version":"v1"}
```

**Testing endpoints**:
```bash
# Health check
curl http://127.0.0.1:8002/api/v1/health/live

# List all routes (from project root)
python3 -c "
import sys
sys.path.insert(0, '.')
from fastapi_app.main import app
for route in app.routes:
    if hasattr(route, 'path') and hasattr(route, 'methods'):
        print(f'{route.path} - {route.methods}')
"
```

## Code Style

- **Formatter**: Ruff with tabs, double quotes, 110 char line length
- **Linters**: Ruff (Python), ESLint (JavaScript), Prettier
- **Python target**: 3.11+
- Run `pre-commit run --all-files` to check formatting

## Architecture

```
memora_admin/
├── memora_admin/memora_admin/         # Frappe module
│   ├── doctype/                       # 31 DocTypes (content, players, analytics)
│   ├── api/                           # Frappe API endpoints
│   └── events/                        # Frappe hooks (access_sync.py)
├── fastapi_app/                       # FastAPI sidecar
│   ├── main.py                        # App entry, lifespan, Redis pool
│   ├── api/v1/endpoints/              # Route handlers
│   ├── services/                      # Business logic (progress, access)
│   ├── models/                        # Pydantic schemas
│   └── core/                          # Config, security, logging
└── .planning/                         # Project docs and roadmap
```

### DocType Structure

Each DocType follows:
```
doctype/memora_{entity}/
├── memora_{entity}.py      # Document class (inherits Document)
├── memora_{entity}.json    # Schema definition
├── memora_{entity}.js      # Form handlers
└── test_memora_{entity}.py # Unit tests
```

Content hierarchy: Subject → Track → Unit → Topic → Lesson → Stage

### FastAPI Patterns

- **Services**: Business logic with Redis operations (`services/progress.py`, `services/access.py`)
- **Dependencies**: Injected via `Annotated` + `Depends`
- **Redis keys**: ALL keys MUST be defined in `fastapi_app/core/redis_keys.py`. Never use inline `f"memora:..."` strings — always import a key builder function. This is the single source of truth for key formats, TTL constants, and documentation.
- **TTL constants**: Defined in `fastapi_app/core/redis_keys.py` alongside key builders: `WALLET_KEY_TTL` (48h), `PROGRESS_KEY_TTL` (48h), `ACCESS_KEY_TTL` (24h), `PLAN_FREE_SUBJECTS_TTL` (12h). Lua scripts use literal values (cannot import Python constants) — cross-reference comments in `redis_keys.py` document which Lua scripts duplicate each value.
- **Logging**: Structured via `structlog`
- **CRITICAL: `decode_responses=True` by default**: The FastAPI Redis pool (`core/redis.py`) and the standard Frappe-side client (`get_memora_redis()`) use `decode_responses=True`. Treat Redis responses as **strings** for normal GET/HGETALL flows, and do NOT use `.encode()` on keys when doing lookups against decoded data. This caused a recurring bug in `profile_page.py` activity endpoint. The only intended exception is binary payload access (for example, progress bitmap `GET` in sync tasks), which must use a dedicated raw client (`get_memora_redis_raw()`) because bitmap bytes are not valid UTF-8.
- **Sync tasks must MERGE, not REPLACE**: When syncing Redis data to MariaDB (e.g., `daily_xp_json`), always merge with existing DB values. Redis may have sparse data after a flush; replacing would destroy historical data.

### Frappe Redis Connection

All Frappe-side code (background tasks, API endpoints, event handlers) that accesses Memora's dedicated Redis should use `get_memora_redis()` from `memora_admin.utils.redis_connection` by default:

```python
from memora_admin.utils.redis_connection import get_memora_redis
r = get_memora_redis()  # Returns redis.Redis with decode_responses=True
```

- Reads `redis_memora` from `frappe.conf` (site_config.json)
- Falls back to `frappe.conf.redis_cache` if `redis_memora` is not configured (backward compat)
- Use `get_memora_redis_raw()` only for binary-safe reads where `decode_responses=True` would fail (for example, bitmap `GET`); keep that exception narrow and explicit
- Do NOT use `frappe.conf.redis_cache` directly for Memora data — that points to Frappe's cache Redis (port 13000)
- Event handlers using `get_fastapi_redis()` (reads `.env` REDIS_URL) auto-pick up port 13001 — no changes needed

### Access Control (Double-Gate)

1. **Gate 1**: Season validation (status + end_ts via Redis hash)
2. **Gate 2**: Player access set check (Redis SADD for grants)

### Frappe Hooks

Events in `memora_admin/events/access_sync.py`:
- `on_season_updated`: Syncs season metadata to Redis
- `on_subscription_change`: Manages access grants (SADD/SREM)

## Redis Architecture (Dual-Instance)

Memora uses a **dedicated Redis instance** on port 13001, separate from Frappe's cache on port 13000:

| Instance | Port | Purpose | `bench clear-cache` impact |
|----------|------|---------|---------------------------|
| Frappe Redis | 13000 | Frappe cache, sessions, queue | FLUSHDB — wipes all keys |
| **Memora Redis** | **13001** | Game data, wallets, progress, leaderboards | **No impact** — isolated |

**Connection points**:
- FastAPI: Reads `REDIS_URL` from `.env` → `redis://127.0.0.1:13001`
- Frappe tasks/API: Uses `get_memora_redis()` from `memora_admin.utils.redis_connection`
- Frappe event handlers using `get_fastapi_redis()`: Reads `.env` REDIS_URL (auto-picks up 13001)

**AOF persistence**: Enabled with `appendfsync everysec` — data survives Redis restart with max 1-second data loss window.

**Eviction policy**: `volatile-ttl` — only evicts keys that have a TTL set. Protected keys (dirty sets, buffer, alltime leaderboard) have no TTL and are never evicted.

## Redis Resilience (Cache-Miss Self-Healing)

Redis is a **hot cache**, MariaDB is source of truth. After restart/eviction, all services auto-hydrate:

| Redis Key | Source of Truth | TTL | Self-heals? |
|-----------|----------------|-----|-------------|
| `memora:access:{player}` | `Memora Player Subscription` | 24h | Yes - `AccessService.ensure_hydrated()` on API call |
| `memora:progress:{user}:{subj}:v{ver}` | `Memora Structure Progress` | 48h | Yes - `ProgressService.ensure_hydrated()` on API call |
| `memora:hierarchy:{subject}` | Frappe hierarchy API | 1h | Yes - fetched on cache miss |
| `memora:subjects_with_free_content` | Hierarchy fetch | None | Yes - auto-repaired when hierarchy fetched from Frappe |
| `memora:plan:{plan}:free_subjects` | `Memora Plan Subject` | 12h | Periodic - `plan_sync.py` every 6h + event hooks |
| `memora:wallet:{player}` | `Memora Player Profile` | 48h | Yes - `WalletService` on API call |
| `memora:stats:{user}:{subj}:v{ver}` | Computed from bitmap | 1h | Yes - cold-start recompute |
| `memora:dirty:progress` | N/A (buffer) | **None** | **Protected** — never evicted |
| `memora:dirty:wallets` | N/A (buffer) | **None** | **Protected** — never evicted |
| `memora:buffer:interactions` | N/A (buffer) | **None** | **Protected** — never evicted |
| `memora:lb:alltime*` | N/A (rankings) | **None** | **Protected** — never evicted |

**Key rules when adding new Redis-cached data:**
1. Always implement `ensure_hydrated()` pattern - fetch from MariaDB on cache miss
2. Ensure `FrappeClient` is injected via `deps.py` (hydration silently skips without it)
3. When adding denormalized fields (e.g., `free_units`), verify ALL producer code paths populate them
4. The hierarchy API (`memora_admin/api/hierarchy.py`) must populate `free_units`/`free_topics` arrays
5. **Cross-cache dependencies**: When data from one DocType feeds into another cache (e.g., Plan Subject `meta_data` → hierarchy cache `free_units`/`free_topics`), ensure the event hook invalidates ALL affected caches, not just the "obvious" one
6. **TTL on writes**: All cacheable keys must have TTL set via `EXPIRE` after writes. Import TTL constants from `fastapi_app.core.redis_keys`. Lua scripts use literal values with cross-reference comments.
7. **Protected keys** (dirty sets, buffer, alltime leaderboard) must NEVER receive TTL — their loss means permanent data loss

### Cache Invalidation Events (`build_trigger.py`)

| Event | Invalidates | Notes |
|-------|------------|-------|
| Content update (Subject/Track/Unit/Topic/Lesson) | Hierarchy cache + plan builds | `on_content_updated` |
| Plan Subject changed (is_premium, meta_data, etc.) | Plan cache + **hierarchy cache** | `on_plan_subject_changed` - hierarchy invalidated because `free_units`/`free_topics` are derived from Plan Subject `meta_data` |
| Plan updated | Plan cache | `on_plan_updated` |
| Plan Overrider changed | Plan cache | `on_plan_overrider_changed` |
| Product Grant changed | Catalog cache | `catalog_sync.py` |

### Stats Cache Staleness (Content Hash)

**Known issue:** When lessons are added/removed, per-user stats caches (`memora:stats:{user}:{subject}:v{version}`) retain stale totals until TTL expires (1h). The `completion_percentage` in MariaDB is also stale.

**Planned fix (PRD: `.planning/prd/stats-hash-staleness.md`):** Embed a `_content_hash` field in the stats hash derived from hierarchy structure. On read, compare with current hierarchy's `content_hash`. Mismatch triggers lazy recompute (~4ms). Zero writes on content change — fully scalable at 100k+ users.

**Key rules for implementation:**
- `content_hash` must be deterministic and change IFF stats totals would change
- Hash only structural fields (bit_range, excluded_bits, lesson IDs, bit_indices) — NOT `is_linear`, `xp`, etc.
- HINCRBY warm path (lesson completion) must NOT be modified
- Pre-migration stats without `_content_hash` must self-heal (mismatch → recompute)
- When hierarchy version bumps, content hash is redundant but harmless (both mechanisms coexist)

### Free Content Access Model

A **premium subject** (`is_premium=1`) can contain **individual free topics/units** as samples. The hierarchy API reads `free_units`/`free_topics` from Plan Subject `meta_data` across ALL Plan Subject records (regardless of `is_premium` flag). The progress endpoint allows access if `hierarchy.has_any_free_content()` is true, even without explicit grants.

## Performance Targets

- Access check: <2ms
- Progress fetch: <20ms
- Stage complete: <10ms
- Lesson complete: <30ms

## Environment Configuration

Copy `.env.example` to `.env`:
```
REDIS_URL=redis://127.0.0.1:13001
JWT_SECRET=your-secret-key
BITMAP_JSON_PATH=/path/to/bitmaps
```

Also add `redis_memora` to Frappe site config:
```bash
bench --site your-site set-config redis_memora "redis://127.0.0.1:13001"
```

## Planning Documents

- `.planning/PROJECT.md` - Project vision and requirements
- `.planning/ROADMAP.md` - Implementation roadmap
- `.planning/codebase/` - Architecture, stack, conventions docs

## Active Technologies
- Python 3.11+ (Frappe v15) + Frappe Framework (ORM, background jobs, hooks), MariaDB (001-voucher-batch-fixes)
- MariaDB via Frappe ORM, direct SQL for bulk updates (001-voucher-batch-fixes)
- Python 3.11+ (Frappe v15) + Frappe Framework (ORM, `frappe.tests.utils.FrappeTestCase`, background jobs) (002-voucher-test-infra)
- MariaDB via Frappe ORM; `tabSeries` for atomic serial reservation (002-voucher-test-infra)
- Python 3.11+ (Frappe v15) + Frappe Framework (`frappe.tests.utils.FrappeTestCase`), `cryptography 3.4.8` (Fernet, HKDF) (003-crypto-generator-tests)
- MariaDB via Frappe ORM (serial reservation only; all other tests are DB-free) (003-crypto-generator-tests)
- Python 3.11+ (Frappe v15) + Frappe Framework (`frappe.tests.utils.FrappeTestCase`), `decimal.Decimal`, ERPNext Sales Invoice (004-commission-invoice-tests)
- MariaDB via Frappe ORM (for resolution and invoice tests); N/A for pure commission math tests (004-commission-invoice-tests)
- Python 3.11+ (Frappe v15) + Frappe Framework (`frappe.tests.utils.FrappeTestCase`), `cryptography 3.4.8` (Fernet/HKDF for export verification) (005-batch-lifecycle-tests)
- MariaDB via Frappe ORM (card records, batch state, export audit log) (005-batch-lifecycle-tests)
- Python 3.11+ (Frappe v15) + Frappe Framework (`frappe.tests.utils.FrappeTestCase`), ERPNext Sales Invoice, `decimal.Decimal` (006-allocation-flow-tests)
- MariaDB via Frappe ORM (card records, batch state, allocation state, Sales Invoice) (006-allocation-flow-tests)
- Python 3.11+ (Frappe v15) + Frappe Framework (`frappe.tests.utils.FrappeTestCase`), `hmac` module, `csv`/`io` (for PIN extraction) (007-redemption-flow-tests)
- MariaDB via Frappe ORM (card records, batch state, redemption logs, subscription transactions) (007-redemption-flow-tests)
- Python 3.11+ (Frappe v15) + Frappe Framework (`frappe.tests.utils.FrappeTestCase`), `hmac`, `decimal.Decimal`, `csv`/`io`, ERPNext Sales Invoice (008-voucher-audit-tests)
- Python 3.11+ (Frappe v15 bench environment) + pytest 8.4.2, pytest-asyncio 0.26.0, httpx 0.28.1, redis.asyncio (all pre-installed) (009-fastapi-test-foundation)
- Redis at `redis://127.0.0.1:13001` (dedicated Memora instance) (009-fastapi-test-foundation)
- Python 3.11+ (Frappe v15 bench environment) + pytest 8.4.2, pytest-asyncio 0.26.0, redis.asyncio, unittest.mock.AsyncMock (010-core-service-tests)
- Redis at `redis://127.0.0.1:13001` (dedicated Memora instance), MariaDB via mocked FrappeClient (010-core-service-tests)
- Python 3.11+ (Frappe v15 bench environment) + pytest 8.4.2, pytest-asyncio 0.26.0, redis.asyncio, unittest.mock.AsyncMock, user-agents (for DeviceService fingerprinting) (011-session-auth-tests)
- Redis at `redis://127.0.0.1:13001` (dedicated Memora instance) (013-core-endpoint-tests)
- Redis at `redis://127.0.0.1:13001` (real, shared with Frappe -- prefix isolation mandatory) (014-remaining-endpoint-tests)
- Python 3.11+ (Frappe v15 bench environment) + Frappe Framework (`frappe.tests.utils.FrappeTestCase`), `redis` (synchronous), `unittest.mock` (016-sync-task-tests)
- MariaDB via Frappe ORM (Player Wallet, Structure Progress, Interaction Log, Sync Log); Redis at `redis://127.0.0.1:13001` (dirty sets, wallet hashes, progress bitmaps, interaction buffer) (016-sync-task-tests)
- Python 3.11+ (Frappe v15 bench environment) + Frappe Framework (ORM-blocked, raw SQL only), `fsrs` 6.3.0 (FSRS library), `redis` (synchronous, for background processor) (018-fsrs-card-state)
- MariaDB 10.6 via `frappe.db.sql()` (RANGE-partitioned `tabMemora Memory State`), Redis at `redis://127.0.0.1:13001` (card state cache) (018-fsrs-card-state)
- Python 3.11+ (Frappe v15 bench environment) + Frappe Framework (ORM, whitelist API), FastAPI, Pydantic v2, `redis.asyncio`, `hashlib` (stdlib) (019-stats-content-hash)
- Redis at `redis://127.0.0.1:13001` (stats hash, hierarchy JSON cache), MariaDB via Frappe ORM (hierarchy source data) (019-stats-content-hash)
- Python 3.11+ (Frappe v15) + Frappe Framework (ORM, whitelist API), `csv` (stdlib), `io` (stdlib) (020-fix-export-redeemed-cards)
- MariaDB via Frappe ORM (card status lookup), encrypted file on disk (PIN source) (020-fix-export-redeemed-cards)
- Python 3.11+ (Frappe v15) + Frappe Framework (ORM, whitelist API, background jobs), `requests` (HTTP client, already available) (021-cdn-cache-purge)
- MariaDB via Frappe ORM (Memora Settings singleton), no new tables (021-cdn-cache-purge)
- Python 3.11+ (Frappe v15 bench environment) + FastAPI, Starlette (`BaseHTTPMiddleware`), `redis.asyncio`, `structlog` (022-global-rate-limiting)
- Redis at `redis://127.0.0.1:13001` (dedicated Memora instance) (022-global-rate-limiting)
- Python 3.11+ (Frappe v15 bench environment) + Frappe Framework (ORM, Single DocType, hooks), FastAPI, `redis.asyncio`, `structlog` (023-dynamic-level-system)
- MariaDB via Frappe ORM (Level Settings DocType), Redis at `redis://127.0.0.1:13001` (config cache) (023-dynamic-level-system)
- Python 3.11+ (Frappe v15 bench environment) + Frappe Framework (ORM, DocType, hooks), FastAPI, Pydantic v2, redis.asyncio (024-review-item-table)
- MariaDB via Frappe ORM (standard DocType — NOT partitioned) (024-review-item-table)
- Python 3.11+ (Frappe v15 bench environment) + FastAPI, Pydantic v2, redis.asyncio, structlog, Frappe Framework (ORM for Review Items, raw SQL for Practice Log) (025-practice-arena)
- MariaDB via Frappe ORM (Review Items) + raw SQL (Practice Log, ~500M rows), Redis at `redis://127.0.0.1:13001` (practice sessions, hierarchy cache) (025-practice-arena)
- Redis at `redis://127.0.0.1:13001` (ZSETs for rankings), MariaDB via Frappe ORM (player profiles, academic plans — read-only for this feature) (026-plan-leaderboard)
- Python 3.11+ (Frappe v15 bench environment) + FastAPI, redis.asyncio (FastAPI side), redis (Frappe sync side), Frappe Framework (ORM, hooks, scheduled jobs), structlog (027-redis-hardening)
- Redis at `redis://127.0.0.1:13001` (dedicated Memora instance), MariaDB via Frappe ORM (source of truth) (027-redis-hardening)
- Python 3.11+ (Frappe v15 bench environment) + FastAPI, Frappe Framework (ORM, whitelist API, hooks), redis.asyncio (FastAPI), redis (Frappe sync tasks), Pydantic v2, structlog (028-player-plan-change)
- MariaDB via Frappe ORM (source of truth), Redis at `redis://127.0.0.1:13001` (hot cache) (028-player-plan-change)
- Python 3.11+ (Frappe v15 bench environment) + FastAPI, redis.asyncio, pydantic-settings, httpx, structlog, asyncio (029-concurrency-scaling)
- Redis at `redis://127.0.0.1:13001` (dedicated Memora instance), MariaDB via Frappe ORM (unchanged) (029-concurrency-scaling)
- Python 3.11+ + Locust (load testing framework), httpx or Locust built-in HTTP client (030-locust-load-tests)
- N/A (test suite only; reads config from Python file) (030-locust-load-tests)
- Python 3.11+ (Frappe v15 bench environment) + FastAPI, redis.asyncio, Pydantic v2, structlog, asyncio (031-large-data-perf-fixes)
- Redis at `redis://127.0.0.1:13001` (dedicated Memora instance) — no schema changes (031-large-data-perf-fixes)
- Python 3.11+ (Frappe v15 bench environment) + Frappe Framework (ORM, DocType, hooks), FastAPI, redis.asyncio, Pydantic v2, structlog (032-admin-announcements)
- Python 3.11+ (Frappe v15 bench environment) + FastAPI, redis.asyncio, structlog, Frappe Framework (for backfill command) (033-dense-rank-tier-index)
- Python 3.11+ (Frappe v15) + Frappe Framework (ORM, DocTypes, hooks, Script Reports), ERPNext (Sales Invoice — unaffected) (034-scholarship-gift-vouchers)
- MariaDB via Frappe ORM (existing tables extended with new fields) (034-scholarship-gift-vouchers)

## Test Environment Configuration

**⚠️ TEST ENVIRONMENT ONLY**: The following are real database records used for testing:
- **Existing Season ID**: `SEAS-00027` (use this for voucher test infrastructure)
  - Reason: Test fixtures that create new seasons encounter MySQL partitioning constraints
  - Solution: Reuse existing season instead of creating new ones
  - Used in: `memora_admin/memora_admin/tests/test_voucher_quickstart.py`

Example usage in tests:
```python
from memora_admin.memora_admin.tests.voucher_fixtures import make_product_grant, make_player

# Use existing test season instead of creating new one
grant = make_product_grant(season="SEAS-00027")
player = make_player(season="SEAS-00027")
```

## Recent Changes
- 034-scholarship-gift-vouchers: Added Python 3.11+ (Frappe v15) + Frappe Framework (ORM, DocTypes, hooks, Script Reports), ERPNext (Sales Invoice — unaffected)
- 033-dense-rank-tier-index: Added Python 3.11+ (Frappe v15 bench environment) + FastAPI, redis.asyncio, structlog, Frappe Framework (for backfill command)
- 032-admin-announcements: Added Python 3.11+ (Frappe v15 bench environment) + Frappe Framework (ORM, DocType, hooks), FastAPI, redis.asyncio, Pydantic v2, structlog

## Important Notes for dev
- this project must handle 100k concurrent users
