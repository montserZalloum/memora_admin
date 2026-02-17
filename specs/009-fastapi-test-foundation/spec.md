# Feature Specification: FastAPI Test Foundation + Pure Function Tests

**Feature Branch**: `009-fastapi-test-foundation`
**Created**: 2026-02-17
**Status**: Draft
**Input**: User description: "Phase 1: Foundation + Pure Functions (~15 tests, 3 files) from FASTAPI_TEST_PLAN.md"

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Run XP Calculation Tests (Priority: P1)

A developer working on the gamification system needs confidence that the XP award calculation produces correct results across all input combinations — fresh completions, replays, hearts bonuses, and streak multipliers. They run `pytest` and see 11 passing tests covering every branch of `calculate_xp_award`.

**Why this priority**: XP calculation is the most business-critical pure function. Incorrect XP awards directly impact player experience and progression. This is the highest-value test target with zero external dependencies.

**Independent Test**: Can be fully tested by running `pytest test_xp_calculation.py` — delivers immediate value by protecting against XP regressions.

**Acceptance Scenarios**:

1. **Given** a fresh lesson completion with `lesson_xp=0` and `base_xp=50`, **When** `calculate_xp_award` is called, **Then** the result uses `base_xp` (50) as the base amount.
2. **Given** a fresh completion with `lesson_xp=75` (overrides `base_xp`), **When** `calculate_xp_award` is called, **Then** the result uses `lesson_xp` (75) as the base.
3. **Given** a replay with `replay_xp=10`, **When** `calculate_xp_award` is called with `is_replay=True`, **Then** the result is based on the fixed `replay_xp` amount regardless of `base_xp` or `lesson_xp`.
4. **Given** a replay, **When** `hearts_remaining > 0`, **Then** the hearts bonus is NOT added (replays ignore hearts).
5. **Given** a fresh completion with `hearts_remaining=3` and `xp_per_heart=5`, **When** `calculate_xp_award` is called, **Then** 15 XP hearts bonus is added to the base before streak multiplication.
6. **Given** a streak of 10 days with `max_multiplier_percent=50`, **When** the streak multiplier is applied, **Then** the multiplier is 1.10 (10% bonus).
7. **Given** a streak of 100 days with `max_multiplier_percent=50`, **When** the streak multiplier is applied, **Then** the multiplier is capped at 1.50 (50% max).
8. **Given** a streak of 0, **When** the multiplier is applied, **Then** the multiplier is 1.0 (no bonus).
9. **Given** a calculation that produces a fractional result, **When** the final XP is computed, **Then** the result is floored (truncated via `int()`), not rounded.
10. **Given** `base_xp=0` and `lesson_xp=0` for a fresh completion, **When** calculated, **Then** the result is 0.
11. **Given** a replay with an active streak, **When** calculated, **Then** the streak multiplier is applied to `replay_xp` as well.

---

### User Story 2 - Run Level Calculation Tests (Priority: P1)

A developer adjusting the leveling curve needs to verify that `calculate_level` returns the correct level number, title, in-level XP, and XP-to-next for any total XP input. They run `pytest` and see 4 passing tests covering boundaries and edge cases.

**Why this priority**: Level display is player-facing. Incorrect level titles or progress bars destroy user trust. This is a pure function with zero dependencies — easy to test and high value.

**Independent Test**: Can be fully tested by running `pytest test_xp_calculation.py -k level` — protects the leveling system independently.

**Acceptance Scenarios**:

1. **Given** 0 total XP, **When** `calculate_level` is called, **Then** returns Level 1, title "Beginner", `xp_in_level=0`, `xp_to_next=100`.
2. **Given** 100 total XP (exact Level 2 boundary), **When** `calculate_level` is called, **Then** returns Level 2, title "Learner", `xp_in_level=0`, `xp_to_next=200`.
3. **Given** 11000+ total XP (max level), **When** `calculate_level` is called, **Then** returns Level 15, title "Transcendent", `xp_to_next=0`.
4. **Given** 500 total XP (mid-level), **When** `calculate_level` is called, **Then** returns Level 3, title "Explorer", `xp_in_level=200`, `xp_to_next=100`.

---

### User Story 3 - Test Infrastructure Bootstrap (Priority: P1)

A developer adding new FastAPI tests needs a working test infrastructure: shared fixtures for Redis connections, mock Frappe clients, JWT token factories, and test key isolation. They create a new test file, import the fixtures from `conftest.py`, and write tests without worrying about setup/teardown.

**Why this priority**: Without test infrastructure, no other tests can be written. This is the foundational prerequisite for all subsequent test phases (2-7). A broken foundation blocks ~225 additional tests.

**Independent Test**: Can be verified by running `pytest --co` (collect-only) to confirm fixture discovery, and by running the quickstart test to confirm Redis connectivity and key cleanup.

**Acceptance Scenarios**:

1. **Given** the test directory exists with `__init__.py` and `conftest.py`, **When** `pytest --co` is run, **Then** all fixtures are discovered and no import errors occur.
2. **Given** a test uses the `redis_client` fixture, **When** the test writes keys with the `test_prefix`, **Then** those keys are automatically cleaned up after the test completes.
3. **Given** a test uses the `mock_frappe` fixture, **When** the test calls `mock_frappe.call()`, **Then** it returns `None` by default and can be configured per-test.
4. **Given** `pyproject.toml` has the pytest configuration, **When** `python3 -m pytest fastapi_app/tests/ -v` is run from the project root, **Then** tests are discovered and executed correctly.
5. **Given** tests run against production Redis at `redis://127.0.0.1:13000`, **When** tests complete, **Then** only test-prefixed keys are touched — no production keys are affected.

---

### Edge Cases

- What happens when `calculate_xp_award` receives negative values for `base_xp`, `hearts_remaining`, or `streak`? (Current behavior: function does not validate inputs — negative values produce unexpected results. Tests document current behavior.)
- What happens when `calculate_level` receives negative XP? (Current behavior: falls through to fallback return of Level 1.)
- What happens when Redis is unreachable during test setup? (Tests should fail fast with clear connection error, not hang.)
- What happens when two test sessions run concurrently? (UUID-based key prefixes prevent interference between parallel test runs.)

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: Test suite MUST include a `conftest.py` with shared fixtures for Redis connection, mock Frappe client, JWT token factories, FastAPI test client, and automatic test key cleanup
- **FR-002**: Test suite MUST use prefix-based key isolation (`test:{uuid}:`) to avoid touching production Redis data
- **FR-003**: Test suite MUST automatically clean up all test-prefixed Redis keys after each test via SCAN+DEL
- **FR-004**: Test suite MUST override `get_settings()` to provide hardcoded test configuration, avoiding dependency on `.env` file
- **FR-005**: `test_xp_calculation.py` MUST contain 11 tests for `calculate_xp_award` covering: fresh base XP, lesson XP override, replay fixed amount, replay ignoring hearts, hearts bonus, streak multiplier (linear, capped, zero), result flooring, zero inputs, and replay with streak
- **FR-006**: `test_xp_calculation.py` MUST contain 4 tests for `calculate_level` covering: zero XP, exact boundary, max level, and mid-level progression
- **FR-007**: `pyproject.toml` MUST include `[tool.pytest.ini_options]` section with `testpaths`, `asyncio_mode = "auto"`, and marker definitions
- **FR-008**: All 15 tests MUST pass when run via `python3 -m pytest fastapi_app/tests/ -v`
- **FR-009**: Test suite MUST NOT use `FLUSHDB` or any command that could affect non-test Redis data
- **FR-010**: Mock Frappe fixture MUST provide a configurable `AsyncMock` with pre-configured `call`, `get_grant_keys`, `create_subscription`, and `close` methods

### Key Entities

- **Test Fixture (conftest.py)**: Reusable test setup components — Redis client, mock Frappe, JWT factories, FastAPI client, key cleanup. Shared across all future test phases.
- **XP Award Calculation**: Pure function mapping input parameters (base_xp, lesson_xp, streak, hearts, replay) to an integer XP result. No side effects.
- **Level Calculation**: Pure function mapping total XP to (level, title, xp_in_level, xp_to_next) tuple. No side effects.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: All 15 tests pass on first run with zero failures or errors
- **SC-002**: Test execution completes in under 5 seconds (pure functions + Redis fixture setup)
- **SC-003**: Running `python3 -m pytest fastapi_app/tests/ -v` from the project root discovers and runs all tests without manual configuration
- **SC-004**: After test completion, no test-prefixed keys remain in Redis (verified by SCAN)
- **SC-005**: Tests can run concurrently with the live FastAPI server without interference
- **SC-006**: Every branch of `calculate_xp_award` (fresh/replay, with/without hearts, streak cases) has at least one covering test

## Assumptions

- Redis is running and accessible at `redis://127.0.0.1:13000` (same instance used by Frappe)
- `pytest` and `pytest-asyncio` are installed (or will be installed as part of this phase)
- The `calculate_xp_award` and `calculate_level` function signatures and behavior are stable and will not change during this phase
- The `conftest.py` fixtures designed here will be consumed by all subsequent test phases (2-7) without modification to the core fixtures
