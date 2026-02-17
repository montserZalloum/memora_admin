# Tasks: FastAPI Test Foundation + Pure Function Tests

**Input**: Design documents from `/specs/009-fastapi-test-foundation/`
**Prerequisites**: plan.md, spec.md, research.md, data-model.md, contracts/fixtures.md, quickstart.md

**Tests**: Tests ARE the feature — this is a test infrastructure + pure function test spec. All tasks produce test code.

**Organization**: Tasks grouped by user story. US3 (infrastructure) is foundational; US1 and US2 depend on it.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependencies)
- **[Story]**: Which user story this task belongs to (e.g., US1, US2, US3)
- Include exact file paths in descriptions

## Path Conventions

- **Test directory**: `fastapi_app/tests/`
- **Source under test**: `fastapi_app/services/wallet.py`, `fastapi_app/core/constants.py`
- **Config**: `pyproject.toml` (repository root)

---

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: Configure pytest and create test directory structure

- [X] T001 Add `[tool.pytest.ini_options]` section to `pyproject.toml` with `testpaths = ["fastapi_app/tests"]`, `asyncio_mode = "auto"`, and marker definitions (`slow`, `integration`)
- [X] T002 [P] Create empty `fastapi_app/tests/__init__.py` package marker

---

## Phase 2: Foundational — Test Infrastructure Bootstrap (US3 tasks, BLOCKS US1+US2)

**Purpose**: Build the shared `conftest.py` with all 9 fixtures that subsequent test phases (2-7, ~225 tests) depend on. This IS User Story 3.

**⚠️ CRITICAL**: No test tasks (US1, US2) can begin until this phase is complete

### Implementation

- [X] T003 [US3] Create `fastapi_app/tests/conftest.py` with module-level settings override: instantiate `_test_settings = Settings(redis_url="redis://127.0.0.1:13000", jwt_secret="test-secret-key-for-unit-tests", jwt_algorithm="HS256", bitmap_json_path="/tmp/test-bitmaps", frappe_url="http://localhost:8000", frappe_site="test.local", frappe_api_key="test-key", frappe_api_secret="test-secret", voucher_hmac_secret="test-hmac-secret")`, call `get_settings.cache_clear()`, and monkey-patch `fastapi_app.core.config.get_settings = lambda: _test_settings` — MUST execute before any FastAPI app import
- [X] T004 [US3] Add `test_prefix` fixture (function scope) to `fastapi_app/tests/conftest.py` — returns `"test:{8-char-hex}:"` string using `uuid.uuid4().hex[:8]` for per-test Redis key namespace isolation
- [X] T005 [US3] Add `redis_client` fixture (function scope, async) to `fastapi_app/tests/conftest.py` — creates `redis.asyncio.Redis.from_url("redis://127.0.0.1:13000", decode_responses=True)`, yields client, teardown calls `await client.aclose()`
- [X] T006 [US3] Add `cleanup_keys` fixture (function scope, autouse, async) to `fastapi_app/tests/conftest.py` — depends on `redis_client` and `test_prefix`, yields then runs SCAN+DELETE loop: `cursor, keys = await redis_client.scan(cursor, match=f"{test_prefix}*", count=1000)` until cursor=0, deleting all matched keys. MUST NOT use FLUSHDB (FR-009)
- [X] T007 [US3] Add `mock_frappe` fixture (function scope) to `fastapi_app/tests/conftest.py` — returns `AsyncMock` with pre-configured methods: `.call` → `AsyncMock(return_value=None)`, `.get_grant_keys` → `AsyncMock(return_value=[])`, `.create_subscription` → `AsyncMock(return_value={})`, `.close` → `AsyncMock()`
- [X] T008 [US3] Add `make_player_token` fixture (function scope) to `fastapi_app/tests/conftest.py` — returns callable factory `(player_id="PLAYER-TEST-001", plan_id="PLAN-TEST-001", display_name="Test Player") -> (token_str, family_id)` using `create_access_token` from `fastapi_app.core.security` with a generated `uuid4()` family_id
- [X] T009 [US3] Add `make_admin_token` fixture (function scope) to `fastapi_app/tests/conftest.py` — returns callable factory `(email="admin@test.local") -> (token_str, family_id)` using `create_access_token` with `role="System Manager"`, `plan_id="PLAN-ADMIN"`, and a generated `uuid4()` family_id
- [X] T010 [US3] Add `app_client` fixture (function scope, async) to `fastapi_app/tests/conftest.py` — creates `httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test")` with dependency overrides: `app.dependency_overrides[get_redis] = lambda: redis_client`, `app.dependency_overrides[get_frappe_client] = lambda: mock_frappe`; yields client; teardown calls `await client.aclose()` and clears `app.dependency_overrides`
- [X] T011 [US3] Add `authed_client` fixture (function scope, async) to `fastapi_app/tests/conftest.py` — depends on `app_client`, `redis_client`, `make_player_token`; calls `make_player_token()` to get `(token, family_id)`; seeds `memora:session:{player_id}` in Redis with `json.dumps({"fid": family_id})`; sets `Authorization: Bearer {token}` header on client; returns `(client, token, player_id, family_id)` tuple
- [X] T012 [US3] Add `admin_client` fixture (function scope, async) to `fastapi_app/tests/conftest.py` — depends on `app_client`, `redis_client`, `make_admin_token`; calls `make_admin_token()` to get `(token, family_id)`; seeds `memora:session:{email}` in Redis with `json.dumps({"fid": family_id})`; sets `Authorization: Bearer {token}` header on client; returns `(client, token, email, family_id)` tuple

**Checkpoint**: Run `python3 -m pytest fastapi_app/tests/ --co` — all fixtures should be discovered with no import errors. Verify `redis_client` can connect to `redis://127.0.0.1:13000`.

---

## Phase 3: User Story 1 — XP Calculation Tests (Priority: P1) 🎯 MVP

**Goal**: 11 tests covering every branch of `calculate_xp_award` (fresh/replay, hearts, streak multiplier, edge cases)

**Independent Test**: `python3 -m pytest fastapi_app/tests/test_xp_calculation.py -k "not level" -v` — 11 passing tests

### Implementation

- [X] T013 [US1] Create `fastapi_app/tests/test_xp_calculation.py` with import of `calculate_xp_award` from `fastapi_app.services.wallet` and `calculate_level` from `fastapi_app.core.constants`
- [X] T014 [US1] Add `test_fresh_base_xp` — fresh completion with `lesson_xp=0, base_xp=50, streak=0, max_mult=50, is_replay=False, replay_xp=0` → assert result == 50
- [X] T015 [US1] Add `test_fresh_lesson_xp_override` — fresh completion with `lesson_xp=75, base_xp=50` → assert result uses `lesson_xp` (75) not `base_xp`
- [X] T016 [US1] Add `test_replay_fixed_amount` — replay with `is_replay=True, replay_xp=10, base_xp=50, lesson_xp=75` → assert result is based on `replay_xp` (10), not base/lesson
- [X] T017 [US1] Add `test_replay_ignores_hearts` — replay with `is_replay=True, replay_xp=10, hearts_remaining=5, xp_per_heart=3` → assert hearts bonus NOT added (result based on 10 only)
- [X] T018 [US1] Add `test_hearts_bonus_fresh` — fresh with `base_xp=50, hearts_remaining=3, xp_per_heart=5, streak=0` → assert result == 65 (50 + 15 hearts bonus)
- [X] T019 [US1] Add `test_streak_multiplier_linear` — fresh with `base_xp=100, streak=10, max_mult=50` → assert result == 110 (100 × 1.10)
- [X] T020 [US1] Add `test_streak_multiplier_capped` — fresh with `base_xp=100, streak=100, max_mult=50` → assert result == 150 (capped at 1.50×)
- [X] T021 [US1] Add `test_streak_zero_no_bonus` — fresh with `base_xp=100, streak=0, max_mult=50` → assert result == 100 (multiplier 1.0)
- [X] T022 [US1] Add `test_result_floored` — fresh with `base_xp=33, streak=1, max_mult=50` → assert result == 33 (33 × 1.01 = 33.33 → floored to 33)
- [X] T023 [US1] Add `test_zero_inputs` — fresh with `base_xp=0, lesson_xp=0, streak=0, hearts=0` → assert result == 0
- [X] T024 [US1] Add `test_replay_with_streak` — replay with `replay_xp=10, streak=10, max_mult=50` → assert result == 11 (10 × 1.10 = 11.0)

**Checkpoint**: `python3 -m pytest fastapi_app/tests/test_xp_calculation.py -k "not level" -v` — 11 tests pass

---

## Phase 4: User Story 2 — Level Calculation Tests (Priority: P1)

**Goal**: 4 tests covering boundary and edge cases of `calculate_level`

**Independent Test**: `python3 -m pytest fastapi_app/tests/test_xp_calculation.py -k "level" -v` — 4 passing tests

### Implementation

- [X] T025 [US2] Add `test_level_zero_xp` to `fastapi_app/tests/test_xp_calculation.py` — `calculate_level(0)` → assert `(1, "Beginner", 0, 100)`
- [X] T026 [US2] Add `test_level_exact_boundary` to `fastapi_app/tests/test_xp_calculation.py` — `calculate_level(100)` → assert `(2, "Learner", 0, 200)`
- [X] T027 [US2] Add `test_level_max` to `fastapi_app/tests/test_xp_calculation.py` — `calculate_level(11000)` → assert `(15, "Transcendent", 0, 0)`; also test `calculate_level(12000)` → assert level=15, xp_to_next=0
- [X] T028 [US2] Add `test_level_mid_level` to `fastapi_app/tests/test_xp_calculation.py` — `calculate_level(500)` → assert `(3, "Explorer", 200, 100)`

**Checkpoint**: `python3 -m pytest fastapi_app/tests/test_xp_calculation.py -v` — all 15 tests pass

---

## Phase 5: Polish & Validation

**Purpose**: End-to-end validation of all success criteria

- [X] T029 Run full suite: `python3 -m pytest fastapi_app/tests/ -v` — verify all 15 tests pass (SC-001)
- [X] T030 Verify execution time under 5 seconds (SC-002) — check pytest output duration
- [X] T031 Verify Redis cleanup: run `redis-cli -p 13000 KEYS "test:*"` after test completion — expect empty array (SC-004)
- [X] T032 Run `python3 -m pytest fastapi_app/tests/ --co` to confirm fixture discovery without import errors (SC-003)

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: No dependencies — can start immediately
- **Foundational (Phase 2)**: Depends on Phase 1 (T001, T002) — BLOCKS all user stories
- **US1 (Phase 3)**: Depends on Phase 2 (conftest.py must exist for imports)
- **US2 (Phase 4)**: Depends on Phase 3 (tests append to same file `test_xp_calculation.py`)
- **Polish (Phase 5)**: Depends on Phase 3 + Phase 4

### User Story Dependencies

- **US3 (Infrastructure)**: Foundational — no dependencies on other stories
- **US1 (XP Tests)**: Depends on US3 infrastructure (conftest.py)
- **US2 (Level Tests)**: Depends on US1 (shared test file created in US1 phase)

### Within Each Phase

- T001 and T002 are parallel (different files)
- T003 must be first in Phase 2 (settings override must precede all other fixtures)
- T004-T012 are sequential within `conftest.py` (single file, fixtures depend on each other)
- T013 must be first in Phase 3 (creates file + imports)
- T014-T024 can logically be written as one batch (all in same file, no interdependencies)
- T025-T028 can logically be written as one batch (all appending to same file)

### Parallel Opportunities

```text
Phase 1:
  T001 ─┬─ (parallel)
  T002 ─┘

Phase 2:
  T003 → T004 → T005 → T006 → T007 → T008 → T009 → T010 → T011 → T012
  (sequential — single file)

Phase 3:
  T013 → T014-T024 (batch — all in one file write)

Phase 4:
  T025-T028 (batch — all appended to existing file)

Phase 5:
  T029 → T030 ─┬─ (after T029)
  T031 ─────────┤  (parallel)
  T032 ─────────┘  (parallel)
```

---

## Implementation Strategy

### MVP First (Phase 1 + 2 + 3)

1. Complete Phase 1: Setup (pyproject.toml + __init__.py)
2. Complete Phase 2: conftest.py with all 9 fixtures
3. Complete Phase 3: 11 XP calculation tests
4. **STOP and VALIDATE**: `pytest -k "not level"` — 11 tests pass
5. This alone provides regression protection for XP calculation

### Full Delivery (Add Phase 4 + 5)

6. Complete Phase 4: 4 level calculation tests
7. Complete Phase 5: Full validation (15 tests, <5s, clean Redis)

### Practical Note

Given the small scope (3 new files + 1 modification), Phases 2-4 are best executed as sequential writes to their respective files. The `conftest.py` fixtures (T003-T012) should be written as a single coherent file. Similarly, all 15 tests (T013-T028) should be written as a single `test_xp_calculation.py` file. The task breakdown exists for traceability, not to suggest 28 separate file operations.

---

## Notes

- All tests are sync `def test_*()` functions (pure functions, no async)
- `conftest.py` fixtures include async fixtures for future phases (2-7)
- Settings override MUST execute before FastAPI app import in conftest.py
- Redis cleanup uses SCAN+DELETE pattern, NEVER FLUSHDB (FR-009)
- Test prefix format: `test:{uuid4().hex[:8]}:` ensures parallel test session isolation
- Code style: tabs, double quotes, 110 char line length (per `pyproject.toml` Ruff config)
