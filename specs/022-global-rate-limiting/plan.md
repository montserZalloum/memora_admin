# Implementation Plan: Global API Rate Limiting

**Branch**: `022-global-rate-limiting` | **Date**: 2026-02-22 | **Spec**: [spec.md](spec.md)
**Input**: Feature specification from `/specs/022-global-rate-limiting/spec.md`

## Summary

Add three layers of rate limiting to the FastAPI sidecar: (1) a global per-IP middleware protecting all endpoints except health checks and payment webhooks, (2) per-player rate limits on write-heavy endpoints (reviews, session start/end), and (3) concurrent WebSocket connection limits per player. All layers use the existing Redis + Lua script pattern, fail open on Redis unavailability, and add <2ms p99 latency.

## Technical Context

**Language/Version**: Python 3.11+ (Frappe v15 bench environment)
**Primary Dependencies**: FastAPI, Starlette (`BaseHTTPMiddleware`), `redis.asyncio`, `structlog`
**Storage**: Redis at `redis://127.0.0.1:13000` (shared with Frappe -- prefix isolation required)
**Testing**: pytest 8.4.2, pytest-asyncio 0.26.0, httpx 0.28.1, redis.asyncio (all pre-installed)
**Target Platform**: Linux server (behind nginx reverse proxy)
**Project Type**: Single (FastAPI sidecar within Frappe bench)
**Performance Goals**: Rate limit check <2ms p99 per request (single Redis round-trip via Lua script)
**Constraints**: No new infrastructure; reuse existing Redis instance and Lua script patterns
**Scale/Scope**: 100k concurrent users; ~50 bytes per rate limit key with 60s TTL

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

| Principle | Status | Notes |
|-----------|--------|-------|
| I. Self-Healing Cache Architecture | PASS | Rate limit counters are ephemeral (60s TTL). No MariaDB persistence needed. Fail-open on Redis unavailability prevents self-DOS. |
| II. Sub-20ms Game API Performance | PASS | FR-010 requires <2ms. Single Lua script round-trip (INCR + conditional EXPIRE). Same pattern as existing `RateLimiter`. |
| III. Content Hierarchy Integrity | N/A | Rate limiting does not modify content hierarchy. |
| IV. Double-Gate Access Control | N/A | Rate limiting is a pre-gate check; does not alter access control. |
| V. Cryptographic Voucher Security | N/A | No voucher operations affected. |
| VI. Financial Precision | N/A | No monetary calculations involved. |
| VII. Auditable State Machines | N/A | No state machine transitions involved. |
| VIII. Test-First Coverage | PASS | All three layers include test specifications. Real Redis testing per constitution requirement. |

**Gate result**: All principles pass or N/A. No violations.

## Project Structure

### Documentation (this feature)

```text
specs/022-global-rate-limiting/
├── plan.md              # This file
├── research.md          # Phase 0 output
├── data-model.md        # Phase 1 output
├── quickstart.md        # Phase 1 output
├── contracts/           # Phase 1 output
│   └── rate-limit-api.md
└── tasks.md             # Phase 2 output (via /speckit.tasks)
```

### Source Code (repository root)

```text
fastapi_app/
├── middleware/
│   ├── request_id.py              # Existing (unchanged)
│   └── rate_limit.py              # NEW: GlobalRateLimitMiddleware
├── services/
│   ├── rate_limit.py              # Existing RateLimiter (unchanged)
│   └── global_rate_limit.py       # NEW: GlobalRateLimiter service
├── api/
│   ├── deps.py                    # MODIFIED: add per-player rate limit dependencies
│   └── v1/endpoints/
│       ├── reviews.py             # MODIFIED: add per-player rate limit dependency
│       └── sessions.py            # MODIFIED: add per-player rate limit dependency
├── core/
│   ├── config.py                  # MODIFIED: add rate limit settings
│   └── ws_manager.py              # MODIFIED: add connection limit enforcement
├── main.py                        # MODIFIED: register GlobalRateLimitMiddleware
└── tests/
    ├── conftest.py                # MODIFIED: add rate limit cleanup patterns
    ├── test_rate_limiter.py       # Existing (unchanged)
    └── test_global_rate_limit.py  # NEW: tests for all 3 rate limit layers
```

**Structure Decision**: All new code lives within the existing `fastapi_app/` structure. One new middleware file, one new service file, one new test file. Three existing files modified (deps.py, config.py, main.py) plus three endpoint files with dependency additions.
