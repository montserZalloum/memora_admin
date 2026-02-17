# Feature Specification: Remaining Service Tests

**Feature Branch**: `012-remaining-service-tests`
**Created**: 2026-02-17
**Status**: Draft
**Input**: User description: "Phase 4: Remaining Service Tests (~30 tests, 10 files) from FASTAPI_TEST_PLAN.md"

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Voucher Service Confidence (Priority: P1)

A developer modifying voucher redemption logic (HMAC computation, rate limiting, Frappe delegation) needs automated tests that catch regressions before deployment. Voucher handling involves real money and subscriptions, so undetected bugs have direct business impact.

**Why this priority**: Voucher service handles financial transactions (subscription grants via PIN redemption). Rate limiting prevents brute-force attacks on PINs. A bug here means revenue loss or security exposure.

**Independent Test**: Can be fully tested by running `pytest test_voucher_service.py` — verifies HMAC determinism, rate limit enforcement (player and IP thresholds), and Frappe delegation for preview/redeem flows.

**Acceptance Scenarios**:

1. **Given** a known PIN and HMAC secret, **When** `_compute_hmac` is called, **Then** it returns the same deterministic HMAC-SHA256 output every time
2. **Given** a player with fewer than 5 failed attempts, **When** `check_rate_limit` is called, **Then** it returns None (not limited)
3. **Given** a player with 5+ failed attempts within the hour, **When** `check_rate_limit` is called, **Then** it returns a positive retry_after value
4. **Given** an IP with 20+ failed attempts within the hour, **When** `check_rate_limit` is called, **Then** it returns a positive retry_after value
5. **Given** valid voucher parameters, **When** `preview` is called, **Then** it delegates to the Frappe API with the HMAC-signed PIN
6. **Given** valid voucher parameters, **When** `redeem` is called and Frappe returns an error, **Then** the failure is recorded and the error is propagated

---

### User Story 2 - Leaderboard and Stats Accuracy (Priority: P1)

A developer working on the gamification loop (XP awards, leaderboards, completion stats) needs tests that verify ranking logic, composite scoring for tie-breaking, and stats cache initialization/incrementation. Incorrect leaderboard rankings or stats directly degrade the player experience.

**Why this priority**: Leaderboards and stats are core engagement features. Incorrect rankings erode player trust. Stats drive the progress UI — wrong counts confuse users.

**Independent Test**: Can be fully tested by running `pytest test_leaderboard_service.py test_stats_service.py` — verifies ZADD/ZREVRANGE ranking, composite score tie-breaking, dense ranking, and stats HINCRBY/initialization.

**Acceptance Scenarios**:

1. **Given** XP awards for multiple players, **When** `update_leaderboards` is called, **Then** daily, weekly, and all-time sorted sets are updated correctly
2. **Given** a populated leaderboard, **When** `get_top` is called, **Then** it returns players in descending XP order with dense ranking
3. **Given** a player's rank request, **When** `get_my_rank` is called, **Then** it returns their rank plus neighboring players
4. **Given** an empty leaderboard, **When** `get_leaderboard` is called, **Then** it returns an empty list without error
5. **Given** a stats cache exists, **When** `get_stats` is called, **Then** it returns the cached hash
6. **Given** no stats cache exists, **When** `get_stats` is called, **Then** it returns None (triggering cold-start recompute by the caller)
7. **Given** lesson completion data, **When** `set_stats` is called, **Then** the stats hash is stored with 1-hour TTL
8. **Given** hierarchy and completion data, **When** `compute_stats_from_hierarchy` is called, **Then** it produces correct per-track/unit/topic counts

---

### User Story 3 - Cache Layer Reliability (Priority: P2)

A developer modifying the caching layer for hierarchy, catalog, profiles, plans, or settings needs tests that verify the cache-hit/cache-miss/invalidation pattern works correctly across all services. These services share a common pattern: check Redis first, fall back to Frappe on miss, cache the result.

**Why this priority**: All these services follow the same cache pattern. Testing them ensures the core caching contract is honored — preventing stale data, unnecessary Frappe calls, and cache-miss avalanches.

**Independent Test**: Can be fully tested by running `pytest test_hierarchy_service.py test_catalog_service.py test_profile_service.py test_plan_service.py test_settings_service.py` — each verifies cache hit, cache miss with Frappe fallback, and invalidation.

**Acceptance Scenarios**:

1. **Given** a cached hierarchy for a subject, **When** `get_hierarchy` is called, **Then** it returns the cached data without calling Frappe
2. **Given** no cached hierarchy, **When** `get_hierarchy` is called, **Then** it fetches from Frappe, caches the result with 1-hour TTL, and returns it
3. **Given** a cached hierarchy, **When** `invalidate` is called, **Then** the cache key is deleted
4. **Given** a cached catalog for a plan, **When** `get_catalog` is called, **Then** it returns the cached data without calling Frappe
5. **Given** no cached catalog, **When** `get_catalog` is called, **Then** it fetches from Frappe and caches the result (no TTL — infinite cache)
6. **Given** a player with existing purchases, **When** `get_player_catalog` is called, **Then** purchased products are excluded from the result
7. **Given** cached profiles, **When** `get_profiles_batch` is called, **Then** it returns profiles via pipeline MGET without N+1 calls
8. **Given** a missing profile, **When** profile fetch is attempted, **Then** it returns an "Anonymous XXXX" fallback
9. **Given** cached plan manifest, **When** `get_manifest` is called, **Then** it returns the cached data
10. **Given** cached gamification settings, **When** `get_gamification_settings` is called, **Then** it returns the cached settings
11. **Given** Frappe is unreachable for settings, **When** `get_gamification_settings` is called, **Then** it returns default values

---

### User Story 4 - Purchase and Review Delegation (Priority: P2)

A developer working on the purchase flow or spaced repetition review system needs tests that verify correct delegation to Frappe, duplicate detection, and error mapping.

**Why this priority**: Purchases involve financial transactions — duplicate submissions or incorrect error handling cause customer support burden. Reviews drive the learning loop.

**Independent Test**: Can be fully tested by running `pytest test_purchase_service.py test_review_service.py` — verifies Frappe delegation, duplicate checks, and response mapping.

**Acceptance Scenarios**:

1. **Given** a valid purchase request, **When** `submit_purchase` is called, **Then** it delegates to Frappe and adds the product to the player's pending set
2. **Given** a duplicate purchase request, **When** `submit_purchase` is called, **Then** it returns a conflict error
3. **Given** a player with due review items, **When** `get_due_items` is called, **Then** it returns items from Frappe (always fresh, no cache)
4. **Given** review results to submit, **When** `submit_review` is called, **Then** it delegates to Frappe and invalidates the overview cache

---

### Edge Cases

- What happens when Redis is unreachable during a cache-miss fallback to Frappe? (Services should handle gracefully or propagate the error)
- What happens when Frappe returns an empty result for hierarchy/catalog? (Cache should store empty result to prevent repeated calls)
- What happens when the HMAC secret is empty or None? (VoucherService should raise ValueError in constructor)
- What happens when `get_profiles_batch` is called with an empty list? (Should return empty dict without Redis calls)
- What happens when composite leaderboard scores tie exactly? (Earlier timestamp should win due to composite score formula)
- What happens when `compute_stats_from_hierarchy` receives an empty hierarchy? (Should return zero counts for all fields)
- What happens when `get_player_catalog` is called for a player with no purchases or pending items? (Full catalog returned)

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: Test suite MUST cover all 10 remaining FastAPI services: VoucherService, LeaderboardService, StatsService, HierarchyService, CatalogService, ProfileService, PlanService, SettingsService, PurchaseService, ReviewService
- **FR-002**: Each test file MUST follow the established pattern from Phases 1-3 (real Redis with prefix isolation, mocked FrappeClient, conftest fixtures)
- **FR-003**: Tests MUST use the existing conftest.py fixtures (redis_client, test_prefix, mock_frappe, cleanup_keys) without modification
- **FR-004**: VoucherService tests MUST verify HMAC determinism, rate limit enforcement for both player (5/hour) and IP (20/hour) thresholds, and Frappe delegation
- **FR-005**: VoucherService tests MUST exercise both Lua scripts (CHECK_LIMIT_SCRIPT and INCREMENT_SCRIPT) against real Redis
- **FR-006**: LeaderboardService tests MUST verify all three leaderboard types (daily, weekly, all-time), composite score tie-breaking, and dense ranking
- **FR-007**: StatsService tests MUST verify cache hit/miss behavior, atomic HINCRBY incrementation, and the `compute_stats_from_hierarchy` pure function
- **FR-008**: Cache-pattern services (Hierarchy, Catalog, Profile, Plan, Settings) MUST each verify the three core behaviors: cache hit returns cached data, cache miss fetches from Frappe and caches, invalidation deletes the cache key
- **FR-009**: ProfileService tests MUST verify batch fetch via pipeline MGET and the "Anonymous XXXX" fallback for missing profiles
- **FR-010**: CatalogService tests MUST verify post-cache filtering (excluding purchased and pending products)
- **FR-011**: PurchaseService tests MUST verify Frappe delegation, duplicate detection via Redis pending set, and error code mapping
- **FR-012**: ReviewService tests MUST verify overview caching (5-min TTL), always-fresh due items, and cache invalidation after submit
- **FR-013**: All tests MUST use prefix-isolated Redis keys (never FLUSHDB) and clean up via the existing autouse cleanup fixture
- **FR-014**: Test suite MUST produce at least 30 passing tests across 10 test files
- **FR-015**: All tests MUST run within the existing pytest infrastructure (`python3 -m pytest fastapi_app/tests/ -v`)

### Key Entities

- **Test File**: One per service, following the naming convention `test_{service_name}_service.py` (10 files total)
- **Service Under Test**: Each FastAPI service class with its constructor dependencies (Redis client, FrappeClient, key_prefix)
- **Redis Keys**: Prefix-isolated keys matching production patterns (e.g., `{test_prefix}hierarchy:{subject_id}`)
- **Mock FrappeClient**: Shared mock from conftest.py with configurable `.call.return_value` per test

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: All 30+ tests pass when running `python3 -m pytest fastapi_app/tests/ -v` with zero failures
- **SC-002**: All 10 service test files are created and each contains at least 2 tests
- **SC-003**: Existing Phase 1-3 tests (15 files) continue to pass without modification
- **SC-004**: Complete test suite (Phases 1-4) runs in under 30 seconds total
- **SC-005**: Each test is self-contained — can run individually without depending on other test execution order
- **SC-006**: No production Redis data is affected — all test keys use prefix isolation and are cleaned up after each test
- **SC-007**: VoucherService Lua scripts are tested against real Redis (not mocked), verifying atomic rate limit behavior
