# Implementation Plan: Redis Hardening

**Branch**: `027-redis-hardening` | **Date**: 2026-02-25 | **Spec**: [spec.md](spec.md)
**Input**: Feature specification from `/specs/027-redis-hardening/spec.md`

## Summary

Isolate Memora game data into a dedicated Redis instance (port 13001) separate from Frappe's cache (port 13000), add AOF persistence for crash recovery, apply TTLs to cacheable keys for bounded memory growth, and implement monitoring/alerting for proactive issue detection. This prevents `bench clear-cache` from wiping game data and ensures the system scales safely to 100k+ users.

## Technical Context

**Language/Version**: Python 3.11+ (Frappe v15 bench environment)
**Primary Dependencies**: FastAPI, redis.asyncio (FastAPI side), redis (Frappe sync side), Frappe Framework (ORM, hooks, scheduled jobs), structlog
**Storage**: Redis at `redis://127.0.0.1:13001` (dedicated Memora instance), MariaDB via Frappe ORM (source of truth)
**Testing**: pytest 8.4.2 + pytest-asyncio + httpx (FastAPI tests), FrappeTestCase (Frappe sync tests)
**Target Platform**: Linux server (Ubuntu 20.04+)
**Project Type**: Dual-architecture (Frappe admin + FastAPI sidecar)
**Performance Goals**: Sub-20ms game API responses, <2ms access checks. No regression from hardening changes.
**Constraints**: 128mb Redis memory (dev), `volatile-ttl` eviction, `appendfsync everysec` (1s max data loss)
**Scale/Scope**: 100k concurrent users, ~70 Redis key patterns, 4 Lua scripts to update, 15+ files to modify

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

### Pre-Design Check

| Principle | Status | Assessment |
|-----------|--------|------------|
| I. Self-Healing Cache (NON-NEGOTIABLE) | PASS | **Enhanced** — dedicated instance prevents accidental flushes. TTLs leverage existing `ensure_hydrated()` pattern. Protected keys (dirty sets, buffer) have no TTL and use `volatile-ttl` policy to avoid eviction. |
| II. Sub-20ms Game API Performance | PASS | No impact — same Redis operations on a different port. AOF `everysec` runs in background thread with zero command latency. TTL refresh adds one `EXPIRE` per pipeline (negligible). |
| III. Content Hierarchy Integrity | PASS | No changes to hierarchy structure, bitmap versioning, or bit indices. Hierarchy cache already has 1h TTL. |
| IV. Double-Gate Access Control | PASS | Access keys get 24h TTL (refreshed on hydration). `ensure_hydrated()` rebuilds from MariaDB on miss. No change to gate logic. |
| V. Cryptographic Voucher Security (NON-NEGOTIABLE) | N/A | No voucher code changes. |
| VI. Financial Precision | N/A | No financial code changes. |
| VII. Auditable State Machines | N/A | No state machine changes. |
| VIII. Test-First Coverage | PASS | All tests updated to use port 13001. Health endpoint and monitoring task tested. |

**Gate result**: PASS — no violations.

### Post-Design Check

| Principle | Status | Notes |
|-----------|--------|-------|
| I. Self-Healing Cache | PASS | TTL policy verified: dirty sets/buffer protected (no TTL). Wallet/progress/access self-heal via `ensure_hydrated()`. `volatile-ttl` evicts only keys with TTL. |
| II. Sub-20ms Performance | PASS | Health endpoint is a new non-critical path (<5ms). No changes to hot paths except adding `EXPIRE` in pipelines (amortized zero cost). Lua scripts updated atomically. |
| VIII. Test-First Coverage | PASS | Test conftest.py updated. Health endpoint tests added. Monitoring task integration test via Frappe. |

## Project Structure

### Documentation (this feature)

```text
specs/027-redis-hardening/
├── plan.md                          # This file
├── spec.md                          # Feature specification
├── research.md                      # Phase 0: Research findings
├── data-model.md                    # Phase 1: Data model
├── quickstart.md                    # Phase 1: Setup guide
├── contracts/
│   ├── health-redis.yaml            # Redis health endpoint OpenAPI
│   ├── monitoring-task.yaml         # Monitoring task contract
│   └── ttl-policy.yaml              # TTL policy for all keys
└── tasks.md                         # Phase 2 output (not yet created)
```

### Source Code (files to create/modify)

```text
# Infrastructure (new files — deployed manually, not in repo)
/etc/redis/redis-memora.conf                    # Redis config
/etc/systemd/system/redis-memora.service        # Systemd service

# FastAPI — New files
fastapi_app/models/health.py                    # RedisHealthReport Pydantic model

# FastAPI — Modified files
fastapi_app/api/v1/endpoints/health.py          # Add /health/redis endpoint
fastapi_app/core/redis_keys.py                  # Add TTL constants
fastapi_app/core/config.py                      # No change (reads from .env)
fastapi_app/core/redis.py                       # No change (reads from config)
fastapi_app/services/wallet.py                  # Add EXPIRE after writes + Lua TTL
fastapi_app/services/progress.py                # Add EXPIRE after writes
fastapi_app/services/access.py                  # Add EXPIRE after hydration
fastapi_app/services/game_session.py            # Add EXPIRE in SESSION_COMPLETE_SCRIPT Lua
fastapi_app/tests/conftest.py                   # Update port 13000 → 13001

# Frappe — New files
memora_admin/utils/redis_connection.py          # get_memora_redis() utility
memora_admin/tasks/redis_monitor.py             # Monitoring task
memora_admin/tasks/leaderboard_cleanup.py       # Leaderboard key cleanup task

# Frappe — Modified files
memora_admin/tasks/sync.py                      # Dynamic batch sizing + get_redis → get_memora_redis
memora_admin/tasks/leaderboard_reset.py         # get_redis → get_memora_redis
memora_admin/tasks/session_cleanup.py           # get_redis → get_memora_redis
memora_admin/tasks/streak_reset.py              # get_redis → get_memora_redis
memora_admin/events/access_sync.py              # Add EXPIRE to grant/plan operations
memora_admin/hooks.py                           # Add new scheduled jobs
memora_admin/tests/sync_test_base.py            # Update Redis connection

# Configuration (modified)
.env                                            # REDIS_URL → 13001
.env.example                                    # Update example
.env.notes                                      # Update notes
README.md                                       # Add deployment guide section
CLAUDE.md                                       # Update Redis architecture docs
```

**Structure Decision**: Existing dual-architecture (Frappe + FastAPI). No new projects or structural changes. All modifications fit within established patterns. New utility `get_memora_redis()` centralizes Frappe-side Redis connections.

## Complexity Tracking

No violations to justify — all changes align with existing architecture and constitution principles.
