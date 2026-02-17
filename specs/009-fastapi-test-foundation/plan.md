# Implementation Plan: FastAPI Test Foundation + Pure Function Tests

**Branch**: `009-fastapi-test-foundation` | **Date**: 2026-02-17 | **Spec**: [spec.md](spec.md)
**Input**: Feature specification from `/specs/009-fastapi-test-foundation/spec.md`

## Summary

Establish the FastAPI test infrastructure (`conftest.py`, pytest config) and write 15 pure function tests for `calculate_xp_award` and `calculate_level`. This is Phase 1 of the FASTAPI_TEST_PLAN — it creates the foundation that all subsequent test phases (2-7, ~225 tests) will build on. No external dependencies are modified; tests use real Redis with prefix isolation and mock FrappeClient.

## Technical Context

**Language/Version**: Python 3.11+ (Frappe v15 bench environment)
**Primary Dependencies**: pytest 8.4.2, pytest-asyncio 0.26.0, httpx 0.28.1, redis.asyncio (all pre-installed)
**Storage**: Redis at `redis://127.0.0.1:13000` (shared with Frappe — prefix isolation required)
**Testing**: pytest + pytest-asyncio (asyncio_mode="auto")
**Target Platform**: Linux server (Ubuntu 20.04+, bench environment)
**Project Type**: Existing FastAPI sidecar within Frappe bench app
**Performance Goals**: All 15 tests complete in <5 seconds (SC-002)
**Constraints**: Must not touch production Redis keys (FR-009); must coexist with running FastAPI server (SC-005)
**Scale/Scope**: 3 new files + 1 modified file; 15 tests total

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

| Principle | Applies to Phase 1? | Status | Notes |
|-----------|---------------------|--------|-------|
| **I. Source-of-Truth Awareness** (NON-NEG) | No | PASS | Phase 1 tests pure functions only — no Redis read/write paths or hydration. Infrastructure prepares for future phases. |
| **II. Atomic Operation Integrity** (NON-NEG) | No | PASS | No Lua scripts or Redis pipelines in Phase 1 scope. |
| **III. Edge-Case-First Design** | Yes | PASS | 15 tests include edge cases: zero streak, max cap, zero inputs, replay variants, result flooring. Ratio ≥1:2. |
| **IV. Test Isolation** (NON-NEG) | Yes | PASS | FR-002 (UUID prefix `test:{uuid}:`), FR-003 (SCAN+DEL cleanup), FR-009 (no FLUSHDB). |
| **V. Business Flow Completeness** | No | PASS | Pure functions have no business flows. Integration coverage starts in Phase 2+. |
| **Excluded Scope** | N/A | PASS | No voucher or library imports in any test file. |

**All gates PASS. No violations to justify.**

## Project Structure

### Documentation (this feature)

```text
specs/009-fastapi-test-foundation/
├── plan.md              # This file
├── research.md          # Phase 0 output — no unknowns (all resolved)
├── data-model.md        # Phase 1 output — fixture & function signatures
├── quickstart.md        # Phase 1 output — how to run tests
├── contracts/
│   └── fixtures.md      # Phase 1 output — conftest.py fixture contracts
└── tasks.md             # Phase 2 output (/speckit.tasks command)
```

### Source Code (repository root)

```text
fastapi_app/
├── tests/                           # NEW — test directory (Phase 1)
│   ├── __init__.py                  # NEW — package marker
│   ├── conftest.py                  # NEW — shared fixtures (9 fixtures)
│   └── test_xp_calculation.py       # NEW — 15 pure function tests
├── services/
│   └── wallet.py                    # EXISTING — contains calculate_xp_award (line 35)
├── core/
│   ├── constants.py                 # EXISTING — contains calculate_level (line 61)
│   └── config.py                    # EXISTING — Settings class + get_settings()
└── ...

pyproject.toml                       # MODIFIED — add [tool.pytest.ini_options]
```

**Structure Decision**: Tests go in `fastapi_app/tests/` per FASTAPI_TEST_PLAN.md convention. This keeps FastAPI tests separate from Frappe tests in `memora_admin/memora_admin/tests/`. The two suites share NO runtime infrastructure.

## Design Decisions

### D1: Settings Override Strategy

`get_settings()` uses `@lru_cache` — it returns the same instance after first call. Tests must:
1. Create a `_test_settings` instance with hardcoded values at module level in `conftest.py`
2. Clear the lru_cache: `get_settings.cache_clear()`
3. Monkey-patch the module: `fastapi_app.core.config.get_settings = lambda: _test_settings`
4. This must happen BEFORE any FastAPI app import triggers `get_settings()`

**Rationale**: Avoids `.env` file dependency in test environment. Hardcoded values are deterministic and CI-friendly.

### D2: Redis Client Fixture Scope

- `redis_client` fixture: **function scope** (default) — creates a new client per test
- `cleanup_keys` fixture: **function scope, autouse** — runs after every test
- `test_prefix` fixture: **function scope** — unique UUID per test

**Rationale**: Function scope ensures zero state leakage between tests. Redis connection overhead is negligible (<1ms).

### D3: FastAPI App Client (Future-Proofing)

The `app_client` fixture uses `httpx.AsyncClient` with `ASGITransport(app=app)` — no real HTTP needed. Dependency overrides wire in test Redis and mock Frappe. This fixture is defined in Phase 1 but only used starting Phase 2.

**Rationale**: Defining all fixtures in Phase 1 means subsequent phases only add test files, never modify `conftest.py` core.

### D4: Pure Function Test Strategy

`calculate_xp_award` and `calculate_level` are synchronous pure functions — no async, no Redis, no mocks needed. Tests are plain `def test_*()` functions (not `async def`) that directly call the function and assert the return value.

**Rationale**: Simplest possible tests. No fixtures required. Fast execution.

## Complexity Tracking

> No violations — table not applicable.
