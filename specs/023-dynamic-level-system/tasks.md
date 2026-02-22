# Tasks: Dynamic Level System

**Input**: Design documents from `/specs/023-dynamic-level-system/`
**Prerequisites**: plan.md, spec.md, research.md, data-model.md, contracts/

**Organization**: Tasks grouped by user story. US1 (Admin Edits Titles) and US2 (Admin Adjusts Curve) share 100% of implementation (same sync pipeline) and are combined into Phase 4.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependencies)
- **[Story]**: Which user story this task belongs to (US1, US2, US3, US4)
- Includes exact file paths in descriptions

---

## Phase 1: Setup (DocType File Structure)

**Purpose**: Create the two Frappe DocType directories with schema files and package markers

- [x] T001 [P] Create Memora Level Title child table DocType (`istable: 1`) with fields `level_number` (Int, reqd), `title_en` (Data, reqd, max 140), `title_ar` (Data, optional, max 140), `icon` (Attach Image, optional) — create `memora_level_title.json`, `memora_level_title.py` (pass class), and `__init__.py` in `memora_admin/memora_admin/doctype/memora_level_title/`
- [x] T002 [P] Create Memora Level Settings Single DocType (`issingle: 1`) JSON schema with fields `quadratic_coefficient` (Int, default 50), `linear_coefficient` (Int, default 50), `max_level` (Int, default 15), and `level_titles` (Table, options: Memora Level Title); System Manager permissions (create, read, write, delete) in `memora_admin/memora_admin/doctype/memora_level_settings/memora_level_settings.json`
- [x] T003 [P] Create Memora Level Settings supporting files: `__init__.py` (empty) and minimal `memora_level_settings.js` form handler (setup with refresh_fields on save) in `memora_admin/memora_admin/doctype/memora_level_settings/`

---

## Phase 2: Foundational (Core Level Config Module)

**Purpose**: FastAPI pure-function level config module with Redis read and hardcoded fallback defaults. Provides the cache-miss resilience required by US3.

**CRITICAL**: All user story phases depend on this module

- [x] T004 Create `LevelConfig` frozen dataclass (fields: `a: int = 50`, `b: int = 50`, `max_level: int = 15`, `titles: dict[int, str]` with 15 default titles "Beginner" through "Transcendent"), `DEFAULT_LEVEL_CONFIG` module constant, `get_threshold(level: int, a: int, b: int) -> int` pure function using `round(a * (level-1)**2 + b * (level-1))`, and `calculate_level(total_xp: int, config: LevelConfig) -> tuple[int, str, int, int]` using O(1) inverse quadratic formula `min(floor((-b + sqrt(b*b + 4*a*xp)) / (2*a)) + 1, max_level)` with negative XP clamped to 0 and title fallback `f"Level {level}"` in `fastapi_app/core/level_config.py`
- [x] T005 Add `async def get_level_config(redis_client) -> LevelConfig` function that reads `memora:config:levels` key, parses JSON string to `LevelConfig` (mapping `"a"`, `"b"`, `"max_level"`, `"titles"` with int keys), and returns `DEFAULT_LEVEL_CONFIG` on cache miss or any parse error in `fastapi_app/core/level_config.py`

**Checkpoint**: Core level calculation module ready. `calculate_level()` is O(1). Fallback defaults handle cache loss (US3).

---

## Phase 3: User Story 4 — Default Config Matches Current Behavior (Priority: P1) MVP

**Goal**: Verify the new formula-based calculation produces identical results to the old hardcoded function for levels 1-11 with default config (a=50, b=50)

**Independent Test**: Run `pytest fastapi_app/tests/test_xp_calculation.py -v` — all `TestLevelCalculation` tests pass with migrated assertions

- [x] T006 [US4] Migrate `TestLevelCalculation` class: change import from `fastapi_app.core.constants import calculate_level` to `fastapi_app.core.level_config import calculate_level, DEFAULT_LEVEL_CONFIG`, update all calls to `calculate_level(xp, DEFAULT_LEVEL_CONFIG)`, and adjust `test_level_max` expected `xp_in_level` from 0 to 500 (formula threshold for L15 is 10500 vs old hardcoded 11000, per research.md R5) in `fastapi_app/tests/test_xp_calculation.py`
- [x] T007 [P] [US4] Add `test_default_thresholds_match_legacy` test verifying `get_threshold(level, 50, 50)` produces `[0, 100, 300, 600, 1000, 1500, 2100, 2800, 3600, 4500, 5500]` for levels 1-11 exactly in `fastapi_app/tests/test_xp_calculation.py`

**Checkpoint**: Default config backward compatibility verified for levels 1-11. Threshold shift for levels 12-15 accepted per R5.

---

## Phase 4: User Story 1 — Admin Edits Level Titles (P1) + User Story 2 — Admin Adjusts XP Curve (P1)

**Goal**: Admin saves Level Settings in Frappe → config pushed to Redis via sync hook → profile API reflects changes within 5 seconds

**Why combined**: US1 (title changes) and US2 (curve changes) use the exact same sync pipeline. The `on_level_settings_updated` hook pushes ALL config (titles + curve params + max_level) on every save. No separate code paths.

**Independent Test**: Change a title or curve coefficient in Frappe admin → save → call profile API → verify new values appear

- [x] T008 [US1] Implement `validate()` method on DocType class: assert `quadratic_coefficient >= 1`, `linear_coefficient >= 0`, `max_level >= 1`, reject duplicate `level_number` values in `level_titles` child table, reject empty `title_en` for any row, reject `level_number < 1` in `memora_admin/memora_admin/doctype/memora_level_settings/memora_level_settings.py`
- [x] T009 [P] [US1] Create `on_level_settings_updated(doc, method)` sync hook: build JSON payload `{"a": doc.quadratic_coefficient, "b": doc.linear_coefficient, "max_level": doc.max_level, "titles": {row.level_number: row.title_en for row in doc.level_titles}}`, call `r.set("memora:config:levels", payload, ex=3600)` then `r.publish("memora:cache:invalidate", json.dumps({"type": "level_config", "timestamp": now_str}))` using `get_fastapi_redis()` from `memora_admin.events.access_sync` in `memora_admin/events/level_sync.py`
- [x] T010 [US1] Register `Memora Level Settings` doc event: add `"Memora Level Settings": {"on_update": "memora_admin.events.level_sync.on_level_settings_updated"}` to `doc_events` dict in `memora_admin/hooks.py`
- [x] T011 [P] [US2] Add `level_config` message type handler to `_handle_invalidation()`: when `msg_type == "level_config"`, log `"level_config_updated"` with timestamp (no in-memory cache to invalidate since config is read from Redis on each request) in `fastapi_app/core/pubsub.py`
- [x] T012 [US1] Update `ProfilePageService.get_hero()`: replace `from fastapi_app.core.constants import LEVEL_THRESHOLDS, calculate_level` with `from fastapi_app.core.level_config import get_level_config, calculate_level, get_threshold`; add `config = await get_level_config(self.redis)` call; change `calculate_level(total_xp)` to `calculate_level(total_xp, config)`; replace `LEVEL_THRESHOLDS[level-1]` with `get_threshold(level, config.a, config.b)` and `LEVEL_THRESHOLDS[level]` with `get_threshold(level+1, config.a, config.b) if level < config.max_level else 0` for `xp_level_start`/`xp_level_end`; remove unused `MASTERY_CACHE_TTL` import only if no longer used in `fastapi_app/services/profile_page.py`

**Checkpoint**: Full admin-to-API pipeline working. Both title changes (US1) and curve changes (US2) propagate within 5 seconds.

---

## Phase 5: User Story 3 — Resilience on Cache Loss (Priority: P2)

**Goal**: Profile API returns correct level data using fallback defaults when Redis config key is missing, expired, or Redis is flushed

**Independent Test**: Delete `memora:config:levels` from Redis → call profile API → verify valid level data matching default config

**Note**: Resilience is already implemented in T005 (`get_level_config()` returns `DEFAULT_LEVEL_CONFIG` on cache miss). This phase adds explicit verification.

- [x] T013 [US3] Add `test_cache_miss_returns_defaults` async test: create `AsyncMock` redis client with `get()` returning `None`, call `get_level_config(mock_redis)`, assert result equals `DEFAULT_LEVEL_CONFIG` (a=50, b=50, max_level=15, 15 titles); also add `test_cache_corrupt_returns_defaults` where `get()` returns invalid JSON string in `fastapi_app/tests/test_xp_calculation.py`

**Checkpoint**: Cache loss resilience explicitly verified. System gracefully falls back to defaults.

---

## Phase 6: Polish & Cross-Cutting Concerns

**Purpose**: Remove old hardcoded level code (SC-007) and final verification

- [x] T014 Remove `LEVEL_THRESHOLDS` list, `LEVEL_TITLES` list, and `calculate_level()` function from `fastapi_app/core/constants.py` (keep all other constants: `DIRTY_PROGRESS_KEY`, `DIRTY_WALLETS_KEY`, `INTERACTION_BUFFER_KEY`, `GAME_SESSION_TTL`, `MASTERY_MATURE_THRESHOLD`, `MASTERY_CACHE_TTL`)
- [x] T015 Run quickstart.md verification checklist: grep for `LEVEL_THRESHOLDS` and `LEVEL_TITLES` in `fastapi_app/` (expect 0 matches), run `pytest fastapi_app/tests/test_xp_calculation.py -v` (all pass), verify `curl http://127.0.0.1:8002/api/v1/health/live` returns alive

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: No dependencies — start immediately, all tasks parallel
- **Foundational (Phase 2)**: No dependencies on Phase 1 (different codebase area — can run in parallel with Phase 1)
- **US4 (Phase 3)**: Depends on Phase 2 completion (level_config module must exist for test migration)
- **US1+US2 (Phase 4)**: Depends on Phase 1 AND Phase 2 (DocType schemas + level_config module)
- **US3 (Phase 5)**: Depends on Phase 2 (get_level_config function)
- **Polish (Phase 6)**: Depends on Phase 3 + Phase 4 (all callers must be updated before removing old code)

### User Story Dependencies

- **US4 (P1)**: After Phase 2 — validates core module, no dependency on other stories
- **US1+US2 (P1)**: After Phase 1 + 2 — independent of US4
- **US3 (P2)**: After Phase 2 — independent of US1/US2/US4

### Parallel Opportunities

```
Parallel Wave 1 (no dependencies):
  Phase 1: T001 | T002 | T003
  Phase 2: T004 → T005

Parallel Wave 2 (after Phase 2):
  Phase 3: T006 | T007
  Phase 5: T013

Parallel Wave 3 (after Phase 1 + 2):
  Phase 4: T008 | T009 → T010
            T011 (parallel with T008/T009)
            T012 (after T005)

Sequential Final (after Phase 3 + 4):
  Phase 6: T014 → T015
```

---

## Implementation Strategy

### MVP First (Phase 1 + 2 + 3)

1. Complete Phase 1: DocType file structure (parallel)
2. Complete Phase 2: Core level_config module (sequential)
3. Complete Phase 3: US4 backward compatibility verification
4. **STOP and VALIDATE**: `pytest fastapi_app/tests/test_xp_calculation.py -v` — all tests pass

### Incremental Delivery

1. Phase 1 + 2 → Foundation ready (DocTypes + level_config module)
2. Phase 3 (US4) → Backward compat verified → Safe to proceed
3. Phase 4 (US1+US2) → Admin can configure levels dynamically → Core feature complete
4. Phase 5 (US3) → Cache resilience explicitly verified
5. Phase 6 → Old code removed → Clean codebase (SC-007 satisfied)

### Post-Implementation Steps

- Run `bench --site x.conanacademy.com migrate` after DocType creation (Phase 1)
- Run `bench restart` after hooks.py changes (T010)
- Restart FastAPI (`pkill -f "uvicorn fastapi_app.main:app"`) after level_config/profile_page changes

---

## Notes

- [P] tasks = different files, no dependencies between them
- [Story] label maps task to specific user story for traceability
- US1 and US2 combined because they share 100% of implementation
- US3 resilience is inherent in the foundational module's `get_level_config()` fallback
- `decode_responses=True` on Redis pool — JSON payloads are strings, no `.decode()` needed
- `get_fastapi_redis()` from `access_sync.py` provides synchronous Redis client for Frappe hooks
- Formula threshold shift for levels 12-15 is accepted (R5) — formula values are LOWER so no player drops a level
