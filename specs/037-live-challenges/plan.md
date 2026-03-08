# Implementation Plan: Live Challenges

**Branch**: `037-live-challenges` | **Date**: 2026-03-07 | **Spec**: [spec.md](spec.md)
**Input**: Feature specification from `/specs/037-live-challenges/spec.md`

## Summary

Live Challenges adds timed exam events to Memora. Admins create events with questions, students join via shared link, answer at their own pace within a time limit, receive instant scores, and see a leaderboard after the event ends. The implementation spans both the Frappe admin layer (4 DocTypes, scheduled state transitions, admin API) and the FastAPI sidecar (REST endpoints, WebSocket waiting room, in-memory submission queue, grading service). Redis provides hot-path data (event state, questions, capacity counter, duplicate prevention) while MariaDB remains source of truth.

## Technical Context

**Language/Version**: Python 3.11+ (Frappe v15 bench environment)
**Primary Dependencies**: Frappe Framework (ORM, DocTypes, hooks, scheduled jobs), FastAPI, Pydantic v2, redis.asyncio, structlog, asyncio (Queue + background tasks)
**Storage**: MariaDB via Frappe ORM (event config, participation, leaderboard), Redis at `redis://127.0.0.1:13001` (event state, questions cache, capacity counter, submitted set)
**Testing**: pytest 8.4.2, pytest-asyncio, httpx (FastAPI integration tests), `FrappeTestCase` (Frappe-side tests), real Redis (no mocking)
**Target Platform**: Linux server (Frappe bench)
**Project Type**: Dual architecture (Frappe admin + FastAPI sidecar)
**Performance Goals**: 1,000 concurrent participants per event, <2s score return, <60s leaderboard computation
**Constraints**: Server-authoritative timing, no correct answers exposed to client (except post-submission if enabled), batch submission queue (50 items or 30s flush), 30s max data loss window for in-memory queue
**Scale/Scope**: 100k+ registered users, 1 event at a time, up to 10,000 capacity per event

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

### Pre-Research Gate (8 Principles)

| # | Principle | Status | Notes |
|---|-----------|--------|-------|
| I | Self-Healing Cache Architecture | PASS | Redis keys for LC are ephemeral (24h TTL). MariaDB is source of truth. Questions cached in Redis are loaded from MariaDB at event start. No `ensure_hydrated()` needed — keys only exist during event lifecycle. |
| II | Sub-20ms Game API Performance | PASS | LC endpoints are not on the game hot path. Submit endpoint targets <2s (grading + queue). Join endpoint targets <50ms (Redis INCR + SISMEMBER). These are acceptable for an exam context. |
| III | Content Hierarchy Integrity | N/A | Live Challenges don't interact with the content hierarchy (Subject->Track->Unit->Topic->Lesson->Stage). Questions are self-contained. |
| IV | Double-Gate Access Control | N/A | LC has its own access model (eligible plans + capacity). It does not use the existing double-gate (season + grants) system. |
| V | Cryptographic Voucher Security | N/A | No vouchers involved. |
| VI | Financial Precision | N/A | XP is integer — no decimal math needed. |
| VII | Auditable State Machines | PASS | Event lifecycle (Draft->Waiting->Active->Ended) uses `VALID_TRANSITIONS` dict pattern from Voucher Card. All transitions are validated in `validate()`. |
| VIII | Test-First Coverage | PASS | Plan includes unit tests for grading/scoring logic, integration tests for full flow, WebSocket tests, and load tests for concurrent submissions. |

### Post-Design Gate

| # | Principle | Status | Notes |
|---|-----------|--------|-------|
| I | Self-Healing Cache | PASS | LC Redis keys are write-once-read-many during event. 24h TTL auto-cleanup. If Redis restarts mid-event, the event is effectively lost (acceptable — events are short-lived). Future improvement: re-populate from MariaDB on cache miss for Active events. |
| II | Sub-20ms | PASS | LC endpoints are outside the game loop. No impact on existing sub-20ms paths. |
| VII | State Machines | PASS | `VALID_TRANSITIONS` implemented. Terminal state `Ended` is irreversible. Computed fields (`exam_start_ts`, `exam_end_ts`) ensure time-based transitions are deterministic. |
| VIII | Test-First | PASS | Grading logic is pure (no DB/Redis), making unit tests straightforward. Integration tests use real Redis. |

**Gate result: PASS** — No violations. No complexity tracking needed.

## Project Structure

### Documentation (this feature)

```text
specs/037-live-challenges/
├── plan.md              # This file
├── spec.md              # Feature specification
├── research.md          # Phase 0: research findings
├── data-model.md        # Phase 1: entity definitions
├── quickstart.md        # Phase 1: developer quickstart
├── contracts/
│   └── api.md           # Phase 1: API contracts
├── checklists/
│   └── requirements.md  # Spec quality checklist
└── tasks.md             # Phase 2 output (created by /speckit.tasks)
```

### Source Code (repository root)

```text
memora_admin/memora_admin/
├── doctype/
│   ├── memora_live_challenge_event/
│   │   ├── memora_live_challenge_event.json
│   │   ├── memora_live_challenge_event.py
│   │   └── memora_live_challenge_event.js
│   ├── memora_live_challenge_question/
│   │   ├── memora_live_challenge_question.json
│   │   └── memora_live_challenge_question.py
│   ├── memora_live_challenge_eligible_plan/
│   │   ├── memora_live_challenge_eligible_plan.json
│   │   └── memora_live_challenge_eligible_plan.py
│   └── memora_live_challenge_participation/
│       ├── memora_live_challenge_participation.json
│       └── memora_live_challenge_participation.py
├── api/
│   └── live_challenge.py
└── tasks/
    └── live_challenge_transitions.py

fastapi_app/
├── api/v1/endpoints/
│   └── live_challenge.py
├── services/
│   └── live_challenge.py
├── models/
│   └── live_challenge.py
└── core/
    └── redis_keys.py          # Modified: add LC keys
```

**Structure Decision**: Follows existing dual-architecture convention — Frappe DocTypes for admin/persistence, FastAPI for student-facing real-time endpoints. New files slot into existing directory structure with no new top-level directories.
