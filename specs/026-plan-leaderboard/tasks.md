# Tasks: Plan-Scoped Leaderboard

**Input**: Design documents from `/specs/026-plan-leaderboard/`
**Prerequisites**: spec.md, plan.md, research.md, data-model.md, contracts/leaderboard-api.yaml, quickstart.md

**Tests**: Required per Constitution Principle VIII (Test-First Coverage) and quickstart.md testing strategy.

**Organization**: Tasks grouped by user story matching spec.md numbering.

## Story Traceability (spec.md → tasks.md)

| Spec Story | Label | Priority | Tasks Phase |
|------------|-------|----------|-------------|
| US1 — View Top Students in My Plan | [US1] | P1 | Phase 4 |
| US2 — See My Rank Among Plan Peers | [US2] | P1 | Phase 5 |
| US3 — Filter Leaderboard by Subject | [US3] | P2 | Phase 4 + Phase 5 (embedded via `subject_id` param) |
| US4 — Dual-Write for Future Global Leaderboard | [US4] | P3 | Phase 3 (implemented first for data accumulation) |

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependencies)
- **[Story]**: Which user story this task belongs to — matches spec.md numbering
- Include exact file paths in descriptions

---

## Phase 1: Setup (Redis Key Infrastructure)

**Purpose**: Define plan-scoped leaderboard Redis key builders — foundational for all other work

- [X] T001 Add 4 plan-scoped leaderboard key builder functions (`lb_daily_plan_key`, `lb_weekly_plan_key`, `lb_daily_plan_subject_key`, `lb_weekly_plan_subject_key`) in `fastapi_app/core/redis_keys.py`
  - Daily plan: `memora:lb:daily:{date}:plan:{plan_id}` (TTL 48h)
  - Daily plan+subject: `memora:lb:daily:{date}:plan:{plan_id}:subject:{subject_id}` (TTL 48h)
  - Weekly plan: `memora:lb:weekly:{friday}:plan:{plan_id}` (TTL 8d)
  - Weekly plan+subject: `memora:lb:weekly:{friday}:plan:{plan_id}:subject:{subject_id}` (TTL 8d)
  - Each function must have full docstring (Type, Producers, Consumers, TTL)
  - Add TTL constants: `PLAN_DAILY_KEY_TTL = 48 * 3600`, `PLAN_WEEKLY_KEY_TTL = 8 * 86400`

---

## Phase 2: Foundational (Model + Edge Case Changes)

**Purpose**: Update Pydantic models to match contract and handle edge cases — blocking for endpoint changes

**CRITICAL**: No endpoint or service read-path work can begin until this phase is complete

- [X] T002 [P] Update `LeaderboardType` literal in `fastapi_app/models/leaderboard.py` from `Literal["daily", "weekly", "alltime"]` to `Literal["daily", "weekly"]`
- [X] T003 [P] Make `rank` field nullable (`int | None`) in `MyRankResponse` in `fastapi_app/models/leaderboard.py` — per contract, unranked players get `rank: null`

**Checkpoint**: Models match the OpenAPI contract in `contracts/leaderboard-api.yaml`

---

## Phase 3: User Story 4 — Write Path (Dual-Write Plan-Scoped + Global) (Priority: P3, implemented first for data accumulation)

**Goal**: Every XP award writes to both plan-scoped AND global leaderboard ZSETs in a single pipeline, so plan-scoped data accumulates from the moment of deployment.

**Independent Test**: Call `update_leaderboards()` with a `plan_id`, verify plan-scoped ZSETs created with correct keys and TTLs, AND global ZSETs still written.

**Spec mapping**: US4 (Dual-Write for Future Global Leaderboard) — acceptance scenarios US4-1 and US4-2.

### Implementation

- [X] T004 [US4] Add `plan_id: str | None = None` parameter to `LeaderboardService.update_leaderboards()` in `fastapi_app/services/leaderboard.py`
  - Import new key builders from `redis_keys.py`
  - When `plan_id` is not None, add ZINCRBY + EXPIRE for plan-scoped daily key (TTL 48h) and weekly key (TTL 8d) to the existing pipeline
  - When `subject_id` is also provided, add ZINCRBY + EXPIRE for plan+subject daily and weekly keys
  - Keep ALL existing global writes unchanged (alltime, daily, weekly, subject variants, daily_xp hash)
  - Use `PLAN_DAILY_KEY_TTL` and `PLAN_WEEKLY_KEY_TTL` constants from redis_keys.py

- [X] T005 [US4] Pass `user.plan` to `update_leaderboards()` call in `fastapi_app/api/v1/endpoints/sessions.py` (around line 389)
  - Add `plan_id=user.plan` kwarg to the existing call
  - No other changes needed in this file

**Checkpoint**: XP awards now dual-write to plan-scoped + global ZSETs. Read path still uses global keys.

---

## Phase 4: User Story 1 + 3 — Read Path: Plan-Scoped Top 20 with Subject Filter (Priority: P1/P2)

**Goal**: `GET /leaderboard/{lb_type}` reads from plan-scoped ZSETs (resolved from JWT `user.plan`), returns top 20 entries with `is_me` flag, no `limit` parameter. Optional `subject_id` filter scopes to a single subject within the plan.

**Independent Test**: With plan-scoped ZSET populated, `GET /leaderboard/daily` returns entries scoped to the player's plan, max 20 entries. With `subject_id` param, returns only subject-specific XP rankings within the plan.

**Spec mapping**: US1 (View Top Students) — acceptance scenarios US1-1..3. US3 (Subject Filter) — acceptance scenarios US3-1..3.

### Implementation

- [X] T006 [US1] Add `_get_plan_key()` method to `LeaderboardService` in `fastapi_app/services/leaderboard.py`
  - Parameters: `lb_type: str`, `plan_id: str`, `subject_id: str | None = None`
  - Returns the plan-scoped key using the builders from T001
  - Computes date/friday the same way as `_get_key()` (extract shared date helper if clean)
  - Only supports "daily" and "weekly" (raise ValueError for "alltime")

- [X] T007 [US1] [US3] Add `plan_id: str | None = None` parameter to `LeaderboardService.get_top()` in `fastapi_app/services/leaderboard.py`
  - When `plan_id` is provided, use `_get_plan_key()` instead of `_get_key()`
  - Keep `limit` parameter on the service method for backward compatibility
  - Subject filtering works via `subject_id` param passed through to `_get_plan_key()`

- [X] T008 [US1] [US3] Update `get_leaderboard` endpoint in `fastapi_app/api/v1/endpoints/leaderboard.py`
  - Change `LeaderboardTypeParam` from `Literal["daily", "weekly", "alltime"]` to `Literal["daily", "weekly"]`
  - Remove `limit` parameter entirely — hardcode `limit=20` in the `get_top()` call
  - Pass `plan_id=user.plan` to `leaderboard_service.get_top()`
  - Pass `plan_id=user.plan` when computing `total_players` ZCARD (use `_get_plan_key` for the key)
  - Handle `user.plan is None`: return `LeaderboardResponse(leaderboard_type=lb_type, subject_id=subject_id, entries=[], total_players=0)` — per spec edge case "student with no plan assigned"

**Checkpoint**: `GET /leaderboard/daily` and `GET /leaderboard/weekly` return plan-scoped top 20, with optional subject filter.

---

## Phase 5: User Story 2 + 3 — Read Path: My Rank + Neighbors with Subject Filter (Priority: P1/P2)

**Goal**: `GET /leaderboard/{lb_type}/me` returns the player's rank, XP, gap to next, and ±2 neighbors within their plan. Unranked players get `rank: null`. Optional `subject_id` filter scopes to a single subject.

**Independent Test**: With plan-scoped ZSET populated, `GET /leaderboard/daily/me` returns correct dense rank, neighbors, and `xp_to_next` scoped to the plan. With subject filter, ranks reflect only that subject's XP.

**Spec mapping**: US2 (My Rank Among Plan Peers) — acceptance scenarios US2-1..3. US3 (Subject Filter) — scenario US3-3.

### Implementation

- [X] T009 [US2] [US3] Add `plan_id: str | None = None` parameter to `LeaderboardService.get_my_rank()` in `fastapi_app/services/leaderboard.py`
  - When `plan_id` is provided, use `_get_plan_key()` instead of `_get_key()`
  - For unranked players: return `rank: None` (not `total + 1`) per contract — update the unranked return block
  - Subject filtering works via `subject_id` param passed through to `_get_plan_key()`

- [X] T010 [US2] [US3] Update `get_my_rank` endpoint in `fastapi_app/api/v1/endpoints/leaderboard.py`
  - `LeaderboardTypeParam` already restricted by T008 (shared type alias)
  - Pass `plan_id=user.plan` to `leaderboard_service.get_my_rank()`
  - Handle `user.plan is None`: return `MyRankResponse(rank=None, xp=0, xp_to_next=None, neighbors=[], total_players=0)` — per spec edge case "student with no plan assigned"

**Checkpoint**: Both leaderboard endpoints fully plan-scoped with subject filter. Contract matches `contracts/leaderboard-api.yaml`.

---

## Phase 6: Tests (Constitution Principle VIII)

**Purpose**: Unit, integration, and endpoint tests per quickstart.md testing strategy and Constitution Principle VIII (Test-First is waived for this feature since we are modifying existing code, but test coverage is mandatory).

- [X] T011 [P] Write unit tests for plan-scoped write path in `fastapi_app/tests/test_leaderboard_service.py`
  - Test `update_leaderboards()` with `plan_id`: verify plan-scoped ZINCRBY keys created with correct format
  - Test `update_leaderboards()` with `plan_id` + `subject_id`: verify plan+subject keys created
  - Test that global keys (alltime, daily, weekly) are still written when `plan_id` is provided
  - Test TTL values: 48h for plan daily, 8d for plan weekly
  - Test `update_leaderboards()` with `plan_id=None`: verify no plan-scoped keys created (backward compat)

- [X] T012 [P] Write unit tests for plan-scoped read path in `fastapi_app/tests/test_leaderboard_service.py`
  - Test `get_top()` with `plan_id`: reads from plan-scoped key, not global
  - Test `get_top()` with `plan_id` + `subject_id`: reads from plan+subject key
  - Test `get_my_rank()` with `plan_id`: rank computed within plan scope
  - Test `get_my_rank()` unranked player returns `rank: None` (not `total + 1`)
  - Test `_get_plan_key()` raises ValueError for "alltime"

- [X] T013 [P] Write integration test for plan isolation in `fastapi_app/tests/test_leaderboard_service.py`
  - Seed two plans (PLAN-A, PLAN-B) with different players and XP
  - Verify `get_top(plan_id="PLAN-A")` returns only PLAN-A players
  - Verify `get_top(plan_id="PLAN-B")` returns only PLAN-B players
  - Verify global keys contain players from BOTH plans

- [X] T014 Write endpoint contract tests in `fastapi_app/tests/test_leaderboard_endpoints.py`
  - Test `GET /leaderboard/alltime` returns 422 (invalid lb_type)
  - Test `GET /leaderboard/daily` returns LeaderboardResponse shape (max 20 entries, `is_me` flag, `total_players`)
  - Test `GET /leaderboard/daily/me` returns MyRankResponse shape (nullable rank, `xp_to_next`, neighbors)
  - Test `GET /leaderboard/daily?subject_id=SUBJ-001` returns subject-filtered results
  - Test response for player with no plan returns empty leaderboard (not 500)

- [X] T015 Run full test suite and verify zero regressions (`pytest fastapi_app/tests/test_leaderboard_service.py fastapi_app/tests/test_leaderboard_endpoints.py -v`)

**Checkpoint**: All tests pass. Existing tests unbroken.

---

## Phase 7: Polish & Cross-Cutting Concerns

**Purpose**: Cleanup, performance validation, and deployment verification

- [X] T016 Replace `self._get_key("alltime")` calls in `update_leaderboards()` (lines 342, 370 of `fastapi_app/services/leaderboard.py`) with direct `lb_alltime_key()` / `lb_alltime_key(subject_id)` calls, then remove the "alltime" branch from `_get_key()` so read-path callers cannot accidentally use it
  - Note: `_get_key()` is also called for "daily" and "weekly" in `update_leaderboards()` — those must remain

- [X] T017 [P] Sanity-check endpoint response times: run `time curl` against both endpoints and verify <20ms (SC-002). Log pipeline command count to confirm 1 RTT (SC-003 proxy)

- [X] T018 Restart FastAPI server and verify endpoints respond correctly (`pkill -f "uvicorn fastapi_app.main:app"`, wait 3s, `curl http://127.0.0.1:8002/api/v1/health/live`)

**FR-010 Note**: The subject filter dropdown data (FR-010) requires no new code. Per research decision R7, the existing plan manifest endpoint (`GET /api/v1/plans/{plan_id}/manifest`) already returns all subjects in the player's plan. The mobile app uses this at startup.

---

## Dependencies & Execution Order

### Phase Dependencies

- **Phase 1 (Setup)**: No dependencies — start immediately
- **Phase 2 (Models)**: No dependencies — can run in parallel with Phase 1
- **Phase 3 (Write Path)**: Depends on Phase 1 (key builders)
- **Phase 4 (Top 20 + Filter)**: Depends on Phase 1 (key builders) + Phase 2 (models)
- **Phase 5 (My Rank + Filter)**: Depends on Phase 1 (key builders) + Phase 2 (models)
- **Phase 6 (Tests)**: Depends on Phases 3, 4, 5 (code must exist to test)
- **Phase 7 (Polish)**: Depends on Phase 6 (tests pass first)

### Within Each Phase

- T001 must complete before T004, T006, T009 (key builders)
- T002/T003 must complete before T008, T010 (model types)
- T004 must complete before T005 (service before caller)
- T006 must complete before T007 (helper before consumer)
- T007 must complete before T008 (service before endpoint)
- T009 must complete before T010 (service before endpoint)
- T011-T014 can run in parallel (different test files/concerns)
- T015 depends on T011-T014 (run after all tests written)
- T016 must complete before T017 (cleanup before perf check)

### Parallel Opportunities

- **Phase 1 + Phase 2**: Can run in parallel (different files)
- **T002 + T003**: Can run in parallel (same file but independent fields)
- **Phase 4 + Phase 5**: Can run in parallel after Phase 1+2 (different methods/endpoints)
- **T011 + T012 + T013 + T014**: Can run in parallel (independent test files/sections)
- **T017**: Can run in parallel with T016 (different concerns)

---

## Parallel Example: Phase 4 + Phase 5

```bash
# These can run simultaneously after Phase 1+2 (different service methods + different endpoint functions):
Task: "T006-T008 — Top 20 read path in leaderboard.py service + endpoint"
Task: "T009-T010 — My Rank read path in leaderboard.py service + endpoint"
```

---

## Implementation Strategy

### MVP First (Write Path Only — Phase 1 + 3)

1. Complete Phase 1: Key builders
2. Complete Phase 3: Write path dual-write
3. **STOP and VALIDATE**: Plan-scoped ZSETs accumulate data
4. Deploy — data starts accumulating even before read path is ready

### Full Delivery

1. Phase 1 + Phase 2 in parallel → Key builders + models ready
2. Phase 3: Write path → Dual-write active
3. Phase 4 + Phase 5 in parallel → Both read endpoints plan-scoped with subject filter
4. Phase 6: Tests → Validate all behavior
5. Phase 7: Polish + perf check + deploy → Ship

### Files Modified Summary

| File | Tasks | Change |
|------|-------|--------|
| `fastapi_app/core/redis_keys.py` | T001 | +4 key builder functions, +2 TTL constants |
| `fastapi_app/models/leaderboard.py` | T002, T003 | LeaderboardType enum, MyRankResponse.rank nullable |
| `fastapi_app/services/leaderboard.py` | T004, T006, T007, T009, T016 | Write path + read path + plan key helper + alltime cleanup |
| `fastapi_app/api/v1/endpoints/leaderboard.py` | T008, T010 | Endpoint parameter changes, no-plan guard |
| `fastapi_app/api/v1/endpoints/sessions.py` | T005 | Pass user.plan to update_leaderboards (parameter-only) |
| `fastapi_app/tests/test_leaderboard_service.py` | T011, T012, T013 | Unit + integration tests for plan-scoped service |
| `fastapi_app/tests/test_leaderboard_endpoints.py` | T014 | Endpoint contract tests |

---

## Notes

- [P] tasks = different files, no dependencies
- [Story] labels match spec.md user story numbering for traceability
- US3 (Subject Filter) is embedded in US1/US2 phases since it's a query parameter, not a separate endpoint
- Total pipeline growth: 4-8 extra commands per XP award (still 1 RTT)
- No new endpoints — same paths, different key resolution
- `alltime` type returns 422 automatically via Literal validation (FastAPI handles this)
- Plan keys auto-expire (48h/8d) — no archival jobs needed
- FR-010 (subject dropdown) covered by existing plan manifest endpoint — no new code
- FR-008 (plan change) is a design property of plan_id-in-key — no explicit code needed
