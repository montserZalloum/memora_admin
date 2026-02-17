# Implementation Plan: Core Service Tests (Phase 2)

**Branch**: `010-core-service-tests` | **Date**: 2026-02-17 | **Spec**: [spec.md](spec.md)
**Input**: Feature specification from `/specs/010-core-service-tests/spec.md`

## Summary

Implement ~31 unit/integration tests across 3 test files for the three core FastAPI services: AccessService (11 tests), ProgressService (8 tests), and WalletService (12 tests). Tests use real Redis with prefix isolation, mock FrappeClient at the `.call()` boundary, and validate all public methods including hydration, dirty tracking, and the Lua streak script. All test infrastructure (conftest.py, fixtures) is already in place from Phase 1 (009-fastapi-test-foundation).

## Technical Context

**Language/Version**: Python 3.11+ (Frappe v15 bench environment)
**Primary Dependencies**: pytest 8.4.2, pytest-asyncio 0.26.0, redis.asyncio, unittest.mock.AsyncMock
**Storage**: Redis at `redis://127.0.0.1:13000` (real, prefix-isolated), MariaDB via mocked FrappeClient
**Testing**: pytest with `asyncio_mode = "auto"`, test files in `fastapi_app/tests/`
**Target Platform**: Linux server (Ubuntu 22.04)
**Project Type**: Single project (FastAPI sidecar test suite)
**Performance Goals**: All tests pass in <5s total (Redis operations are sub-ms)
**Constraints**: Never FLUSHDB (shared Redis with Frappe), all tests must be independent and isolated
**Scale/Scope**: 31 tests across 3 files, testing 3 services with ~15 public methods total

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

| Principle | Status | Evidence |
|-----------|--------|----------|
| **I. Source-of-Truth Awareness** (NON-NEGOTIABLE) | PASS | All 3 services test both Redis state AND hydration from MariaDB (mocked Frappe). Dirty set membership verified for write paths (AccessService excluded — no dirty tracking). |
| **II. Atomic Operation Integrity** (NON-NEGOTIABLE) | PASS | `STREAK_UPDATE_SCRIPT` Lua tested with exact key/argument patterns across all 5 branches (replay, same-day, consecutive, missed, first). `complete_lesson` SETBIT+SADD tested as atomic pair. |
| **III. Edge-Case-First Design** | PASS | 31 tests include: idempotent re-grant (returns 0), replay detection, missing FrappeClient graceful skip, empty wallet defaults, track-key plan bypass, hydration-skip-when-exists. Ratio: ~12 edge cases / ~19 happy paths > 1:2 minimum. |
| **IV. Test Isolation** (NON-NEGOTIABLE) | PASS | Every test uses unique `test:{uuid}:` prefix. `cleanup_keys` fixture (autouse) SCAN+DELetes after each test. No shared state. Dirty keys use hardcoded `memora:dirty:*` but are additive (SADD) and cleaned by prefix scan. |
| **V. Business Flow Completeness** | PARTIAL | Phase 2 tests individual services, not end-to-end flows. Full business flows (lesson completion pipeline, sync flow) are covered in Phases 3-8 per FASTAPI_TEST_PLAN.md. Acceptable for Phase 2 scope. |

**Gate 1 (Pre-Merge)**: All tests pass, no `time.sleep()`, no Voucher/Library imports, each service has happy + error tests.
**Gate 2 (Coverage)**: All public methods of AccessService (5), ProgressService (4), WalletService (4) covered. Lua script `STREAK_UPDATE_SCRIPT` has 5 dedicated tests.
**Gate 3 (Risk Coverage)**: RISK-01 (hydration), RISK-04 (streak timezone), RISK-06 (XP on empty wallet), RISK-08 (access after flush) directly addressed.

## Project Structure

### Documentation (this feature)

```text
specs/010-core-service-tests/
├── plan.md              # This file
├── research.md          # Phase 0 output
├── data-model.md        # Phase 1 output (test data model)
├── quickstart.md        # Phase 1 output (how to run tests)
├── contracts/           # Phase 1 output (test contracts per service)
└── tasks.md             # Phase 2 output (/speckit.tasks command)
```

### Source Code (repository root)

```text
fastapi_app/
├── services/
│   ├── access.py              # AccessService (under test)
│   ├── progress.py            # ProgressService (under test)
│   └── wallet.py              # WalletService + STREAK_UPDATE_SCRIPT (under test)
├── core/
│   └── constants.py           # DIRTY_PROGRESS_KEY, DIRTY_WALLETS_KEY
└── tests/
    ├── __init__.py             # (exists)
    ├── conftest.py             # (exists) Fixtures: redis_client, test_prefix, mock_frappe, cleanup_keys
    ├── test_xp_calculation.py  # (exists) Phase 1 tests
    ├── test_access_service.py  # NEW — 11 tests
    ├── test_progress_service.py # NEW — 8 tests
    └── test_wallet_service.py  # NEW — 12 tests
```

**Structure Decision**: Tests live in `fastapi_app/tests/` alongside existing Phase 1 tests. No new directories needed. Each service gets one test file following the naming convention `test_{service_name}_service.py`.

## Post-Design Constitution Re-Check

*Re-evaluated after Phase 1 design artifacts (data-model.md, contracts/, quickstart.md) were produced.*

| Principle | Status | Post-Design Evidence |
|-----------|--------|---------------------|
| **I. Source-of-Truth Awareness** | PASS | Contracts explicitly define mock return values for hydration (hex bitmaps, access keys, wallet data). Each contract includes Frappe API method name and expected payload shape. |
| **II. Atomic Operation Integrity** | PASS | Wallet contract defines 5 Lua script test cases with exact pre-seeded hash state and expected return values. Progress contract tests SETBIT+SADD as coupled operations. |
| **III. Edge-Case-First Design** | PASS | Contracts include: idempotent grant (returns 0), TRK-key plan bypass, empty wallet defaults, hydration-skip-when-exists, no-frappe-client graceful handling. Edge ratio verified. |
| **IV. Test Isolation** | PASS | Data model documents `test_prefix` usage in all key patterns. Dirty key cleanup fixtures documented per contract. No cross-test dependencies. |
| **V. Business Flow Completeness** | PARTIAL (expected) | Phase 2 scope is service-level. Contracts cover all public methods but not cross-service flows. Acceptable per FASTAPI_TEST_PLAN.md phasing. |

**Verdict**: All NON-NEGOTIABLE principles (I, II, IV) PASS. Principle V is PARTIAL by design (Phase 2 scope). No gate violations. Proceed to task generation.

## Complexity Tracking

> No constitution violations. No complexity justification needed.
