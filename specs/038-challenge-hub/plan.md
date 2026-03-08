# Implementation Plan: Challenge Hub (مركز التحدي)

**Branch**: `038-challenge-hub` | **Date**: 2026-03-08 | **Spec**: [spec.md](spec.md)
**Input**: Feature specification from `/specs/038-challenge-hub/spec.md`

## Summary

Build a sequential challenge mode where students prove topic mastery by answering all MCQ questions (from Review Item table), earning isolated Challenge XP, and competing on a plan-scoped leaderboard. The implementation reuses existing hierarchy, access control, progress bitmap, FSRS interaction buffer, and leaderboard infrastructure — adding 3 new DocTypes, 1 new FastAPI service, 5 new endpoints, and extending the build pipeline for per-topic question cache files.

## Technical Context

**Language/Version**: Python 3.11+ (Frappe v15 bench environment)
**Primary Dependencies**: FastAPI, Pydantic v2, redis.asyncio, structlog, Frappe Framework (ORM, DocTypes, hooks, scheduled jobs)
**Storage**: MariaDB via Frappe ORM (Challenge Progress, Challenge Attempt, Challenge Attempt Detail); Redis at `redis://127.0.0.1:13001` (progress cache, leaderboard ZSETs, idempotency keys, FSRS interaction buffer)
**Testing**: pytest 8.4.2 + pytest-asyncio (FastAPI), FrappeTestCase (Frappe DocTypes)
**Target Platform**: Linux server (Frappe bench)
**Project Type**: Dual architecture (Frappe admin + FastAPI game API)
**Performance Goals**: Hierarchy browse < 1s P95, attempt submission < 2s P95, leaderboard load < 500ms P95
**Constraints**: 100k concurrent users, sub-20ms for Redis operations, Challenge XP fully isolated from main game systems
**Scale/Scope**: 3 new DocTypes, 5 FastAPI endpoints, 1 new service, 4 new settings fields, build pipeline extension, 6 test cases

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

### Pre-Design Check

| Principle | Status | Notes |
|-----------|--------|-------|
| I. Self-Healing Cache | PASS | Challenge progress uses Redis HASH with `ensure_hydrated()` from MariaDB. Dirty set `memora:dirty:ch_progress` syncs to MariaDB. MariaDB is source of truth. |
| II. Sub-20ms Game API | PASS | All reads from Redis (progress HASH, hierarchy cache, leaderboard ZSET). No Frappe ORM in hot paths. FSRS push via async RPUSH to buffer. |
| III. Content Hierarchy Integrity | PASS | Reuses existing hierarchy structure. No new bitmap versioning needed (challenge progress is separate from lesson bitmaps). Content hash irrelevant (challenge tracks topic-level state, not lesson bits). |
| IV. Double-Gate Access Control | PASS | Condition 1 of topic unlock uses existing `check_access_with_plan()`. Season validation via `ActiveSeasonDep`. |
| V. Cryptographic Voucher Security | N/A | No voucher operations. |
| VI. Financial Precision | N/A | No monetary calculations. XP is integer-only. |
| VII. Auditable State Machines | PASS | Topic states (locked → open → stamped) are deterministic and permanent within season. Every attempt is recorded with full details. |
| VIII. Test-First Coverage | PASS | Tests planned for: grading logic (pure), XP delta (pure), unlock chain (integration), FSRS push (integration), leaderboard isolation (integration). |

### Post-Design Check

| Principle | Status | Notes |
|-----------|--------|-------|
| I. Self-Healing Cache | PASS | `ch_progress_key()` in redis_keys.py, TTL 48h, dirty set sync every 1 min. Hydration reads `Memora Challenge Progress` records. |
| II. Sub-20ms Game API | PASS | Attempt submission: Redis HGET (progress) + HSET (update) + RPUSH (FSRS) + ZINCRBY (leaderboard) — all pipelined. Hierarchy: single Redis GET for cached hierarchy + HGETALL for challenge progress. |
| III. Content Hierarchy Integrity | PASS | MCQ count per topic embedded in hierarchy JSON via build pipeline extension. Empty topics detected at query time. |
| IV. Double-Gate Access Control | PASS | Attempt endpoint validates: season active + content access + normal path complete + sequence. |
| VII. Auditable State Machines | PASS | `Memora Challenge Attempt` is append-only (never updated). `Memora Challenge Progress.stamped` transitions 0→1 only. |
| VIII. Test-First Coverage | PASS | Pure logic tests for grading, XP delta, empty topic chain. Integration tests against real Redis for leaderboard and progress cache. |

**Gate Result**: PASS — no violations.

## Project Structure

### Documentation (this feature)

```text
specs/038-challenge-hub/
├── plan.md              # This file
├── spec.md              # Feature specification
├── research.md          # Phase 0 output
├── data-model.md        # Phase 1 output
├── quickstart.md        # Phase 1 output
├── contracts/
│   └── challenge-hub-api.md  # API contracts
├── checklists/
│   └── requirements.md  # Requirement checklist
└── tasks.md             # Phase 2 output (created by /speckit.tasks)
```

### Source Code (repository root)

```text
fastapi_app/
├── api/v1/endpoints/
│   └── challenge.py                    # 5 endpoints (hierarchy ×2, attempt, leaderboard ×2)
├── services/
│   └── challenge.py                    # ChallengeService: progress, grading, XP, FSRS, unlock
└── models/
    └── challenge.py                    # Pydantic request/response schemas

memora_admin/memora_admin/doctype/
├── memora_challenge_progress/          # Per-student per-topic state (stamped, best score, XP)
│   ├── memora_challenge_progress.json
│   ├── memora_challenge_progress.py
│   └── test_memora_challenge_progress.py
├── memora_challenge_attempt/           # Per-attempt analytics record
│   ├── memora_challenge_attempt.json
│   ├── memora_challenge_attempt.py
│   └── test_memora_challenge_attempt.py
└── memora_challenge_attempt_detail/    # Child table: per-question results
    ├── memora_challenge_attempt_detail.json
    └── memora_challenge_attempt_detail.py

memora_admin/services/build/
└── challenge_questions.py              # Topic question JSON file generator

fastapi_app/tests/
├── test_challenge_service.py           # Unit tests for ChallengeService
└── test_challenge_endpoints.py         # Integration tests for endpoints
```

**Modified Files**:

| File | Change |
|------|--------|
| `fastapi_app/api/v1/router.py` | Mount `challenge.router` |
| `fastapi_app/api/deps.py` | Add `ChallengeServiceDep`, rate limit scopes |
| `fastapi_app/core/redis_keys.py` | Add 6 key builders + TTL constants (includes `ch_attempt_buffer_key`) |
| `memora_admin/memora_admin/doctype/memora_settings/memora_settings.json` | Add Challenge Hub section (4 fields) |
| `memora_admin/hooks.py` | Add `sync_dirty_challenge_progress` (every 1 min), question file rebuild trigger |
| `memora_admin/tasks/sync.py` | Add `sync_dirty_challenge_progress()` function |
| `memora_admin/events/build_trigger.py` | Trigger question JSON rebuild on Review Item sync |
| `memora_admin/services/build/plan_generator.py` | Embed `mcq_count` per topic in hierarchy JSON |

**Structure Decision**: Follows existing dual-architecture pattern. FastAPI handles all game API endpoints. Frappe manages DocTypes, admin, and background sync. No new architectural patterns introduced.

## Complexity Tracking

> No constitution violations. No complexity justifications needed.
