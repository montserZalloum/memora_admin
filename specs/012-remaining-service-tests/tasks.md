# Tasks: Remaining Service Tests

**Input**: Design documents from `/specs/012-remaining-service-tests/`
**Prerequisites**: plan.md, spec.md, research.md, data-model.md, contracts/test-contracts.md

**Organization**: Tasks grouped by user story. Each story produces independent test files that can be run and verified in isolation.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependencies)
- **[Story]**: Which user story this task belongs to (e.g., US1, US2, US3, US4)
- All file paths relative to repository root

---

## Phase 1: Setup (Verification Baseline)

**Purpose**: Confirm existing test infrastructure is healthy before adding new tests

- [ ] T001 Verify existing Phase 1-3 tests pass by running `python3 -m pytest fastapi_app/tests/ -v --tb=short` and confirming all 91 tests pass with zero failures

**Checkpoint**: Baseline confirmed — all existing tests green, safe to add new test files

---

## Phase 2: User Story 1 — Voucher Service Confidence (Priority: P1)

**Goal**: Automated regression tests for VoucherService covering HMAC determinism, rate limit enforcement (Lua scripts against real Redis), and Frappe delegation for preview/redeem flows.

**Independent Test**: `python3 -m pytest fastapi_app/tests/test_voucher_service.py -v`

**Why first**: Voucher service handles financial transactions (subscription grants via PIN redemption). Rate limiting prevents brute-force attacks. Bugs = revenue loss or security exposure.

### Implementation

- [ ] T002 [US1] Create `fastapi_app/tests/test_voucher_service.py` with 7 tests:
  - `voucher_svc` fixture: `VoucherService(redis_client, frappe_client=mock_frappe, hmac_secret="test-hmac-secret")`
  - Autouse cleanup fixture: SCAN+DELETE `memora:voucher_fail:player:*` and `memora:voucher_fail:ip:*`
  - TC-VCH-01: `_compute_hmac` determinism — call twice with same PIN, verify identical hex digest matching `hmac.new()`
  - TC-VCH-02: `check_rate_limit` with no prior failures — returns `None`
  - TC-VCH-03: `check_rate_limit` after 5 player failures (via `record_failure` x5) — returns positive `retry_after`
  - TC-VCH-04: `check_rate_limit` after 20 IP failures (from different players) — returns positive `retry_after`
  - TC-VCH-05: `preview` delegates to Frappe with HMAC-signed PIN (not plaintext)
  - TC-VCH-06: `redeem` when Frappe raises `FrappeAPIError(417, "EXPIRED")` — returns error dict
  - TC-VCH-07 (edge): Constructor with empty `hmac_secret=""` — raises `ValueError`
  - Reference: contracts/test-contracts.md Section 1, research.md R-001/R-002

**Checkpoint**: VoucherService tests pass — HMAC, rate limiting (Lua scripts on real Redis), and Frappe delegation verified

---

## Phase 3: User Story 2 — Leaderboard and Stats Accuracy (Priority: P1)

**Goal**: Tests for LeaderboardService (ZADD/ZREVRANGE ranking, composite score tie-breaking, dense ranking) and StatsService (cache hit/miss, HINCRBY incrementation, `compute_stats_from_hierarchy` pure function).

**Independent Test**: `python3 -m pytest fastapi_app/tests/test_leaderboard_service.py fastapi_app/tests/test_stats_service.py -v`

**Why P1**: Leaderboards and stats are core engagement features. Wrong rankings erode trust; wrong stats confuse the progress UI.

### Implementation

- [ ] T003 [P] [US2] Create `fastapi_app/tests/test_leaderboard_service.py` with 5 tests:
  - `lb_svc` fixture: `LeaderboardService(redis_client)`
  - Autouse cleanup fixture: SCAN+DELETE `memora:lb:*` keys
  - TC-LB-01: `update_leaderboards("P1", 50, 50)` — alltime/daily/weekly sorted sets populated
  - TC-LB-02: `get_top("alltime", limit=10)` with 3 players — returns desc XP order with dense ranks 1, 2, 3
  - TC-LB-03: Dense ranking tie — P1=100, P2=100, P3=50 — P1/P2 share rank 1, P3 gets rank 3
  - TC-LB-04 (edge): `get_top("alltime")` on empty Redis — returns `[]`
  - TC-LB-05: `compute_composite_score(100, ts)` — integer part is 100, earlier timestamp yields higher fractional part
  - Reference: contracts/test-contracts.md Section 2, research.md R-003/R-004/R-005

- [ ] T004 [P] [US2] Create `fastapi_app/tests/test_stats_service.py` with 6 tests:
  - `stats_svc` fixture: `StatsService(redis_client, key_prefix=test_prefix)`
  - TC-STS-01: `get_stats` cache hit — pre-seed via `set_stats`, verify returns same data
  - TC-STS-02: `get_stats` cache miss — empty Redis, returns `None`
  - TC-STS-03: `set_stats` — stores hash in Redis with TTL ~3600s
  - TC-STS-04: `increment_completion_stats` — `completed` incremented from "5" to "6", track subkey created
  - TC-STS-05: `compute_stats_from_hierarchy` — 1 track/1 unit/1 topic/2 lessons, `completed_bits={0}` → correct counts
  - TC-STS-06 (edge): `compute_stats_from_hierarchy` with empty hierarchy — returns zero counts
  - Reference: contracts/test-contracts.md Section 3, research.md R-006

**Checkpoint**: Leaderboard ranking + stats caching verified independently

---

## Phase 4: User Story 3 — Cache Layer Reliability (Priority: P2)

**Goal**: Verify the cache-hit/cache-miss/invalidation pattern for HierarchyService, CatalogService, ProfileService, PlanService, and SettingsService. All share a common contract: check Redis first, fall back to Frappe on miss, cache the result.

**Independent Test**: `python3 -m pytest fastapi_app/tests/test_hierarchy_service.py fastapi_app/tests/test_catalog_service.py fastapi_app/tests/test_profile_service.py fastapi_app/tests/test_plan_service.py fastapi_app/tests/test_settings_service.py -v`

**Why P2**: These services are the caching backbone. Testing ensures no stale data, no unnecessary Frappe calls, and correct invalidation.

### Implementation

- [ ] T005 [P] [US3] Create `fastapi_app/tests/test_hierarchy_service.py` with 4 tests:
  - `hierarchy_svc` fixture: `HierarchyService(redis_client, frappe_client=mock_frappe, key_prefix=test_prefix)`
  - TC-HIR-01: Cache hit — pre-seed `{prefix}hierarchy:{subj}`, verify Frappe NOT called
  - TC-HIR-02: Cache miss — Frappe called, result cached with TTL ~3600s
  - TC-HIR-03: `invalidate(subj)` — Redis key deleted
  - TC-HIR-04: Cache miss with free content — `subjects_with_free_content` set updated
  - Reference: contracts/test-contracts.md Section 4, research.md R-007

- [ ] T006 [P] [US3] Create `fastapi_app/tests/test_plan_service.py` with 3 tests:
  - `plan_svc` fixture: `PlanService(redis_client, frappe_client=mock_frappe, key_prefix=test_prefix)`
  - TC-PLN-01: Cache hit — pre-seed `{prefix}plan:{plan}:manifest`, verify Frappe NOT called
  - TC-PLN-02: Cache miss — Frappe called, result cached with TTL ~3600s
  - TC-PLN-03: `invalidate(plan)` — Redis key deleted
  - Reference: contracts/test-contracts.md Section 7

- [ ] T007 [P] [US3] Create `fastapi_app/tests/test_settings_service.py` with 3 tests:
  - `settings_svc` fixture: `SettingsService(redis_client, frappe_client=mock_frappe)`
  - Autouse cleanup fixture: DELETE `memora:settings:gamification`
  - TC-SET-01: Cache hit — pre-seed hardcoded key, verify Frappe NOT called
  - TC-SET-02: Cache miss — Frappe called, result cached with TTL ~300s
  - TC-SET-03 (edge): Frappe returns `None` — returns default `GamificationSettings()` with `base_lesson_xp=100`
  - Reference: contracts/test-contracts.md Section 8, research.md R-007/R-008

- [ ] T008 [P] [US3] Create `fastapi_app/tests/test_catalog_service.py` with 4 tests:
  - `catalog_svc` fixture: `CatalogService(redis_client, frappe_client=mock_frappe, key_prefix=test_prefix)`
  - TC-CAT-01: Cache hit — pre-seed `{prefix}catalog:{plan}`, verify Frappe NOT called
  - TC-CAT-02: Cache miss — Frappe called, result cached with NO TTL (infinite)
  - TC-CAT-03: `get_player_catalog` excludes pending — SADD pending set, verify product filtered
  - TC-CAT-04: `get_player_catalog` excludes purchased — SADD access set with ALL subjects, verify filtered
  - Reference: contracts/test-contracts.md Section 5, research.md R-009

- [ ] T009 [P] [US3] Create `fastapi_app/tests/test_profile_service.py` with 4 tests:
  - `profile_svc` fixture: `ProfileService(redis_client, frappe_client=mock_frappe, key_prefix=test_prefix)`
  - TC-PRF-01: Batch cache hit — pre-seed 2 profiles, verify Frappe NOT called
  - TC-PRF-02: Partial miss — 1 cached + 1 missing, Frappe called for missing
  - TC-PRF-03: Fallback — all missing, Frappe returns `[]`, returns `Anonymous XXXX` format
  - TC-PRF-04 (edge): Empty input `[]` — returns `{}` immediately, no Redis calls
  - Reference: contracts/test-contracts.md Section 6, research.md R-010

**Checkpoint**: All 5 cache-pattern services verified — cache hit, miss+Frappe fallback, and invalidation

---

## Phase 5: User Story 4 — Purchase and Review Delegation (Priority: P2)

**Goal**: Tests for PurchaseService (Frappe delegation, duplicate detection, error code mapping) and ReviewService (overview caching with 5-min TTL, always-fresh due items, cache invalidation after submit).

**Independent Test**: `python3 -m pytest fastapi_app/tests/test_purchase_service.py fastapi_app/tests/test_review_service.py -v`

**Why P2**: Purchases involve financial transactions — duplicate submissions cause support burden. Reviews drive the learning loop.

### Implementation

- [ ] T010 [P] [US4] Create `fastapi_app/tests/test_purchase_service.py` with 4 tests:
  - `purchase_svc` fixture: `PurchaseService(redis_client, frappe_client=mock_frappe)`
  - Autouse cleanup fixture: DELETE `memora:pending:*` for test user
  - TC-PUR-01: `submit_purchase` success — Frappe called, grant ID added to `memora:pending:{user}` set
  - TC-PUR-02: Duplicate in Redis pending set — raises `HTTPException(409)`
  - TC-PUR-03: Frappe raises `FrappeAPIError(417, "DuplicateEntryError: ...")` — raises `HTTPException(409)`
  - TC-PUR-04: Frappe raises `FrappeAPIError(404, "Not Found")` — raises `HTTPException(404)`
  - Reference: contracts/test-contracts.md Section 9, research.md R-011

- [ ] T011 [P] [US4] Create `fastapi_app/tests/test_review_service.py` with 4 tests:
  - `review_svc` fixture: `ReviewService(redis_client, frappe_client=mock_frappe)`
  - Autouse cleanup fixture: DELETE `memora:reviews_overview:*` for test player
  - TC-REV-01: `get_overview` cache hit — pre-seed overview key, verify Frappe NOT called
  - TC-REV-02: `get_overview` cache miss — Frappe called, cached with TTL ~300s
  - TC-REV-03: `get_due_items` — always delegates to Frappe (no cache)
  - TC-REV-04: `submit_reviews` — Frappe called, overview cache key DELETED (invalidated)
  - Reference: contracts/test-contracts.md Section 10, research.md R-012

**Checkpoint**: Purchase delegation + error mapping and review caching verified

---

## Phase 6: Polish & Cross-Cutting Concerns

**Purpose**: Full suite validation and performance check

- [ ] T012 Run full test suite `python3 -m pytest fastapi_app/tests/ -v --tb=short` and verify:
  - All Phase 1-3 tests still pass (SC-003: no regressions)
  - All 44 new Phase 4 tests pass (SC-001: 30+ minimum exceeded)
  - All 10 test files present with 2+ tests each (SC-002)
  - Suite completes in under 30 seconds (SC-004)
  - No production Redis data affected (SC-006: prefix isolation confirmed)

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: No dependencies — run immediately
- **US1 Voucher (Phase 2)**: Depends on Phase 1 baseline — can start after verification
- **US2 Leaderboard+Stats (Phase 3)**: Depends on Phase 1 only — independent of US1
- **US3 Cache Layer (Phase 4)**: Depends on Phase 1 only — independent of US1/US2
- **US4 Purchase+Review (Phase 5)**: Depends on Phase 1 only — independent of US1/US2/US3
- **Polish (Phase 6)**: Depends on ALL user stories completing

### User Story Independence

- **US1 (Voucher)**: Fully independent — own test file, hardcoded keys, custom cleanup
- **US2 (Leaderboard+Stats)**: Fully independent — two files, no cross-dependency
- **US3 (Cache Layer)**: Fully independent — five files, all use same cache pattern but different services
- **US4 (Purchase+Review)**: Fully independent — two files, delegation pattern

### Within Each User Story

All test files within a story are marked `[P]` (parallelizable) because they:
- Target different source files
- Use different Redis key patterns
- Have no shared test state

### Parallel Opportunities

After Phase 1 baseline, ALL user stories can run in parallel:

```
Phase 1 (T001) ─┬─ Phase 2: T002 (Voucher)
                 ├─ Phase 3: T003 + T004 (Leaderboard + Stats)  [P within]
                 ├─ Phase 4: T005-T009 (5 cache services)       [P within]
                 └─ Phase 5: T010 + T011 (Purchase + Review)    [P within]
                              │
                              └─── Phase 6: T012 (Full validation)
```

Maximum parallelism: 10 tasks (T002-T011) can all run simultaneously after T001 completes.

---

## Implementation Strategy

### MVP First (User Story 1 — Voucher)

1. Complete Phase 1: Baseline verification
2. Complete Phase 2: VoucherService tests (7 tests — highest business risk)
3. **STOP and VALIDATE**: `pytest test_voucher_service.py -v` — all 7 pass
4. Financial transaction safety net established

### Incremental Delivery

1. Baseline verification → Confirmed green
2. Add US1 (Voucher, 7 tests) → Validate → Financial safety net
3. Add US2 (Leaderboard+Stats, 11 tests) → Validate → Gamification loop covered
4. Add US3 (Cache Layer, 18 tests) → Validate → Caching backbone covered
5. Add US4 (Purchase+Review, 8 tests) → Validate → Delegation pattern covered
6. Full suite validation → 44 new tests, 135+ total

### Wave-Based Strategy (from quickstart.md)

- **Wave 1** (simplest): T004 (Stats) + T003 (Leaderboard)
- **Wave 2** (cache pattern): T005-T009 (Hierarchy, Plan, Settings, Catalog, Profile)
- **Wave 3** (delegation): T010-T011 (Purchase, Review) + T002 (Voucher — most complex)

---

## Notes

- All 10 test files follow naming convention `test_{service_name}_service.py` (research.md R-014)
- Test ID codes: TC-VCH, TC-LB, TC-STS, TC-HIR, TC-CAT, TC-PRF, TC-PLN, TC-SET, TC-PUR, TC-REV
- Pattern A services (key_prefix): StatsService, HierarchyService, CatalogService, ProfileService, PlanService — auto-cleanup via conftest
- Pattern B services (hardcoded keys): VoucherService, LeaderboardService, SettingsService, PurchaseService, ReviewService — need custom cleanup fixtures
- conftest.py is NOT modified (FR-003)
- VoucherService Lua scripts (CHECK_LIMIT_SCRIPT, INCREMENT_SCRIPT) run against real Redis, not mocked (SC-007)
