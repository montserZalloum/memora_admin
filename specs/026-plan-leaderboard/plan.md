# Implementation Plan: Plan-Scoped Leaderboard

**Branch**: `026-plan-leaderboard` | **Date**: 2026-02-24 | **Spec**: [spec.md](spec.md)
**Input**: Feature specification from `/specs/026-plan-leaderboard/spec.md`

## Summary

Replace global leaderboards with plan-scoped leaderboards so students compete only against peers in the same academic plan (grade + major + season). Remove the all-time leaderboard type. Fix the top endpoint to return 20 entries (no pagination). Continue dual-writing to global Redis ZSETs as a data reserve. Plan is resolved from the JWT access token at zero additional cost.

## Technical Context

**Language/Version**: Python 3.11+ (Frappe v15 bench environment)
**Primary Dependencies**: FastAPI, Pydantic v2, redis.asyncio, structlog
**Storage**: Redis at `redis://127.0.0.1:13000` (ZSETs for rankings), MariaDB via Frappe ORM (player profiles, academic plans — read-only for this feature)
**Testing**: pytest 8.4.2, pytest-asyncio, httpx (AsyncClient), redis.asyncio (real Redis, prefix-isolated)
**Target Platform**: Linux server (FastAPI sidecar on port 8002)
**Project Type**: Web (FastAPI backend only — no frontend changes)
**Performance Goals**: <20ms for both top and my-rank endpoints at 100k concurrent users
**Constraints**: Single Redis pipeline per XP award (1 RTT), Asia/Amman timezone for all date calculations
**Scale/Scope**: ~100k concurrent students, ~50-200 plans per season, ~5-10 subjects per plan

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

### Pre-Research Check

| Principle | Status | Notes |
|-----------|--------|-------|
| I. Self-Healing Cache | PASS | Plan-scoped ZSETs are ephemeral (48h/8d TTL). No MariaDB source of truth for rankings — rankings are derived from real-time XP writes. Loss = empty leaderboard until students earn XP again. This is acceptable for a competitive ranking (not student progress). |
| II. Sub-20ms Performance | PASS | Read path: 1-3 Redis pipelines (same as current). Write path: same pipeline, ~4 more ZINCRBY commands. No MariaDB in hot path. |
| III. Content Hierarchy | N/A | Leaderboards don't modify content structure. |
| IV. Double-Gate Access | N/A | Leaderboards are read-only views of earned XP, not content access. |
| V. Crypto Voucher Security | N/A | No voucher involvement. |
| VI. Financial Precision | N/A | XP is integer, no monetary calculations. |
| VII. Auditable State Machines | N/A | ZSETs are stateless accumulators, no lifecycle. |
| VIII. Test-First Coverage | PASS | Tests planned for service methods (unit), Redis isolation (integration), and endpoint contracts (endpoint). |

### Post-Design Check

| Principle | Status | Notes |
|-----------|--------|-------|
| I. Self-Healing Cache | PASS | Redis key builders in `redis_keys.py`. No `ensure_hydrated()` needed — leaderboard data is transient by nature. Global keys archived as before. |
| II. Sub-20ms Performance | PASS | Pipeline analysis: write adds 4-8 commands to existing pipeline (still 1 RTT). Read unchanged (1-3 RTT). No new MariaDB calls. |
| VIII. Test-First Coverage | PASS | See quickstart.md testing strategy. |

## Project Structure

### Documentation (this feature)

```text
specs/026-plan-leaderboard/
├── plan.md              # This file
├── spec.md              # Feature specification
├── research.md          # Phase 0: research decisions
├── data-model.md        # Phase 1: data model
├── quickstart.md        # Phase 1: implementation guide
├── contracts/
│   └── leaderboard-api.yaml  # Phase 1: OpenAPI contract
└── tasks.md             # Phase 2 output (created by /speckit.tasks)
```

### Source Code (repository root)

```text
fastapi_app/
├── core/
│   └── redis_keys.py          # Add 4 plan-scoped key builder functions
├── services/
│   └── leaderboard.py         # Modify: add plan_id param, plan-scoped read/write, remove alltime read
├── models/
│   └── leaderboard.py         # Modify: update type enum, make rank nullable
└── api/v1/endpoints/
    ├── leaderboard.py         # Modify: pass user.plan, remove limit param, restrict lb_type
    └── sessions.py            # Modify: pass user.plan to update_leaderboards() call (parameter-only)

tests/fastapi_app/
└── test_leaderboard.py        # New: unit + integration + endpoint tests
```

**Structure Decision**: All changes are within the existing FastAPI sidecar. No new files except tests. No Frappe-side changes needed — player profile and plan data are read-only via JWT token.
