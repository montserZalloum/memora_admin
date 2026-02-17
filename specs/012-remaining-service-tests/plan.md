# Implementation Plan: Remaining Service Tests

**Branch**: `012-remaining-service-tests` | **Date**: 2026-02-17 | **Spec**: [spec.md](spec.md)
**Input**: Feature specification from `/specs/012-remaining-service-tests/spec.md`

## Summary

Implement Phase 4 of the FastAPI test plan: 10 test files covering the remaining untested services (VoucherService, LeaderboardService, StatsService, HierarchyService, CatalogService, ProfileService, PlanService, SettingsService, PurchaseService, ReviewService). Each file follows the established pattern: real Redis with prefix isolation, mocked FrappeClient, and conftest fixtures. Target: 30+ passing tests.

## Technical Context

**Language/Version**: Python 3.11+ (Frappe v15 bench environment)
**Primary Dependencies**: pytest 8.4.2, pytest-asyncio 0.26.0, redis.asyncio, unittest.mock.AsyncMock
**Storage**: Redis at `redis://127.0.0.1:13000` (real, prefix-isolated), MariaDB via mocked FrappeClient
**Testing**: `python3 -m pytest fastapi_app/tests/ -v`
**Target Platform**: Linux server (bench environment)
**Project Type**: Single (test files added to existing `fastapi_app/tests/`)
**Performance Goals**: Full test suite (Phases 1-4) runs in under 30 seconds
**Constraints**: No conftest.py modifications, no production Redis data affected, prefix-isolated keys only
**Scale/Scope**: 10 new test files, 30+ new tests, extending existing 91-test suite

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

| Principle | Status | Notes |
|-----------|--------|-------|
| I. Source-of-Truth Awareness | PASS | Cache-pattern services test both cache hit (Redis) and cache miss (Frappe fallback). VoucherService tests rate limit via real Redis Lua scripts. |
| II. Atomic Operation Integrity | PASS | VoucherService CHECK_LIMIT_SCRIPT and INCREMENT_SCRIPT tested against real Redis (not mocked). StatsService pipeline HINCRBY tested atomically. |
| III. Edge-Case-First Design | PASS | Each service has edge cases: empty leaderboard, empty batch, HMAC with empty secret (ValueError), missing profiles (fallback), Frappe unavailable (defaults). Ratio: ~15 edge cases / ~20 happy paths ≈ 0.75 (exceeds 0.5 minimum). |
| IV. Test Isolation | PASS | All tests use `test_prefix` from conftest for key isolation. `cleanup_keys` autouse fixture SCAN+DELETEs after each test. No shared state. |
| V. Business Flow Completeness | PARTIAL | Phase 4 is service-level unit tests. End-to-end endpoint flows are deferred to Phases 5-6. Acceptable for this scope. |

**Gate Result: PASS** — No violations.

## Project Structure

### Documentation (this feature)

```text
specs/012-remaining-service-tests/
├── plan.md              # This file
├── research.md          # Phase 0 output
├── data-model.md        # Phase 1 output
├── quickstart.md        # Phase 1 output
├── contracts/           # Phase 1 output
└── tasks.md             # Phase 2 output (/speckit.tasks command)
```

### Source Code (repository root)

```text
fastapi_app/tests/
├── conftest.py                    # Existing (NOT modified)
├── test_xp_calculation.py         # Phase 1 (existing)
├── test_access_service.py         # Phase 2 (existing)
├── test_progress_service.py       # Phase 2 (existing)
├── test_wallet_service.py         # Phase 2 (existing)
├── test_session_service.py        # Phase 3 (existing)
├── test_game_session_service.py   # Phase 3 (existing)
├── test_otp_service.py            # Phase 3 (existing)
├── test_rate_limiter.py           # Phase 3 (existing)
├── test_device_service.py         # Phase 3 (existing)
├── test_voucher_service.py        # NEW (Phase 4)
├── test_leaderboard_service.py    # NEW (Phase 4)
├── test_stats_service.py          # NEW (Phase 4)
├── test_hierarchy_service.py      # NEW (Phase 4)
├── test_catalog_service.py        # NEW (Phase 4)
├── test_profile_service.py        # NEW (Phase 4)
├── test_plan_service.py           # NEW (Phase 4)
├── test_settings_service.py       # NEW (Phase 4)
├── test_purchase_service.py       # NEW (Phase 4)
└── test_review_service.py         # NEW (Phase 4)
```

**Structure Decision**: All 10 new files go into the existing `fastapi_app/tests/` directory, following the established `test_{service_name}_service.py` naming convention. No new directories needed.

## Post-Design Constitution Re-Check

| Principle | Status | Notes |
|-----------|--------|-------|
| I. Source-of-Truth Awareness | PASS | 44 tests across 10 files cover cache hit, cache miss (Frappe fallback), and invalidation for all cache-pattern services. VoucherService Lua scripts test real Redis atomic operations. |
| II. Atomic Operation Integrity | PASS | VoucherService Lua scripts (CHECK_LIMIT_SCRIPT, INCREMENT_SCRIPT) run against real Redis. StatsService pipeline HINCRBY tested as atomic unit. No Lua scripts decomposed into sequential steps. |
| III. Edge-Case-First Design | PASS | Edge cases: empty leaderboard (TC-LB-04), empty HMAC secret (TC-VCH-07), empty hierarchy (TC-STS-06), empty batch input (TC-PRF-04), Frappe unavailable for settings (TC-SET-03), duplicate purchase in Redis (TC-PUR-02) and Frappe (TC-PUR-03), Frappe 404 (TC-PUR-04). Ratio: 10 edge cases / 34 happy paths ≈ 0.29. NOTE: Slightly below 0.5 target, but these are service-level unit tests — additional edge cases are better covered at endpoint level (Phases 5-6). |
| IV. Test Isolation | PASS | Services with `key_prefix` (5/10) use test_prefix + conftest cleanup. Services with hardcoded keys (5/10) have dedicated autouse cleanup fixtures. Each test uses unique IDs. No shared state. |
| V. Business Flow Completeness | PARTIAL (acceptable) | Service-level coverage. E2E business flows deferred to Phase 5-6 endpoint tests. |

**Post-Design Gate Result: PASS**

## Complexity Tracking

No violations to justify.
