# Research: FastAPI Test Foundation + Pure Function Tests

**Date**: 2026-02-17
**Status**: Complete — no unknowns remaining

## Research Summary

All technical decisions for Phase 1 were resolved through codebase analysis. No external research or third-party documentation lookup was required — the existing `FASTAPI_TEST_PLAN.md` and source code provided complete answers.

---

## R1: Test Dependencies Availability

**Decision**: All required dependencies are pre-installed — no `pip install` needed.

**Evidence**:
- `pytest` 8.4.2 ✅
- `pytest-asyncio` 0.26.0 ✅
- `httpx` 0.28.1 ✅
- `redis.asyncio` (bundled with `redis` package) ✅

**Alternatives considered**: None — dependencies are already available.

---

## R2: Settings Override Pattern

**Decision**: Clear `lru_cache` and replace `get_settings` at module level in `conftest.py`, before any app import.

**Rationale**: `get_settings()` in `fastapi_app/core/config.py:53-56` uses `@lru_cache`. Once called, it returns the cached `Settings()` instance which reads from `.env`. Tests must intercept this before any module triggers it.

**Implementation**:
```python
from fastapi_app.core.config import Settings, get_settings
import fastapi_app.core.config as config_module

_test_settings = Settings(
    redis_url="redis://127.0.0.1:13000",
    jwt_secret="test-secret-key-for-unit-tests",
    jwt_algorithm="HS256",
    bitmap_json_path="/tmp/test-bitmaps",
    frappe_url="http://localhost:8000",
    frappe_site="test.local",
    frappe_api_key="test-key",
    frappe_api_secret="test-secret",
    voucher_hmac_secret="test-hmac-secret",
)

get_settings.cache_clear()
config_module.get_settings = lambda: _test_settings
```

**Alternatives considered**:
- Environment variable override (`monkeypatch.setenv`) — rejected because `Settings` reads `.env` file first, and `@lru_cache` caches across tests. Module-level patch is more reliable.
- FastAPI dependency override (`app.dependency_overrides[get_settings]`) — only works for endpoint tests via `app_client`, not for modules that import `get_settings` directly (like `security.py`).

---

## R3: `calculate_xp_award` Function Signature

**Decision**: Confirmed stable at `fastapi_app/services/wallet.py:35-67`.

**Signature**:
```python
def calculate_xp_award(
    base_xp: int,
    lesson_xp: int,
    current_streak: int,
    max_multiplier_percent: int,
    is_replay: bool,
    replay_xp: int,
    hearts_remaining: int = 0,
    xp_per_heart: int = 0,
) -> int
```

**Logic summary**:
1. If replay → `base = replay_xp` (no hearts bonus)
2. If fresh → `base = lesson_xp if lesson_xp > 0 else base_xp`, then `base += hearts_remaining * xp_per_heart`
3. Streak multiplier: `1.0 + min(current_streak, max_multiplier_percent) * 0.01`
4. Return `int(base * multiplier)` (floor)

---

## R4: `calculate_level` Function Signature

**Decision**: Confirmed stable at `fastapi_app/core/constants.py:61-81`.

**Signature**:
```python
def calculate_level(total_xp: int) -> tuple[int, str, int, int]
# Returns: (level, title, xp_in_level, xp_to_next_level)
```

**Key data**:
- 15 levels, thresholds: [0, 100, 300, 600, 1000, 1500, 2100, 2800, 3600, 4500, 5500, 6700, 8000, 9500, 11000]
- Titles: ["Beginner", "Learner", "Explorer", "Scholar", "Achiever", "Expert", "Master", "Champion", "Legend", "Grandmaster", "Sage", "Titan", "Mythic", "Immortal", "Transcendent"]
- Iterates from highest threshold downward; first match wins
- Fallback: Level 1 "Beginner" (unreachable since threshold[0]=0)

---

## R5: pytest-asyncio Configuration

**Decision**: Use `asyncio_mode = "auto"` in `pyproject.toml`.

**Rationale**: With pytest-asyncio 0.26.0 and `asyncio_mode = "auto"`, all `async def test_*` functions are automatically treated as async tests without requiring `@pytest.mark.asyncio`. Phase 1 pure function tests are sync (`def test_*`) so this config has no effect, but it's required for Phase 2+ async tests.

**Alternatives considered**: `asyncio_mode = "strict"` — rejected because it requires explicit `@pytest.mark.asyncio` on every async test, adding unnecessary boilerplate.

---

## R6: Redis Key Cleanup Pattern

**Decision**: Use `SCAN + DELETE` with test prefix pattern after each test.

**Implementation** (from FASTAPI_TEST_PLAN.md, verified against redis.asyncio API):
```python
@pytest.fixture(autouse=True)
async def cleanup_keys(redis_client, test_prefix):
    yield
    cursor = 0
    while True:
        cursor, keys = await redis_client.scan(cursor, match=f"{test_prefix}*", count=1000)
        if keys:
            await redis_client.delete(*keys)
        if cursor == 0:
            break
```

**Alternatives considered**: `FLUSHDB` — explicitly forbidden by FR-009 (shared Redis with production Frappe).

---

## R7: Code Style for Test Files

**Decision**: Follow existing Ruff configuration — tabs, double quotes, 110 char line length.

**Source**: `pyproject.toml` `[tool.ruff]` and `[tool.ruff.format]` sections.
