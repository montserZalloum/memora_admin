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
- **Python target**: 3.10+
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
