# Implementation Plan: 100k Concurrency Scaling Optimizations

**Branch**: `029-concurrency-scaling` | **Date**: 2026-02-27 | **Spec**: [spec.md](./spec.md)
**Input**: Feature specification from `/specs/029-concurrency-scaling/spec.md`

## Summary

Scale the FastAPI sidecar to handle 100k concurrent users by making all scaling parameters configurable via environment variables, replacing N-command bitmap pipelines with single-fetch decode, parallelizing multi-subject lookups, implementing per-user WebSocket locks, and adding configurable rate limiter fail behavior. All changes are backward-compatible with zero-config development defaults.

## Technical Context

**Language/Version**: Python 3.11+ (Frappe v15 bench environment)
**Primary Dependencies**: FastAPI, redis.asyncio, pydantic-settings, httpx, structlog, asyncio
**Storage**: Redis at `redis://127.0.0.1:13001` (dedicated Memora instance), MariaDB via Frappe ORM (unchanged)
**Testing**: pytest 8.4.2, pytest-asyncio 0.26.0, redis.asyncio (real Redis, no mocking)
**Target Platform**: Linux server (Ubuntu 20.04+), uvicorn workers
**Project Type**: Backend API (FastAPI sidecar within Frappe bench)
**Performance Goals**: <20ms progress fetch, <2ms access check, <10ms stage complete, <30ms lesson complete
**Constraints**: `decode_responses=True` on Redis pool (binary data returns as text), `@lru_cache` frozen settings
**Scale/Scope**: 100k concurrent users, 4+ uvicorn workers, 200+ Redis connections per worker

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

### Pre-Research Check

| Principle | Status | Notes |
|-----------|--------|-------|
| I. Self-Healing Cache | PASS | No new Redis keys; bitmap decode preserves hydration pattern |
| II. Sub-20ms Game API | PASS | All changes reduce latency (single-fetch, parallel summary) |
| III. Content Hierarchy | PASS | Bitmap decode uses same MSB-first bit ordering as GETBIT |
| IV. Double-Gate Access | PASS | Access control untouched |
| V. Cryptographic Voucher | N/A | No voucher changes |
| VI. Financial Precision | N/A | No financial changes |
| VII. Auditable State Machines | N/A | No state machine changes |
| VIII. Test-First Coverage | PASS | All existing tests must pass; no new Redis keys to test |

### Post-Design Check

| Principle | Status | Notes |
|-----------|--------|-------|
| I. Self-Healing Cache | PASS | `ensure_hydrated()` unchanged, bitmap decode is read-path only |
| II. Sub-20ms Game API | PASS | 500-GETBIT pipeline → 1 GET command; parallel summary ~8x faster |
| III. Content Hierarchy | PASS | `latin-1` encode preserves exact byte values; MSB-first bit ordering matches Redis GETBIT semantics |
| IV. Double-Gate Access | PASS | No access control changes |
| VIII. Test-First Coverage | PASS | Bitmap decode tested for edge cases (empty, sparse, full); settings validated at startup |

**Gate result**: PASS — no violations.

## Project Structure

### Documentation (this feature)

```text
specs/029-concurrency-scaling/
├── plan.md              # This file
├── spec.md              # Feature specification
├── research.md          # Phase 0: research findings
├── data-model.md        # Phase 1: entity changes
├── quickstart.md        # Phase 1: quickstart guide
├── contracts/           # Phase 1: API/service contracts
│   ├── settings.yaml
│   ├── progress-service.yaml
│   ├── ws-manager.yaml
│   ├── progress-summary.yaml
│   └── rate-limiter.yaml
└── tasks.md             # Phase 2 output (via /speckit.tasks)
```

### Source Code (repository root)

```text
fastapi_app/
├── core/
│   ├── config.py            # Modified: 6 new settings fields
│   ├── redis.py             # Modified: configurable pool size + startup log
│   └── ws_manager.py        # Modified: per-user locks + parallel broadcast
├── api/v1/endpoints/
│   └── progress.py          # Modified: parallel progress summary
├── middleware/
│   └── rate_limit.py        # Modified: configurable fail behavior
├── services/
│   ├── progress.py          # Modified: single-fetch bitmap decode
│   └── frappe_client.py     # Modified: configurable timeout/limits
└── main.py                  # Modified: pass settings to middleware + ws_manager

.env.example                 # Modified: document new env vars
production.env.example       # New: recommended production values
```

**Structure Decision**: Existing FastAPI sidecar structure — all changes to existing files except `production.env.example` (new reference file per FR-012).

## Complexity Tracking

No constitution violations — table intentionally empty.

| Violation | Why Needed | Simpler Alternative Rejected Because |
|-----------|------------|-------------------------------------|
| — | — | — |
