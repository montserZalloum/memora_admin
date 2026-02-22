# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Memora Admin is a gamified educational platform backend for Arabic-speaking students. It uses a **dual architecture**:
- **Frappe v15**: Admin panel, content management, ORM, 31 DocTypes
- **FastAPI sidecar**: High-performance game API (sub-20ms responses)

Key data stores: Redis (hot data: progress bitmaps, wallets, sessions) + MariaDB (cold data via Frappe).

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
- **Redis keys**: Prefixed with `memora:` (e.g., `memora:progress:{user_id}:{subject_id}:v{version}`)
- **Logging**: Structured via `structlog`
- **CRITICAL: `decode_responses=True`**: The Redis pool (`core/redis.py`) uses `decode_responses=True`. ALL Redis responses are **strings**, NEVER bytes. Do NOT use `.encode()` on keys when doing lookups against HGETALL/GET results. This caused a recurring bug in `profile_page.py` activity endpoint.
- **Sync tasks must MERGE, not REPLACE**: When syncing Redis data to MariaDB (e.g., `daily_xp_json`), always merge with existing DB values. Redis may have sparse data after a flush; replacing would destroy historical data.

### Access Control (Double-Gate)

1. **Gate 1**: Season validation (status + end_ts via Redis hash)
2. **Gate 2**: Player access set check (Redis SADD for grants)

### Frappe Hooks

Events in `memora_admin/events/access_sync.py`:
- `on_season_updated`: Syncs season metadata to Redis
- `on_subscription_change`: Manages access grants (SADD/SREM)

## Redis Resilience (Cache-Miss Self-Healing)

Redis is a **hot cache**, MariaDB is source of truth. After FLUSHDB/restart/eviction, all services auto-hydrate:

| Redis Key | Source of Truth | Self-heals? |
|-----------|----------------|-------------|
| `memora:access:{player}` | `Memora Player Subscription` | Yes - `AccessService.ensure_hydrated()` on API call |
| `memora:progress:{user}:{subj}:v{ver}` | `Memora Structure Progress` | Yes - `ProgressService.ensure_hydrated()` on API call |
| `memora:hierarchy:{subject}` | Frappe hierarchy API | Yes - fetched on cache miss (1h TTL) |
| `memora:subjects_with_free_content` | Hierarchy fetch | Yes - auto-repaired when hierarchy fetched from Frappe |
| `memora:plan:{plan}:free_subjects` | `Memora Plan Subject` | Periodic - `plan_sync.py` every 6h + event hooks |
| `memora:wallet:{player}` | `Memora Player Profile` | Yes - `WalletService` on API call |
| `memora:stats:{user}:{subj}:v{ver}` | Computed from bitmap | Yes - cold-start recompute |

**Key rules when adding new Redis-cached data:**
1. Always implement `ensure_hydrated()` pattern - fetch from MariaDB on cache miss
2. Ensure `FrappeClient` is injected via `deps.py` (hydration silently skips without it)
3. When adding denormalized fields (e.g., `free_units`), verify ALL producer code paths populate them
4. The hierarchy API (`memora_admin/api/hierarchy.py`) must populate `free_units`/`free_topics` arrays
5. **Cross-cache dependencies**: When data from one DocType feeds into another cache (e.g., Plan Subject `meta_data` → hierarchy cache `free_units`/`free_topics`), ensure the event hook invalidates ALL affected caches, not just the "obvious" one

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
REDIS_URL=redis://localhost:6379/0
JWT_SECRET=your-secret-key
BITMAP_JSON_PATH=/path/to/bitmaps
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
- Redis at `redis://127.0.0.1:13000` (shared with Frappe — prefix isolation required) (009-fastapi-test-foundation)
- Python 3.11+ (Frappe v15 bench environment) + pytest 8.4.2, pytest-asyncio 0.26.0, redis.asyncio, unittest.mock.AsyncMock (010-core-service-tests)
- Redis at `redis://127.0.0.1:13000` (real, prefix-isolated), MariaDB via mocked FrappeClient (010-core-service-tests)
- Python 3.11+ (Frappe v15 bench environment) + pytest 8.4.2, pytest-asyncio 0.26.0, redis.asyncio, unittest.mock.AsyncMock, user-agents (for DeviceService fingerprinting) (011-session-auth-tests)
- Redis at `redis://127.0.0.1:13000` (real, shared with Frappe — prefix isolation mandatory) (013-core-endpoint-tests)
- Redis at `redis://127.0.0.1:13000` (real, shared with Frappe -- prefix isolation mandatory) (014-remaining-endpoint-tests)
- Python 3.11+ (Frappe v15 bench environment) + Frappe Framework (`frappe.tests.utils.FrappeTestCase`), `redis` (synchronous), `unittest.mock` (016-sync-task-tests)
- MariaDB via Frappe ORM (Player Wallet, Structure Progress, Interaction Log, Sync Log); Redis at `redis://127.0.0.1:13000` (dirty sets, wallet hashes, progress bitmaps, interaction buffer) (016-sync-task-tests)
- Python 3.11+ (Frappe v15 bench environment) + Frappe Framework (ORM-blocked, raw SQL only), `fsrs` 6.3.0 (FSRS library), `redis` (synchronous, for background processor) (018-fsrs-card-state)
- MariaDB 10.6 via `frappe.db.sql()` (RANGE-partitioned `tabMemora Memory State`), Redis at `redis://127.0.0.1:13000` (card state cache) (018-fsrs-card-state)
- Python 3.11+ (Frappe v15 bench environment) + Frappe Framework (ORM, whitelist API), FastAPI, Pydantic v2, `redis.asyncio`, `hashlib` (stdlib) (019-stats-content-hash)
- Redis at `redis://127.0.0.1:13000` (stats hash, hierarchy JSON cache), MariaDB via Frappe ORM (hierarchy source data) (019-stats-content-hash)
- Python 3.11+ (Frappe v15) + Frappe Framework (ORM, whitelist API), `csv` (stdlib), `io` (stdlib) (020-fix-export-redeemed-cards)
- MariaDB via Frappe ORM (card status lookup), encrypted file on disk (PIN source) (020-fix-export-redeemed-cards)
- Python 3.11+ (Frappe v15) + Frappe Framework (ORM, whitelist API, background jobs), `requests` (HTTP client, already available) (021-cdn-cache-purge)
- MariaDB via Frappe ORM (Memora Settings singleton), no new tables (021-cdn-cache-purge)
- Python 3.11+ (Frappe v15 bench environment) + FastAPI, Starlette (`BaseHTTPMiddleware`), `redis.asyncio`, `structlog` (022-global-rate-limiting)
- Redis at `redis://127.0.0.1:13000` (shared with Frappe -- prefix isolation required) (022-global-rate-limiting)
- Python 3.11+ (Frappe v15 bench environment) + Frappe Framework (ORM, Single DocType, hooks), FastAPI, `redis.asyncio`, `structlog` (023-dynamic-level-system)
- MariaDB via Frappe ORM (Level Settings DocType), Redis at `redis://127.0.0.1:13000` (config cache) (023-dynamic-level-system)

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
- 023-dynamic-level-system: Added Python 3.11+ (Frappe v15 bench environment) + Frappe Framework (ORM, Single DocType, hooks), FastAPI, `redis.asyncio`, `structlog`
- 022-global-rate-limiting: Added Python 3.11+ (Frappe v15 bench environment) + FastAPI, Starlette (`BaseHTTPMiddleware`), `redis.asyncio`, `structlog`
- 021-cdn-cache-purge: Added Python 3.11+ (Frappe v15) + Frappe Framework (ORM, whitelist API, background jobs), `requests` (HTTP client, already available)

## Important Notes for dev
- this project must handle 100k concurrent users
