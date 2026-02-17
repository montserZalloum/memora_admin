# Quickstart: Remaining Service Tests

**Feature**: 012-remaining-service-tests | **Date**: 2026-02-17

## Prerequisites

1. Redis running at `redis://127.0.0.1:13000`
2. pytest + pytest-asyncio installed (already present in bench env)
3. Existing Phase 1-3 tests passing

Verify:
```bash
cd /home/corex/aurevia-bench/apps/memora_admin
python3 -m pytest fastapi_app/tests/ -v --tb=short
# Expected: 91 tests passing
```

## Implementation Order

Create test files in dependency order (simplest first):

### Wave 1 — Pure/Isolated Services (no Frappe, no hardcoded keys)
1. `test_stats_service.py` — StatsService (uses key_prefix, no Frappe)
2. `test_leaderboard_service.py` — LeaderboardService (no Frappe, but hardcoded keys)

### Wave 2 — Cache-Pattern Services (all follow identical pattern)
3. `test_hierarchy_service.py` — HierarchyService
4. `test_plan_service.py` — PlanService
5. `test_settings_service.py` — SettingsService
6. `test_catalog_service.py` — CatalogService (adds player filtering)
7. `test_profile_service.py` — ProfileService (adds batch + fallback)

### Wave 3 — Delegation Services (Frappe error mapping)
8. `test_purchase_service.py` — PurchaseService (HTTPException mapping)
9. `test_review_service.py` — ReviewService (cache + delegation)
10. `test_voucher_service.py` — VoucherService (Lua scripts + HMAC)

## File Template

Each test file follows this skeleton:

```python
"""Tests for {ServiceName}."""

import pytest

# Service import
from fastapi_app.services.{module} import {ServiceClass}

# Test constants
TEST_PLAYER = "PLAYER-TEST-SVC-001"
TEST_SUBJECT = "SUBJ-TEST-001"


@pytest.fixture
async def svc(redis_client, test_prefix, mock_frappe):
    """ServiceClass with test dependencies."""
    return ServiceClass(redis_client, frappe_client=mock_frappe, key_prefix=test_prefix)


class TestCacheHit:
    """Cache hit returns cached data without Frappe call."""

    async def test_tc_XXX_01_returns_cached_data(self, svc, redis_client, test_prefix, mock_frappe):
        """TC-XXX-01: Cache hit returns data without Frappe call."""
        # Setup: pre-seed Redis
        # Action: call service method
        # Assert: correct return + mock_frappe.call.assert_not_called()


class TestCacheMiss:
    """Cache miss fetches from Frappe and caches result."""

    async def test_tc_XXX_02_fetches_and_caches(self, svc, redis_client, test_prefix, mock_frappe):
        """TC-XXX-02: Cache miss fetches from Frappe and caches."""
        # Setup: configure mock return
        # Action: call service method
        # Assert: correct return + mock_frappe.call.assert_called_once() + Redis key exists
```

## Running Tests

```bash
# Run all Phase 4 tests only
python3 -m pytest fastapi_app/tests/test_voucher_service.py \
    fastapi_app/tests/test_leaderboard_service.py \
    fastapi_app/tests/test_stats_service.py \
    fastapi_app/tests/test_hierarchy_service.py \
    fastapi_app/tests/test_catalog_service.py \
    fastapi_app/tests/test_profile_service.py \
    fastapi_app/tests/test_plan_service.py \
    fastapi_app/tests/test_settings_service.py \
    fastapi_app/tests/test_purchase_service.py \
    fastapi_app/tests/test_review_service.py -v

# Run single file during development
python3 -m pytest fastapi_app/tests/test_stats_service.py -v --tb=long

# Run full suite (Phases 1-4, must still pass)
python3 -m pytest fastapi_app/tests/ -v --tb=short

# Run with timing
python3 -m pytest fastapi_app/tests/ -v --durations=10
```

## Key Patterns

### Services WITH `key_prefix` (auto-cleanup by conftest)
- StatsService, HierarchyService, CatalogService, ProfileService, PlanService

### Services WITHOUT `key_prefix` (need manual cleanup)
- VoucherService: `memora:voucher_fail:player:*`, `memora:voucher_fail:ip:*`
- LeaderboardService: `memora:lb:*`
- SettingsService: `memora:settings:gamification`
- PurchaseService: `memora:pending:*`
- ReviewService: `memora:reviews_overview:*`

Use unique test IDs + autouse cleanup fixtures for these.

## Verification Checklist

- [ ] All 10 test files created
- [ ] Each file has at least 2 tests (SC-002)
- [ ] Total tests ≥ 30 (SC-001)
- [ ] `python3 -m pytest fastapi_app/tests/ -v` passes with zero failures
- [ ] Existing Phase 1-3 tests unmodified and still passing (SC-003)
- [ ] Suite completes in under 30 seconds (SC-004)
- [ ] VoucherService Lua scripts tested against real Redis (SC-007)
