# Implementation Plan: Live Challenge Mode — Last Stand

**Branch**: `053-last-stand-mode` | **Date**: 2026-03-22 | **Spec**: [spec.md](./spec.md)
**Input**: Feature specification from `/specs/053-last-stand-mode/spec.md`

## Summary

Add a "Last Stand" elimination mode to the Live Challenge system alongside the existing "exam" mode. Players start with a configurable number of hearts; wrong or missed answers cost one heart; zero hearts = eliminated (spectator). The server controls round-based question delivery with synchronized timing. All runtime state lives in Redis (no DB writes during gameplay). Results are persisted during post-event reconciliation with a three-tier ranking: score → hearts → response time.

The implementation extends the existing Live Challenge infrastructure: DocTypes gain a `mode` field, the FastAPI service gains a round engine, WebSocket messaging adds round lifecycle types, and reconciliation adds hearts/elimination data. Exam mode remains completely untouched.

## Technical Context

**Language/Version**: Python 3.11+
**Primary Dependencies**: Frappe v15 (DocTypes, ORM, scheduled tasks), FastAPI (async endpoints, WebSocket), Redis (async via redis-py, Lua scripting)
**Storage**: MariaDB (source of truth via Frappe ORM), Redis (hot runtime state, 86400s TTL)
**Testing**: pytest with httpx.AsyncClient (FastAPI), FrappeTestCase (Frappe DocTypes)
**Target Platform**: Linux server (FastAPI sidecar on port 8002)
**Project Type**: Web application — Frappe admin backend + FastAPI game API sidecar
**Performance Goals**: Sub-20ms answer submission, <500ms question synchronization across 10k players, round_result broadcast within 1s for 10k connections
**Constraints**: Zero DB writes during Active gameplay (FR-022), Redis-first design, no late join in Last Stand (FR-007), exam mode zero regressions (US7)
**Scale/Scope**: 10,000 concurrent players per event (FR-024), up to 50 questions per event

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

### Pre-Phase 0 Check

| # | Principle | Applicable | Status | Notes |
|---|-----------|------------|--------|-------|
| I | Self-Healing Cache Architecture | Partial | **PASS** | Runtime state (hearts, rounds) is intentionally ephemeral during Active gameplay — analogous to `memora:session` keys. Post-event data is persisted via reconciliation. Redis loss during Active ends the event (cron safety net). No new self-healing needed since runtime state is not reconstructable from DB by design (FR-022). |
| II | Sub-20ms Game API Performance | Yes | **PASS** | Answer submission is pure Redis (Lua script). No Frappe ORM in hot paths. Round management is server-pushed via WebSocket (not request-response). |
| III | Content Hierarchy Integrity | No | N/A | Uses existing MCQ questions. No hierarchy changes. |
| IV | Double-Gate Access Control | No | N/A | Join uses existing plan eligibility + paid event gate. No new access control. |
| V | Cryptographic Voucher Security | No | N/A | No voucher interaction. |
| VI | Financial Precision | No | N/A | XP is integer-based. No monetary calculations. |
| VII | Auditable State Machines | Yes | **PASS** | Reuses existing event lifecycle (Draft/Waiting/Active/Ended). New round lifecycle (Answer Window → Result Window → Next/End) is documented in data-model.md. Player state machine (Alive → Eliminated) is documented. |
| VIII | Test-First Coverage | Yes | **PASS** | Test plan covers: DocType validation (Frappe), round engine logic (pytest), Lua script atomicity (pytest), WebSocket message flow (httpx), ranking algorithm (pure function), reconciliation (integration). |

**Gate result**: PASS — no violations.

### Post-Phase 1 Re-Check

| # | Principle | Status | Design Notes |
|---|-----------|--------|--------------|
| I | Self-Healing Cache | **PASS** | Round state HASH enables crash recovery. FastAPI startup scan resumes engines from Redis state. Cron safety net handles total failure. |
| II | Sub-20ms Performance | **PASS** | Answer Lua script: ~5 Redis commands in one atomic call. Personalized WS broadcast: O(N) sends with chunked concurrency. No Frappe calls during gameplay. |
| VII | Auditable State Machines | **PASS** | Three state machines documented: event lifecycle (existing), round lifecycle (new), player lifecycle (new). All transitions enforced in code. |
| VIII | Test-First Coverage | **PASS** | Test files planned: test_last_stand_engine.py (round logic), test_last_stand_answer.py (Lua + endpoint), test_live_challenge_event.py (DocType validation extensions). |

**Gate result**: PASS — no violations. No Complexity Tracking entries needed.

## Project Structure

### Documentation (this feature)

```text
specs/053-last-stand-mode/
├── spec.md              # Feature specification
├── plan.md              # This file
├── research.md          # Phase 0: research findings (9 decisions)
├── data-model.md        # Phase 1: entity design, Redis schemas, state machines
├── quickstart.md        # Phase 1: developer quickstart guide
├── contracts/
│   └── api.yaml         # Phase 1: OpenAPI contracts (new + modified endpoints, WS messages)
└── tasks.md             # Phase 2: (generated by /speckit.tasks)
```

### Source Code (repository root)

```text
memora_admin/
├── memora_admin/
│   ├── doctype/
│   │   ├── memora_live_challenge_event/
│   │   │   ├── memora_live_challenge_event.json    # MODIFY: add mode, starting_hearts, result_window_duration
│   │   │   └── memora_live_challenge_event.py      # MODIFY: validation for Last Stand fields, immutable mode
│   │   └── memora_live_challenge_participation/
│   │       └── memora_live_challenge_participation.json  # MODIFY: add final_hearts, is_eliminated, eliminated_at_question, avg_response_time_ms
│   ├── api/
│   │   └── live_challenge.py                        # MODIFY: dashboard stats for Last Stand
│   └── tests/
│       └── test_live_challenge_event.py             # MODIFY: add Last Stand validation tests
├── tasks/
│   └── live_challenge_transitions.py                # MODIFY: mode-aware transitions, Last Stand reconciliation + ranking
└── fastapi_app/
    ├── api/v1/endpoints/
    │   └── live_challenge.py                        # MODIFY: new /answer endpoint, /submit mode gate, WS handler
    ├── services/
    │   ├── live_challenge.py                        # MODIFY: mode branching, connection player tracking
    │   └── last_stand_engine.py                     # NEW: round engine (async loop, Lua scripts, evaluation)
    ├── models/
    │   └── live_challenge.py                        # MODIFY: AnswerRequest/Response, WS message models
    ├── core/
    │   └── redis_keys.py                            # MODIFY: new key builders
    └── tests/
        ├── test_last_stand_engine.py                # NEW: round engine unit tests
        └── test_last_stand_answer.py                # NEW: answer endpoint + Lua tests
```

**Structure Decision**: Follows existing patterns — new round engine in its own service file (`last_stand_engine.py`) to keep `live_challenge.py` manageable (already 1405 lines). All other changes are modifications to existing files.

## Complexity Tracking

> No violations — table intentionally empty.

| Violation | Why Needed | Simpler Alternative Rejected Because |
|-----------|------------|-------------------------------------|
| — | — | — |
