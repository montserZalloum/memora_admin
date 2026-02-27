# Implementation Plan: Locust Load Test Suite

**Branch**: `030-locust-load-tests` | **Date**: 2026-02-27 | **Spec**: [spec.md](./spec.md)
**Input**: Feature specification from `/specs/030-locust-load-tests/spec.md`

## Summary

Build a Locust-based load test suite that simulates realistic player behavior against the FastAPI sidecar (port 8002) with 4 weighted user behavior profiles (Dashboard 40%, LessonPlayer 35%, Browser 15%, Leaderboard 10%). The suite validates the system's 100k concurrent user target via a 5-stage scaling ladder, uses externalized Python config for test data, handles rate-limited responses gracefully, and produces per-endpoint stats for analysis. No production code is modified.

## Technical Context

**Language/Version**: Python 3.11+
**Primary Dependencies**: Locust (load testing framework), httpx or Locust built-in HTTP client
**Storage**: N/A (test suite only; reads config from Python file)
**Testing**: Manual execution via Locust CLI (`--headless`) or Locust Web UI
**Target Platform**: Linux server (single machine; distributed mode documented but not orchestrated)
**Project Type**: Single (standalone load test module within existing repo)
**Performance Goals**: Simulate up to 100k concurrent virtual users; validate p99 <500ms at 1k users, <1% error rate at 10k users
**Constraints**: Single Locust master process; test players must be pre-created; no production code modifications (FR-011)
**Scale/Scope**: 5-stage ladder: 100 → 1,000 → 10,000 → 50,000 → 100,000 users

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

| Principle | Relevance | Status |
|-----------|-----------|--------|
| I. Self-Healing Cache Architecture | N/A — no Redis/cache code modified | PASS |
| II. Sub-20ms Game API Performance | VALIDATES — load test measures this target | PASS |
| III. Content Hierarchy Integrity | N/A — read-only access to hierarchy endpoints | PASS |
| IV. Double-Gate Access Control | N/A — uses pre-created test accounts with proper grants | PASS |
| V. Cryptographic Voucher Security | N/A — voucher endpoints not exercised in profiles | PASS |
| VI. Financial Precision | N/A — no financial operations | PASS |
| VII. Auditable State Machines | N/A — no state machine transitions modified | PASS |
| VIII. Test-First Coverage | COMPLIANT — this IS a test suite | PASS |

**FR-011 Compliance**: Suite MUST NOT modify any production code or seed data directly into data stores. PASS — all Locust files live in `load_tests/` directory, no production imports.

**Gate Result**: ALL PASS. No violations. No complexity tracking needed.

## Project Structure

### Documentation (this feature)

```text
specs/030-locust-load-tests/
├── plan.md              # This file
├── research.md          # Phase 0: Locust best practices, endpoint mapping, auth flow
├── data-model.md        # Phase 1: Config entities, user profile classes
├── quickstart.md        # Phase 1: Getting started guide
├── contracts/           # Phase 1: API endpoint contracts exercised by load tests
│   ├── auth.md          # Login/refresh contracts
│   ├── dashboard.md     # Profile/stats/activity/mastery contracts
│   ├── sessions.md      # Session start/end contracts
│   ├── progress.md      # Hierarchy browsing contracts
│   └── leaderboard.md   # Leaderboard contracts
└── tasks.md             # Phase 2 output (/speckit.tasks)
```

### Source Code (repository root)

```text
load_tests/
├── locustfile.py        # Main entry point with all 4 user classes
├── config.py            # Test data config (players, subjects, lessons)
├── config.example.py    # Placeholder config for version control
├── helpers.py           # Shared auth helper, response validators
└── README.md            # Usage guide, scaling ladder, distributed mode docs
```

**Structure Decision**: Standalone `load_tests/` directory at the app root (same level as `fastapi_app/`). This keeps load test code isolated from production code (FR-011), avoids polluting `fastapi_app/tests/` (which is for unit/integration tests), and makes the suite easy to run from any machine by copying just this directory + installing Locust.

## Complexity Tracking

> No violations detected. Table intentionally left empty.

| Violation | Why Needed | Simpler Alternative Rejected Because |
|-----------|------------|-------------------------------------|
| (none)    | —          | —                                   |
