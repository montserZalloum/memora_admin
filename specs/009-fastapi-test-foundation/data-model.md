# Data Model: FastAPI Test Foundation + Pure Function Tests

**Date**: 2026-02-17

## Overview

Phase 1 has no new database entities or API models. The "data model" for this feature is the set of function signatures under test and the fixture contracts that future test phases depend on.

---

## Functions Under Test

### Entity: `calculate_xp_award`

**Location**: `fastapi_app/services/wallet.py:35-67`
**Type**: Pure synchronous function (no side effects)

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `base_xp` | `int` | required | Default XP for lesson completion |
| `lesson_xp` | `int` | required | Per-lesson XP override (0 = use base_xp) |
| `current_streak` | `int` | required | Player's current streak in days |
| `max_multiplier_percent` | `int` | required | Streak cap as percentage (e.g., 50 = 1.50x max) |
| `is_replay` | `bool` | required | Whether this is a replay of a completed lesson |
| `replay_xp` | `int` | required | Fixed XP amount for replays |
| `hearts_remaining` | `int` | `0` | Hearts left at end of lesson |
| `xp_per_heart` | `int` | `0` | XP bonus per remaining heart |

**Returns**: `int` — floored XP amount

**Business rules**:
1. Replay → base = `replay_xp` (hearts ignored)
2. Fresh → base = `lesson_xp` if > 0, else `base_xp`; then add `hearts_remaining * xp_per_heart`
3. Streak multiplier = `1.0 + min(current_streak, max_multiplier_percent) * 0.01`
4. Result = `int(base * multiplier)` (truncation, not rounding)

### Entity: `calculate_level`

**Location**: `fastapi_app/core/constants.py:61-81`
**Type**: Pure synchronous function (no side effects)

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `total_xp` | `int` | required | Player's total accumulated XP |

**Returns**: `tuple[int, str, int, int]` — `(level, title, xp_in_level, xp_to_next_level)`

**Data dependencies**:
- `LEVEL_THRESHOLDS`: 15-element list `[0, 100, 300, 600, 1000, 1500, 2100, 2800, 3600, 4500, 5500, 6700, 8000, 9500, 11000]`
- `LEVEL_TITLES`: 15-element list `["Beginner", ..., "Transcendent"]`

**Business rules**:
1. Iterate thresholds from highest to lowest
2. First threshold ≤ `total_xp` determines level
3. `xp_in_level` = `total_xp - threshold[level-1]`
4. `xp_to_next` = `threshold[level] - total_xp` (0 at max level)
5. Fallback: Level 1 "Beginner" (unreachable for non-negative input)

---

## Test Settings Entity

**Location**: `conftest.py` (to be created)
**Type**: `fastapi_app.core.config.Settings` instance with hardcoded values

| Field | Test Value | Production Source |
|-------|-----------|-------------------|
| `redis_url` | `redis://127.0.0.1:13000` | `.env` |
| `jwt_secret` | `test-secret-key-for-unit-tests` | `.env` |
| `jwt_algorithm` | `HS256` | default |
| `bitmap_json_path` | `/tmp/test-bitmaps` | `.env` |
| `frappe_url` | `http://localhost:8000` | `.env` |
| `frappe_site` | `test.local` | `.env` |
| `frappe_api_key` | `test-key` | `.env` |
| `frappe_api_secret` | `test-secret` | `.env` |
| `voucher_hmac_secret` | `test-hmac-secret` | `.env` |

---

## Relationships

```text
conftest.py fixtures
├── _test_settings ──────── overrides ──→ get_settings() (lru_cache)
├── redis_client ────────── connects to ─→ Redis 127.0.0.1:13000
├── test_prefix ─────────── generates ──→ "test:{uuid8}:" (per-test unique)
├── cleanup_keys ────────── uses ───────→ redis_client + test_prefix (autouse)
├── mock_frappe ─────────── mocks ──────→ FrappeClient (AsyncMock)
├── make_player_token ───── calls ──────→ create_access_token (security.py)
├── make_admin_token ────── calls ──────→ create_access_token (security.py)
├── app_client ──────────── creates ────→ httpx.AsyncClient(ASGITransport(app))
├── authed_client ───────── extends ────→ app_client + Redis session seeding
└── admin_client ────────── extends ────→ app_client + Redis session seeding

test_xp_calculation.py
├── test_fresh_base_xp ────── calls ──→ calculate_xp_award
├── ... (11 total)
├── test_level_zero_xp ────── calls ──→ calculate_level
└── ... (4 total)
```
