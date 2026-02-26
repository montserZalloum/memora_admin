# Tasks: Player Plan Change

**Input**: Design documents from `/specs/028-player-plan-change/`
**Prerequisites**: plan.md, spec.md, research.md, data-model.md, contracts/plan-change-api.yaml, quickstart.md

**Tests**: Required per constitution Principle VIII (Test-First Coverage) and plan.md constitution check. Three test categories: Frappe API lifecycle (FrappeTestCase), FastAPI endpoints (pytest + httpx against real Redis), concurrency (concurrent plan change attempts).

**Organization**: Tasks grouped by user story. US1 (Expired Season Plan Change) is the MVP — it implements the full plan change flow including all validations, snapshot, cleanup, and freeze mechanism. US3 (Browse Available Plans) is an independent endpoint. US2 (Voluntary Plan Change) and US4 (Data Preservation) are inherently satisfied by the US1 implementation (trigger auto-detection per FR-018, snapshot per FR-005).

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependencies)
- **[Story]**: Which user story this task belongs to (e.g., US1, US2, US3)
- Include exact file paths in descriptions

---

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: Redis key builders, Pydantic models, and constants needed by all components

- [X] T001 Add `freeze_key(player_id)` and `plan_change_ts_key(player_id)` key builders, plus `FREEZE_KEY_TTL = 30` and `PLAN_CHANGE_COOLDOWN_TTL = 86400` constants in `fastapi_app/core/redis_keys.py`
- [X] T002 [P] Create Pydantic request/response models (`PlanChangeRequest`, `PlanChangeResponse`, `AvailablePlansResponse`, `GradePlanGroup`, `AvailablePlan`, `PlanChangeErrorResponse`) in `fastapi_app/models/plan_change.py`

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: DocType, dependency injection, router registration — MUST complete before any user story work

**CRITICAL**: No user story work can begin until this phase is complete

- [X] T003 Create `Memora Player Plan History` DocType: schema JSON (16 fields per data-model.md, autoname `PLHIST-.#####.`), Document class (`memora_player_plan_history.py`), and `__init__.py` in `memora_admin/memora_admin/doctype/memora_player_plan_history/`
- [X] T004 [P] Add `get_plan_change_service()` factory function and `PlanChangeServiceDep` type alias in `fastapi_app/api/deps.py`
- [X] T005 [P] Register `plan_change` router import in `fastapi_app/api/v1/router.py`
- [X] T006 Run `bench --site x.conanacademy.com migrate` to create `tabMemora Player Plan History` table

**Checkpoint**: Foundation ready — user story implementation can now begin

---

## Phase 3: User Story 1 — Expired Season Plan Change (Priority: P1) MVP

**Goal**: A player on an expired season can change to a new active plan with a complete clean slate. All progress, XP, subscriptions, leaderboard positions, and cached data are reset. Session is invalidated, requiring re-login.

**Independent Test**: Create a player on an expired season → call POST /plans/change with a valid new plan → verify: history record created with accurate snapshots, player profile updated to new plan/grade/major/season, wallet zeroed, subscriptions deleted, progress deleted, leaderboard positions removed, old JWT returns 401, all Redis caches cleared.

### Tests for User Story 1

> **NOTE: Write these tests FIRST, ensure they FAIL before implementation**

- [X] T007 [P] [US1] Write Frappe API lifecycle test for `execute_plan_change()` using `FrappeTestCase` in `memora_admin/memora_admin/doctype/memora_player_plan_history/test_memora_player_plan_history.py` — test cases: (1) successful plan change creates history record with accurate snapshot_total_xp/streak/lessons/time, snapshot_subscriptions_json, snapshot_progress_json, (2) subscriptions deleted after change, (3) progress records deleted after change, (4) wallet reset to zero (total_xp=0, current_streak=0, total_lessons=0, total_time_min=0, daily_xp_json="{}"), (5) player profile updated with new plan/grade/major/season derived from plan, (6) cooldown enforcement — second call within 24h returns error COOLDOWN_ACTIVE, (7) same-plan rejection returns SAME_PLAN, (8) invalid/unpublished plan returns INVALID_PLAN, (9) trigger_reason auto-detected ("Season Expired" vs "Voluntary Change" based on current season end_date)
- [X] T008 [P] [US1] Write FastAPI endpoint test for `POST /plans/change` using pytest + httpx + real Redis in `fastapi_app/tests/test_plan_change_endpoint.py` — test cases: (1) successful plan change returns 200 with PlanChangeResponse (success=true, history_id, previous_plan_id, new_plan_id), (2) same plan returns 400 SAME_PLAN, (3) invalid plan returns 400 INVALID_PLAN, (4) cooldown active returns 429 COOLDOWN_ACTIVE with retry_after, (5) concurrent request returns 409 PLAN_CHANGE_IN_PROGRESS, (6) freeze key exists during operation (check mid-flight), (7) freeze key removed after operation, (8) session key deleted after change (old JWT → 401)
- [X] T009 [P] [US1] Write concurrency test for plan change freeze mechanism in `fastapi_app/tests/test_plan_change_concurrency.py` — use `asyncio.gather` to fire 10 simultaneous `POST /plans/change` requests for the same player, verify exactly 1 returns 200 and remaining return 409 PLAN_CHANGE_IN_PROGRESS (SC-005). Also verify no partial state: history record count = 1, wallet reset exactly once

### Implementation for User Story 1

- [X] T010 [US1] Implement `execute_plan_change(player_id, new_plan_id)` Frappe whitelisted API in `memora_admin/api/plan_change.py` — single atomic transaction: (1) validate cooldown via latest history record `changed_at`, (2) validate plan eligibility (published + active season), (3) reject same-plan, (4) auto-detect trigger reason (FR-018: compare current season `end_date` vs today), (5) snapshot wallet fields + subscriptions JSON + progress JSON, (6) insert `Memora Player Plan History` record, (7) `frappe.db.delete` all `Memora Player Subscription` for player, (8) `frappe.db.delete` all `Memora Structure Progress` for player, (9) reset wallet (total_xp=0, current_streak=0, total_lessons=0, total_time_min=0, daily_xp_json="{}", dirty_flag=0, last_sync_at=None), (10) update player profile with new plan/grade/major/season (derived from plan, FR-023). Return `{status, history_id, previous_plan, trigger_reason}` or `{status: "error", code, message, retry_after}`
- [X] T011 [US1] Implement `PlanChangeService` class in `fastapi_app/services/plan_change.py` — orchestration methods: (1) `execute(player_id, new_plan_id, current_plan_id)` main flow, (2) `_check_cooldown(player_id)` Redis fast check via `plan_change_ts_key`, (3) `_acquire_freeze(player_id)` SET NX EX 30 via `freeze_key`, (4) `_pre_cleanup(player_id)` delete game session + SREM dirty sets (derive progress entries from SCAN `memora:progress:{player_id}:*`), (5) `_call_frappe_api(player_id, new_plan_id)` via FrappeClient, (6) `_post_cleanup(player_id)` DEL 10 direct keys + SCAN+DEL 6 patterns + SCAN `memora:lb:*` + pipeline ZREM (see data-model.md:L125-180 for exhaustive key list), (7) `_set_cooldown(player_id)` SET plan_change_ts with 24h TTL, (8) `_publish_invalidation(player_id)` PUBLISH to cache invalidation channel, (9) `_release_freeze(player_id)` DEL freeze key. Non-fatal cache cleanup per FR-022 (try/except with structlog warning). **Key builders reference**: All 10 direct DEL keys use existing builders from `redis_keys.py`: `session_key`, `game_session_key`, `wallet_key`, `access_key`, `daily_xp_key`, `player_plan_key`, `profile_key`, `reviews_overview_key`, `practice_session_key`, `pending_key`. All 6 SCAN patterns use: `progress_key`, `stats_key`, `items_learned_key`, `mastery_key`, `fsrs_card_state_key`, `fsrs_processed_key`
- [X] T012 [US1] Implement `POST /plans/change` endpoint in `fastapi_app/api/v1/endpoints/plan_change.py` — inject `CurrentUser`, `PlanChangeServiceDep`, `RedisClient`. Extract `player_id` from JWT. Call service.execute(). Map error codes to HTTP status (400 SAME_PLAN/INVALID_PLAN, 409 PLAN_CHANGE_IN_PROGRESS, 429 COOLDOWN_ACTIVE, 500 INTERNAL_ERROR). Return `PlanChangeResponse` on success
- [X] T013 [P] [US1] Add freeze check guard to `POST /sessions/start` and `POST /sessions/end` in `fastapi_app/api/v1/endpoints/sessions.py` — before session creation/completion, check `EXISTS freeze_key(player_id)`. If frozen, return 409 with error code `PLAN_CHANGE_IN_PROGRESS` and message "A plan change is in progress. Please try again shortly."
- [X] T014 [P] [US1] Add freeze check to `sync_dirty_wallets()` and `sync_dirty_progress()` in `memora_admin/tasks/sync.py` — before processing each player/entry, check `r.exists(freeze_key(player_id))`. If frozen, skip the player (leave in dirty set for next sync cycle). Import `freeze_key` from `fastapi_app.core.redis_keys`. Use synchronous `redis` client (Frappe context, not async)

**Checkpoint**: At this point, the full plan change flow works end-to-end for expired-season players. Voluntary changes (US2) also work since the trigger reason is auto-detected (FR-018). Data preservation (US4) is implemented via the snapshot in T010. FR-017 (season sequence cache invalidation) is handled by the existing `memora_admin/events/plan_change_sync.py` hook — it fires automatically on `Memora Player Profile.on_update` when the `plan` field changes, calling `invalidate_player_season_seq()` and deleting the session/plan cache keys.

---

## Phase 4: User Story 3 — Browse Available Plans (Priority: P2)

**Goal**: A player can see all plans available for switching — plans linked to active seasons (published, end_date >= today), excluding their current plan, grouped by grade and major.

**Independent Test**: Query GET /plans/available with a valid JWT → verify response includes only plans with active seasons, excludes current plan, is grouped by grade, and each plan includes grade_name, major_name, season_title.

### Tests for User Story 3

> **NOTE: Write these tests FIRST, ensure they FAIL before implementation**

- [X] T015 [P] [US3] Write FastAPI endpoint test for `GET /plans/available` using pytest + httpx in `tests/fastapi/test_available_plans_endpoint.py` — test cases: (1) returns plans grouped by grade with correct structure (GradePlanGroup with grade_id, grade_name, plans array), (2) excludes player's current plan from results, (3) only includes plans with active seasons (is_published=1, end_date >= today), (4) returns empty grades array with total=0 when no eligible plans exist, (5) each plan includes plan_id, plan_name, grade_id, grade_name, major_id, major_name, season_id, season_title

### Implementation for User Story 3

- [X] T016 [P] [US3] Implement `get_available_plans(current_plan_id)` Frappe whitelisted API in `memora_admin/api/plan_change.py` — SQL JOIN `tabMemora Academic Plan` → `tabMemora Season` (+ LEFT JOIN Grade, Major) WHERE `is_published=1 AND end_date >= CURDATE() AND name != current_plan_id`, ORDER BY grade_name, major_name, plan_name. Return `{plans: [{name, plan_name, grade, grade_name, major, major_name, season, season_title}]}`
- [X] T017 [US3] Implement `GET /plans/available` endpoint in `fastapi_app/api/v1/endpoints/plan_change.py` — inject `CurrentUser`, `RedisClient`. Extract current plan from player profile (via FrappeClient or cached player_plan_key). Call Frappe API `get_available_plans`. Group results by grade into `GradePlanGroup` list. Return `AvailablePlansResponse` with grades array and total count

**Checkpoint**: Players can now browse available plans AND execute plan changes. Both user stories work independently.

---

## Phase 5: Polish & Cross-Cutting Concerns

**Purpose**: Service restart, integration verification, edge case handling

- [X] T018 Restart FastAPI sidecar (`pkill -f "uvicorn fastapi_app.main:app"`) and verify health at `http://127.0.0.1:8002/api/v1/health/live`
- [X] T019 Restart Frappe workers (`bench restart`) to activate new Frappe API and DocType
- [ ] T020 Run quickstart.md manual verification flow: browse available plans → execute plan change → verify session invalidation → re-login → verify clean slate → verify cooldown enforcement

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: No dependencies — can start immediately
- **Foundational (Phase 2)**: Depends on Setup (T003 needs no setup deps, but T004/T005 reference PlanChangeService from Phase 3 — stub import is sufficient)
- **US1 (Phase 3)**: Depends on Phase 1 (redis_keys, models) + Phase 2 (DocType, deps, router)
- **US3 (Phase 4)**: Depends on Phase 2 (router registration, Pydantic models from Phase 1). Can run in parallel with US1
- **Polish (Phase 5)**: Depends on Phase 3 + Phase 4

### User Story Dependencies

- **US1 (P1)**: Can start after Phase 2 — no dependencies on other stories
- **US3 (P2)**: Can start after Phase 2 — no dependencies on US1 (different endpoint, different Frappe API)
- **US2 (P2)**: Fully implemented by US1 (trigger auto-detection per FR-018, cooldown per FR-004, same-plan check). No additional tasks needed
- **US4 (P3)**: Fully implemented by US1 T010 (snapshot in execute_plan_change). No additional tasks needed

### Within Each User Story

- Tests FIRST — write and verify they FAIL before implementation (Principle VIII)
- Frappe API before FastAPI service (service calls Frappe API)
- FastAPI service before FastAPI endpoint (endpoint calls service)
- Freeze checks (T013, T014) are independent of main flow and can run in parallel

### Parallel Opportunities

- T001 and T002 can run in parallel (different files)
- T003, T004, T005 can run in parallel (different files)
- T007, T008, T009 can all run in parallel (different test files, written before implementation)
- T013 and T014 can run in parallel with each other and with T010/T011 (different files)
- T015 can run in parallel with all US1 tasks (independent test file)
- T016 can run in parallel with all US1 tasks (independent endpoint)

---

## Parallel Example: User Story 1

```bash
# After Phase 2 completes, write all tests in parallel:
Task T007: "Frappe API lifecycle test in test_memora_player_plan_history.py"
Task T008: "FastAPI endpoint test in test_plan_change_endpoint.py"
Task T009: "Concurrency test in test_plan_change_concurrency.py"

# Then implement Frappe API + freeze checks in parallel:
Task T010: "Implement execute_plan_change() Frappe API in memora_admin/api/plan_change.py"
Task T013: "Add freeze check to sessions.py"
Task T014: "Add freeze check to sync.py"

# Then sequentially:
Task T011: "Implement PlanChangeService in fastapi_app/services/plan_change.py"  # needs T010
Task T012: "Implement POST /plans/change endpoint"                               # needs T011
```

---

## Implementation Strategy

### MVP First (User Story 1 Only)

1. Complete Phase 1: Setup (T001-T002)
2. Complete Phase 2: Foundational (T003-T006)
3. Write US1 tests (T007-T009) — verify they fail
4. Implement US1 (T010-T014) — verify tests pass
5. **STOP and VALIDATE**: Test plan change with a player on an expired season
6. Deploy if ready — players can use plan change immediately (plan ID provided by support or hardcoded in app)

### Incremental Delivery

1. Setup + Foundational → Foundation ready
2. Write US1 tests → Implement US1 → Verify tests pass → Deploy (MVP! Players can change plans)
3. Write US3 test → Implement US3 → Verify test passes → Deploy (Players can self-serve plan selection)
4. Polish → Full validation → Production-ready

### File Change Summary

| File | Action | Tasks |
|------|--------|-------|
| `fastapi_app/core/redis_keys.py` | MODIFY | T001 |
| `fastapi_app/models/plan_change.py` | NEW | T002 |
| `memora_admin/memora_admin/doctype/memora_player_plan_history/*` | NEW (3 files) | T003 |
| `fastapi_app/api/deps.py` | MODIFY | T004 |
| `fastapi_app/api/v1/router.py` | MODIFY | T005 |
| `memora_admin/memora_admin/doctype/memora_player_plan_history/test_*.py` | NEW (test) | T007 |
| `tests/fastapi/test_plan_change_endpoint.py` | NEW (test) | T008 |
| `tests/fastapi/test_plan_change_concurrency.py` | NEW (test) | T009 |
| `memora_admin/api/plan_change.py` | NEW | T010, T016 |
| `fastapi_app/services/plan_change.py` | NEW | T011 |
| `fastapi_app/api/v1/endpoints/plan_change.py` | NEW | T012, T017 |
| `fastapi_app/api/v1/endpoints/sessions.py` | MODIFY | T013 |
| `memora_admin/tasks/sync.py` | MODIFY | T014 |
| `tests/fastapi/test_available_plans_endpoint.py` | NEW (test) | T015 |

---

## Notes

- [P] tasks = different files, no dependencies on incomplete tasks
- [Story] label maps task to specific user story for traceability
- US2 and US4 have no dedicated tasks — they are inherently implemented by US1's flow
- **FR-017 (season sequence cache)**: Handled by existing `memora_admin/events/plan_change_sync.py` which fires on `Memora Player Profile.on_update` when `plan` field changes — calls `invalidate_player_season_seq()` + deletes session/plan cache keys via two-pronged invalidation
- The Frappe API provides automatic transaction rollback (FR-021) via Frappe's request lifecycle
- Cache cleanup failures are non-fatal (FR-022) — use try/except with structlog warning in PlanChangeService
- `decode_responses=True` in Redis pool — all responses are strings, never bytes
- All Redis key strings MUST be imported from `fastapi_app/core/redis_keys.py` — no inline `f"memora:..."` strings
- Sync tasks (T014) use synchronous `redis` client via `get_memora_redis()`, not `redis.asyncio`
- **Key builder verification**: All key builders referenced in data-model.md:L125-180 (direct DEL keys: `session_key`, `game_session_key`, `wallet_key`, `access_key`, `daily_xp_key`, `player_plan_key`, `profile_key`, `reviews_overview_key`, `practice_session_key`, `pending_key`; SCAN patterns: `progress_key`, `stats_key`, `items_learned_key`, `mastery_key`, `fsrs_card_state_key`, `fsrs_processed_key`) have been verified to exist in `fastapi_app/core/redis_keys.py`
