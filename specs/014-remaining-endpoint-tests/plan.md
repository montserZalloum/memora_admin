# Implementation Plan: Remaining Endpoint Tests

**Branch**: `014-remaining-endpoint-tests` | **Date**: 2026-02-17 | **Spec**: [spec.md](spec.md)
**Input**: Feature specification from `/specs/014-remaining-endpoint-tests/spec.md`

## Summary

Create 11 test files with ~41-45 async test cases covering all remaining FastAPI endpoint groups (catalog, purchase, plans, profile, leaderboard, reviews, settings, subscriptions, voucher, webhooks, notifications). Tests use the established Phase 5 patterns: class-based organization, `pytest-asyncio`, real Redis with prefix isolation, mocked `FrappeClient`, and the `authed_client`/`admin_client`/`app_client` fixtures from conftest.py.

## Technical Context

**Language/Version**: Python 3.11+ (Frappe v15 bench environment)
**Primary Dependencies**: pytest 8.4.2, pytest-asyncio 0.26.0, httpx 0.28.1, redis.asyncio (all pre-installed)
**Storage**: Redis at `redis://127.0.0.1:13000` (real, shared with Frappe -- prefix isolation mandatory)
**Testing**: pytest + pytest-asyncio + httpx.AsyncClient with ASGITransport
**Target Platform**: Linux server (Frappe bench)
**Project Type**: Single project (test files only, no production code changes)
**Performance Goals**: Full test suite (Phases 1-6) completes in under 60 seconds
**Constraints**: No FLUSHDB, no Frappe imports, no production code modifications
**Scale/Scope**: 11 new test files, ~41-45 test cases, 0 production files modified

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

| Principle | Status | Notes |
|-----------|--------|-------|
| **I. Source-of-Truth Awareness** | PASS | Endpoint tests validate HTTP responses from mocked services. Underlying Redis/MariaDB dual-layer is tested by Phase 2-4 service tests. Endpoint tests mock at the service boundary (FrappeClient), which is the correct abstraction level. |
| **II. Atomic Operation Integrity** | PASS | No Lua scripts are tested at the endpoint layer. Lua atomicity is covered by Phase 2-4 service tests. Endpoint tests validate that service results are correctly serialized into HTTP responses. |
| **III. Edge-Case-First Design** | PASS | Each endpoint group includes error paths (401, 404, 409, 422, 429) alongside happy paths. Ratio exceeds the 1:2 edge-to-happy minimum. |
| **IV. Test Isolation** | PASS | All tests use `authed_client` (unique UUID player IDs), `cleanup_keys` autouse fixture (SCAN+DEL), and prefix-isolated Redis keys. No shared state between tests. |
| **V. Business Flow Completeness** | PASS | Endpoint tests cover complete HTTP request/response cycles including auth, service delegation, and response serialization. End-to-end flows are covered by Phase 5 session endpoint tests. |
| **Excluded Scope (Voucher System)** | EXCEPTION | `test_voucher_endpoints.py` tests the voucher HTTP endpoints (preview/redeem), NOT the voucher DocType system. This is explicitly listed in the FASTAPI_TEST_PLAN Phase 6. The constitution excludes voucher *system* tests (batches, cards, allocations, PINs, redemption logic), NOT the FastAPI endpoint layer. The endpoint tests mock VoucherService entirely. |

## Project Structure

### Documentation (this feature)

```text
specs/014-remaining-endpoint-tests/
├── plan.md              # This file
├── research.md          # Phase 0 output
├── data-model.md        # Phase 1 output
├── quickstart.md        # Phase 1 output
├── contracts/           # Phase 1 output
│   └── endpoint-test-contracts.md
└── tasks.md             # Phase 2 output (created by /speckit.tasks)
```

### Source Code (repository root)

```text
fastapi_app/tests/                       # All new files in this directory
├── conftest.py                          # EXISTING - not modified
├── test_catalog_endpoints.py            # NEW - 3 tests
├── test_purchase_endpoints.py           # NEW - 4 tests
├── test_plans_endpoints.py              # NEW - 3 tests
├── test_profile_endpoints.py            # NEW - 6 tests
├── test_leaderboard_endpoints.py        # NEW - 5 tests
├── test_review_endpoints.py             # NEW - 5 tests
├── test_settings_endpoints.py           # NEW - 2 tests
├── test_subscription_endpoints.py       # NEW - 2 tests
├── test_voucher_endpoints.py            # NEW - 4 tests
├── test_webhook_endpoints.py            # NEW - 4 tests
└── test_notification_endpoints.py       # NEW - 3 tests
```

**Structure Decision**: All 11 test files go into the existing `fastapi_app/tests/` directory alongside the Phase 5 test files. No new directories, no conftest modifications, no production code changes.

## Complexity Tracking

| Violation | Why Needed | Simpler Alternative Rejected Because |
|-----------|------------|-------------------------------------|
| Voucher endpoint tests (constitution excluded scope) | FASTAPI_TEST_PLAN Phase 6 explicitly includes `test_voucher_endpoints.py` | Endpoint tests mock VoucherService entirely; no voucher system logic is tested. Only HTTP routing/status-code mapping is verified. |
