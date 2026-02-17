# Research: Remaining Service Tests

**Feature**: 012-remaining-service-tests | **Date**: 2026-02-17

## R-001: VoucherService Constructor — `hmac_secret` Requirement

**Decision**: VoucherService constructor validates `hmac_secret` is non-empty, raising `ValueError` if not set. Tests must pass a real string (e.g., `"test-hmac-secret"`) and separately test the ValueError edge case.

**Rationale**: The constructor has an explicit guard: `if not hmac_secret: raise ValueError(...)`. Unlike other services that accept `key_prefix`, VoucherService uses hardcoded `memora:voucher_fail:*` keys, not the prefix pattern.

**Alternatives considered**: Mocking the constructor — rejected because it defeats the purpose of testing the real guard logic.

## R-002: VoucherService Key Prefix — Hardcoded Keys

**Decision**: VoucherService uses hardcoded key patterns `memora:voucher_fail:player:{id}` and `memora:voucher_fail:ip:{ip}` — it does NOT accept a `key_prefix` parameter.

**Rationale**: Reading `voucher.py:104,109`, the keys are built inline without `self.prefix`. Tests must work with these hardcoded keys and rely on the conftest `cleanup_keys` fixture's SCAN pattern matching `test_prefix` for cleanup. Since these keys won't match the test prefix, tests must manually clean up voucher rate limit keys.

**Impact**: Each voucher test must clean up its own `memora:voucher_fail:*` keys in a fixture or use unique player_id/IP values and clean up in teardown.

## R-003: LeaderboardService Key Prefix — Hardcoded `LB_PREFIX`

**Decision**: LeaderboardService uses hardcoded `LB_PREFIX = "memora:lb"`. It does NOT accept `key_prefix`. Same cleanup concern as VoucherService.

**Rationale**: Reading `leaderboard.py:29,91-126`, all keys are built from the global `LB_PREFIX` constant. Tests must manually clean up `memora:lb:*` keys.

**Impact**: Leaderboard tests need their own cleanup fixture for `memora:lb:*` keys to avoid polluting production data.

## R-004: LeaderboardService Time Dependency — `_get_key` Uses `datetime.now()`

**Decision**: `_get_key()` calls `datetime.now(AMMAN_TZ)` to generate date-based key suffixes for daily/weekly leaderboards. Tests should NOT mock time; instead, they should call `_get_key()` to discover the actual key and verify against it, or directly use `update_leaderboards()` which internally calls `_get_key()`.

**Rationale**: Mocking `datetime.now` across async code is fragile. Since we're testing the service layer (not time boundaries), we verify that keys were written to the correct sorted sets by checking the keys that `_get_key` would generate.

**Alternatives considered**: Freezegun or `unittest.mock.patch("...datetime")` — rejected as overkill for service-level tests. Time boundary tests belong in endpoint/integration tests (Phase 5-6).

## R-005: `compute_composite_score` — Module-Level Function

**Decision**: `compute_composite_score` is a module-level function (not a method), importable directly from `fastapi_app.services.leaderboard`. Tests should test it as a pure function.

**Rationale**: Reading `leaderboard.py:32`, it's defined at module level. The formula: `xp + (1.0 - (timestamp % 1e9) / 1e9)`. Integer part = XP, fractional part = inverted timestamp.

## R-006: StatsService `key_prefix` Pattern

**Decision**: StatsService accepts `key_prefix` (default `"memora:"`) and uses it in `_stats_key()`. Tests use `test_prefix` fixture for isolation. This matches the Phase 2-3 pattern exactly.

**Rationale**: Reading `stats.py:34`, `self.prefix = key_prefix`. Clean, no special handling needed.

## R-007: Cache-Pattern Services — Shared Pattern

**Decision**: HierarchyService, CatalogService, ProfileService, PlanService, and SettingsService all follow the same cache pattern:
1. Check Redis (GET)
2. On miss, call Frappe API (mock)
3. Cache result (SET with optional TTL)
4. Invalidation (DELETE)

All except SettingsService accept `key_prefix` parameter. SettingsService uses hardcoded `CACHE_KEY = "memora:settings:gamification"`.

**Rationale**: Direct reading of each service file confirmed the pattern. Services with `key_prefix`:
- HierarchyService: `self.prefix` used in `_cache_key()` and `_free_content_subjects_key()`
- CatalogService: `self.prefix` used in `_cache_key()` and pipeline access/pending lookups
- ProfileService: `self.prefix` used in `_cache_key()`
- PlanService: `self.prefix` used in `_cache_key()`
- SettingsService: `CACHE_KEY` hardcoded (NOT using prefix) — needs manual cleanup

## R-008: SettingsService Fallback Behavior

**Decision**: When Frappe returns `None` (empty/unavailable), SettingsService returns `GamificationSettings()` with defaults. Test must verify this fallback.

**Rationale**: Reading `settings.py:59-62`: `if not result: return GamificationSettings()`. This is an explicit design decision — the game must always have valid settings even if Frappe is down.

## R-009: CatalogService Player Filtering Logic

**Decision**: `get_player_catalog()` does a pipeline fetch of `access:{player_id}` and `pending:{player_id}` sets, then excludes products where:
1. `product_grant_id` is in the pending set (pending purchase)
2. ALL subjects in the grant are in the access set (already purchased)

**Rationale**: Reading `catalog.py:102-132`. The key insight is the "ALL subjects" check — partial access doesn't exclude the product.

**Impact**: Tests need to set up Redis access and pending sets with the correct key patterns, using the service's `key_prefix`.

## R-010: ProfileService Batch Pattern

**Decision**: `get_profiles_batch()` uses pipeline MGET for cache hits, Frappe batch for misses, and fallback for still-missing. Empty input returns `{}` immediately.

**Rationale**: Reading `profile.py:61-123`. The `_apply_fallback()` generates `"Anonymous {last_4}"` format. Frappe batch is capped at `MAX_FRAPPE_BATCH = 50`.

## R-011: PurchaseService Error Mapping

**Decision**: PurchaseService maps FrappeAPIError to HTTP exceptions:
- `status_code=417 + "DuplicateEntryError"` → 409 Conflict
- `status_code=404` → 404 Not Found
- `status_code=417` (other) → 400 Bad Request
- Other → 502 Bad Gateway

**Rationale**: Reading `purchase.py:89-118`. Tests must raise `FrappeAPIError` with appropriate codes and verify the resulting HTTPException.

## R-012: ReviewService Cache Strategy

**Decision**: Overview cached with 5-min TTL. Due items always fresh (no cache). Submit invalidates overview cache after Frappe call.

**Rationale**: Reading `review.py:28-92`. The `REVIEW_OVERVIEW_KEY` uses string format template: `"memora:reviews_overview:{player_id}"`. No `key_prefix` parameter — hardcoded keys, similar to VoucherService.

**Impact**: ReviewService tests need manual key cleanup for `memora:reviews_overview:*` keys.

## R-013: Services Without `key_prefix` — Cleanup Strategy

**Decision**: Services with hardcoded keys (VoucherService, LeaderboardService, SettingsService, ReviewService, PurchaseService) need custom cleanup fixtures. Strategy: use unique identifiers per test and add a cleanup fixture that deletes the specific keys.

**Rationale**: The conftest `cleanup_keys` fixture only scans for `{test_prefix}*` keys. Hardcoded keys won't match.

**Approach**:
- VoucherService: Clean `memora:voucher_fail:player:*` and `memora:voucher_fail:ip:*` for test player/IP
- LeaderboardService: Clean `memora:lb:*` keys using SCAN after each test
- SettingsService: Clean `memora:settings:gamification` key
- ReviewService: Clean `memora:reviews_overview:*` for test player
- PurchaseService: Clean `memora:pending:*` for test user

## R-014: Existing Test Naming Convention

**Decision**: Follow `test_tc_{CODE}_{NUMBER}_description` pattern with uppercase service code abbreviation.

**Rationale**: Existing tests use: TC-SS (Session), TC-DS (Device), TC-GS (GameSession), TC-OTP, TC-RL (RateLimiter), TC-ACC (Access), TC-PRG (Progress), TC-WAL (Wallet).

**New codes**:
- TC-VCH: VoucherService
- TC-LB: LeaderboardService
- TC-STS: StatsService
- TC-HIR: HierarchyService
- TC-CAT: CatalogService
- TC-PRF: ProfileService
- TC-PLN: PlanService
- TC-SET: SettingsService
- TC-PUR: PurchaseService
- TC-REV: ReviewService
