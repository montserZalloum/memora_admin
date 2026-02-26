# Implementation Plan: Player Plan Change

**Branch**: `028-player-plan-change` | **Date**: 2026-02-26 | **Spec**: specs/028-player-plan-change/spec.md
**Input**: Feature specification from `/specs/028-player-plan-change/spec.md`

## Summary

Enable players to self-serve a plan change (expired season or voluntary) with a complete clean slate. Two-phase architecture: FastAPI endpoint orchestrates Redis freeze/cleanup, Frappe whitelisted API handles atomic DB transaction (snapshot, delete, reset, update, history insert). New DocType `Memora Player Plan History` captures pre-change state. Combined freeze key prevents race conditions with background sync jobs and in-flight sessions.

## Technical Context

**Language/Version**: Python 3.11+ (Frappe v15 bench environment)
**Primary Dependencies**: FastAPI, Frappe Framework (ORM, whitelist API, hooks), redis.asyncio (FastAPI), redis (Frappe sync tasks), Pydantic v2, structlog
**Storage**: MariaDB via Frappe ORM (source of truth), Redis at `redis://127.0.0.1:13001` (hot cache)
**Testing**: pytest + pytest-asyncio + httpx (FastAPI endpoints), FrappeTestCase (Frappe API)
**Target Platform**: Linux server (Frappe bench)
**Project Type**: Dual-architecture (Frappe admin + FastAPI sidecar)
**Performance Goals**: Plan change < 5s end-to-end (SC-004); post-change hot paths resume sub-20ms via cache self-healing
**Constraints**: 100k concurrent users, up to 1000 concurrent plan changes (SC-008), atomic DB transaction (FR-021), non-fatal cache cleanup (FR-022)
**Scale/Scope**: 2 new FastAPI endpoints, 1 new Frappe API module, 1 new DocType, 1 new FastAPI service, 2 new Redis key types, modifications to 5 existing files

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

| Principle | Relevant? | Compliance | Notes |
|-----------|-----------|------------|-------|
| I. Self-Healing Cache | Yes | PASS | Cache keys deleted during plan change; self-healing `ensure_hydrated()` restores data on next API call for new plan context. No Redis-only state created. |
| II. Sub-20ms Game API | Yes | PASS | Plan change is a one-time operation with 5s budget (not a hot path). All hot paths (progress, sessions, wallets) resume sub-20ms via cache re-hydration after change. |
| III. Content Hierarchy Integrity | Yes | PASS | Clean slate: new bitmaps created fresh for new plan's subjects on first access. Old bitmaps deleted. No bit_index reuse concerns (different subjects). |
| IV. Double-Gate Access Control | Yes | PASS | All access grants deleted (Gate 2 cleared). New subscriptions via new plan grant access. Season validation (Gate 1) uses new season automatically. |
| V. Cryptographic Voucher Security | No | N/A | No voucher operations in this feature. |
| VI. Financial Precision | No | N/A | No monetary calculations. |
| VII. Auditable State Machines | Yes | PASS | History record preserves complete pre-change state. Audit logs (interactions, redemptions, memory state, subscription transactions) untouched per FR-019. |
| VIII. Test-First Coverage | Yes | PASS | Tests required: Frappe API lifecycle (FrappeTestCase), FastAPI endpoints (pytest + httpx against real Redis), concurrency (concurrent plan change attempts). |

**Gate result**: PASS — no violations. No complexity tracking needed.

**Post-Phase 1 re-check**: PASS — data model and contracts maintain compliance. The freeze key is ephemeral (30s TTL) and does not introduce Redis-only state (Principle I). The plan change endpoint is not a hot path (Principle II). All new Redis keys follow the centralized `redis_keys.py` pattern.

## Project Structure

### Documentation (this feature)

```text
specs/028-player-plan-change/
├── plan.md              # This file
├── research.md          # Phase 0: design decisions and rationale
├── data-model.md        # Phase 1: entity schemas and relationships
├── quickstart.md        # Phase 1: setup and testing guide
├── contracts/           # Phase 1: API contracts
│   └── plan-change-api.yaml
└── tasks.md             # Phase 2 output (via /speckit.tasks)
```

### Source Code (repository root)

```text
memora_admin/
├── memora_admin/memora_admin/
│   ├── doctype/
│   │   └── memora_player_plan_history/           # NEW DocType
│   │       ├── memora_player_plan_history.json   # Schema (16 fields)
│   │       ├── memora_player_plan_history.py     # Document class
│   │       └── test_memora_player_plan_history.py
│   └── api/
│       └── plan_change.py                        # NEW Frappe whitelisted API
│                                                 #   execute_plan_change()
│                                                 #   get_available_plans()
├── fastapi_app/
│   ├── api/v1/endpoints/
│   │   └── plan_change.py                        # NEW endpoints
│   │                                             #   GET /plans/available
│   │                                             #   POST /plans/change
│   ├── services/
│   │   └── plan_change.py                        # NEW PlanChangeService
│   │                                             #   Redis cleanup orchestration
│   ├── models/
│   │   └── plan_change.py                        # NEW Pydantic models
│   └── core/
│       └── redis_keys.py                         # MODIFIED +2 key builders
│                                                 #   freeze_key(), plan_change_ts_key()
├── memora_admin/tasks/
│   └── sync.py                                   # MODIFIED freeze check
├── fastapi_app/api/v1/endpoints/
│   └── sessions.py                               # MODIFIED freeze check
├── fastapi_app/api/deps.py                       # MODIFIED +PlanChangeService dep
└── fastapi_app/api/v1/router.py                  # MODIFIED +plan_change router include
```

**Structure Decision**: Follows existing dual-architecture pattern. Frappe handles DB operations (DocType + whitelisted API), FastAPI handles client-facing endpoints and Redis orchestration. No new architectural patterns introduced.

## Architecture Overview

### Plan Change Flow

```
Mobile App                FastAPI (8002)              Frappe (8000)              Redis (13001)
    │                          │                          │                          │
    ├─ POST /plans/change ────►│                          │                          │
    │   {new_plan_id}          │                          │                          │
    │                          ├─ Quick checks ──────────────────────────────────────►│
    │                          │  same plan? cooldown?    │                     GET/EXISTS
    │                          │                          │                          │
    │                          ├─ SET NX freeze key ─────────────────────────────────►│
    │                          │  (30s TTL, combined      │                    SET NX EX 30
    │                          │   lock + freeze)         │                          │
    │                          │                          │                          │
    │                          ├─ DEL gamesession ───────────────────────────────────►│
    │                          ├─ SREM dirty sets ───────────────────────────────────►│
    │                          │                          │                          │
    │                          ├─ execute_plan_change() ─►│                          │
    │                          │                          ├─ Validate (cooldown,     │
    │                          │                          │   eligible plan)          │
    │                          │                          ├─ Snapshot → History      │
    │                          │                          ├─ DELETE subs + progress  │
    │                          │                          ├─ RESET wallet            │
    │                          │                          ├─ UPDATE profile          │
    │                          │                          ├─ COMMIT (atomic)         │
    │                          │◄─ {ok, history_id} ──────┤                          │
    │                          │                          │                          │
    │                          ├─ Clean all player keys ─────────────────────────────►│
    │                          │  DEL (10 direct keys)   │                     DEL pipeline
    │                          │  SCAN+DEL (6 patterns)  │                     SCAN+DEL
    │                          │  SCAN+ZREM (lb:*)       │                     ZREM pipeline
    │                          │                          │                          │
    │                          ├─ SET plan_change_ts ─────────────────────────────────►│
    │                          ├─ PUBLISH invalidation ──────────────────────────────►│
    │                          ├─ DEL freeze key ────────────────────────────────────►│
    │                          │                          │                          │
    │◄─ 200 {success} ────────┤                          │                          │
    │   "Re-login required"    │                          │                          │
```

### Freeze Key Integration

The freeze key `memora:freeze:{player_id}` serves dual purpose:

1. **Distributed lock**: `SET NX` prevents concurrent plan changes for the same player
2. **Freeze signal**: Checked by sync jobs and session endpoints to prevent stale writes

| Consumer | Location | Check Point | Action on Freeze |
|----------|----------|-------------|------------------|
| `sync_dirty_wallets()` | `tasks/sync.py` | Before processing each player | Skip player, leave in dirty set |
| `sync_dirty_progress()` | `tasks/sync.py` | Before processing each entry | Skip entry, leave in dirty set |
| `POST /sessions/start` | `endpoints/sessions.py` | Before session creation | Return 409 |
| `POST /sessions/end` | `endpoints/sessions.py` | Before session completion | Return 409 |

### Error Handling Strategy

| Error Scenario | HTTP Status | Error Code | Recovery |
|----------------|-------------|------------|----------|
| Same plan selected | 400 | SAME_PLAN | None needed |
| Invalid/ineligible plan | 400 | INVALID_PLAN | None needed |
| Cooldown active | 429 | COOLDOWN_ACTIVE | Return retry_after timestamp |
| Concurrent request | 409 | PLAN_CHANGE_IN_PROGRESS | Retry after freeze expires (30s) |
| Frappe API failure | 500 | INTERNAL_ERROR | Freeze key released, no DB changes (atomic rollback) |
| Redis cleanup failure | 200 | N/A | Success returned; stale cache keys expire via TTL or self-heal (FR-022) |

## Key Design Decisions

| Decision | Choice | Rationale | See |
|----------|--------|-----------|-----|
| Freeze mechanism | Single Redis key (lock + freeze) | Simplest correct approach; 30s TTL safety net | research.md R1 |
| Dirty set cleanup | Progress key SCAN → derive entries → SREM | O(K) where K = player's subjects (3-10) vs O(N) SSCAN on 100k+ set | research.md R2 |
| DB atomicity | Single Frappe API call | Automatic transaction from Frappe request lifecycle | research.md R3 |
| Leaderboard cleanup | SCAN `memora:lb:*` + pipeline ZREM | Handles ~1500 keys in <50ms with batched pipelines | research.md R4 |
| Cooldown check | Redis fast check + DB safety net | Most common rejection path is fast; DB prevents edge cases | research.md R5 |
| Available plans | Frappe API with SQL JOIN, no cache | Low-frequency query, simple indexed JOIN | research.md R6 |
| Trigger detection | Compare current season end_date vs today | FR-018: backend auto-detects, no client trust | research.md R7 |

## New Components Summary

| Component | Type | Purpose |
|-----------|------|---------|
| `Memora Player Plan History` | Frappe DocType | Immutable audit record with pre-change snapshots |
| `memora_admin.api.plan_change` | Frappe API module | `execute_plan_change()`, `get_available_plans()` |
| `fastapi_app/api/v1/endpoints/plan_change.py` | FastAPI router | `GET /plans/available`, `POST /plans/change` |
| `fastapi_app/services/plan_change.py` | FastAPI service | `PlanChangeService` — Redis cleanup orchestration |
| `fastapi_app/models/plan_change.py` | Pydantic models | Request/response schemas |
| `memora:freeze:{player_id}` | Redis key | Combined lock + freeze (30s TTL) |
| `memora:plan_change_ts:{player_id}` | Redis key | Cooldown timestamp (24h TTL) |

## Modified Components Summary

| File | Change | Reason |
|------|--------|--------|
| `fastapi_app/core/redis_keys.py` | Add `freeze_key()`, `plan_change_ts_key()`, TTL constants | Centralized key management |
| `memora_admin/tasks/sync.py` | Add freeze check in `sync_dirty_wallets()` and `sync_dirty_progress()` | FR-016: prevent stale writes during plan change |
| `fastapi_app/api/v1/endpoints/sessions.py` | Add freeze check before session start/end | FR-016: prevent in-flight gameplay writes |
| `fastapi_app/api/deps.py` | Add `get_plan_change_service()` factory | Dependency injection |
| `fastapi_app/api/v1/router.py` | Include `plan_change` router | Route registration |
