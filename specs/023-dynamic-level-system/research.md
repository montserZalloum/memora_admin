# Research: Dynamic Level System

**Feature**: 023-dynamic-level-system
**Date**: 2026-02-22

## R1: Redis Sync Pattern for Config Data

**Decision**: Use the same two-pronged pattern as `catalog_sync.py` — direct `r.set()` on save + pubsub publish to notify FastAPI.

**Rationale**: This is the established pattern across 7+ sync files in the codebase (`catalog_sync.py`, `profile_sync.py`, `plan_change_sync.py`, `build_trigger.py`, `access_sync.py`). Using the same pattern ensures consistency and leverages the existing pubsub listener infrastructure in `fastapi_app/core/pubsub.py`.

**Alternatives considered**:
- Frappe API hydration on cache miss (like `SettingsService`): Would work but requires FrappeClient availability in FastAPI. For a global singleton config that rarely changes, direct push from Frappe on save is simpler and faster. Use hardcoded defaults as fallback when cache is empty (no FrappeClient dependency for level calculation).
- No-TTL event-driven only (like catalog): Acceptable since config is pushed on save and fallback defaults exist. However, adding a 1h TTL as a safety net is fine — if it expires before the next save, defaults are identical for fresh installs.

## R2: FastAPI Module Placement

**Decision**: Create `fastapi_app/core/level_config.py` as a standalone module with pure functions + frozen dataclass. No service class needed.

**Rationale**: Level config is a read-only, stateless lookup. It doesn't need the service class pattern (`__init__` with redis/frappe deps, `ensure_hydrated`, etc.) because:
1. It's a global singleton config, not per-player data
2. Fallback defaults make hydration unnecessary
3. A simple `get_level_config(redis)` async function + pure `calculate_level(xp, config)` function is sufficient
4. Callers (like `ProfilePageService.get_hero()`) already have a Redis client

**Alternatives considered**:
- Full `LevelService` class with `ensure_hydrated()`: Over-engineering for a singleton config. The `SettingsService` pattern uses a class, but that service has multiple methods. Level config is just two functions.
- In-memory cache in FastAPI (via pubsub invalidation): Would add complexity. Redis GET is sub-millisecond; caching in Python memory saves negligible time.

## R3: Frappe Single DocType with Child Table

**Decision**: Create `Memora Level Settings` (Single DocType) + `Memora Level Title` (child table). Follow exact structure of `Memora Settings` + `Memora Grant Component`.

**Rationale**: Existing patterns in the codebase:
- `Memora Settings` is a Single DocType with standard fields (CDN config, XP values, etc.)
- `Memora Grant Component` is a child table (`istable: 1`, no permissions, minimal `.py`)
- `Memora Academic Plan` → `Memora Plan Subject` demonstrates parent-child table relationship

**Key implementation details discovered**:
- Single DocType: `"issingle": 1` in JSON, permissions for System Manager
- Child table: `"istable": 1` in JSON, no permissions (inherited)
- Parent references child via `"fieldtype": "Table", "options": "Memora Level Title"`
- Hook registration in `hooks.py` `doc_events` dictionary
- Redis connection via `get_fastapi_redis()` from `access_sync.py`

## R4: Pubsub Listener Extension

**Decision**: Add a new `level_config` message type to the existing `_handle_invalidation()` function in `pubsub.py`. On receipt, delete the local Redis key (redundant but consistent) and log.

**Rationale**: The pubsub listener already handles 6 message types (hierarchy, plan, profile, catalog, plan_subjects, subscription_changed). Adding `level_config` follows the exact same pattern. Since level config is read directly from Redis (not cached in a service object's memory), the pubsub handler only needs to log the event — the direct `r.set()` from the Frappe hook already updates Redis.

**Alternatives considered**:
- Skip pubsub entirely (rely on direct write only): Would work, but breaks pattern consistency. Other developers expect all cache updates to fire pubsub.
- Create a dedicated `LevelConfigService` on `app.state` for in-memory caching: Over-engineering. Redis GET is fast enough.

## R5: Backward Compatibility of Levels 12-15

**Decision**: Accept the slight threshold shift for levels 12-15. Formula values replace hardcoded values.

**Rationale**:
- Current hardcoded: L12=6700, L13=8000, L14=9500, L15=11000
- Formula (a=50, b=50): L12=6600, L13=7800, L14=9100, L15=10500
- Formula thresholds are LOWER, so no player drops a level
- `xp_in_level` and `xp_to_next` will change slightly for L12-15 players
- The PRD explicitly accepted this tradeoff
- Levels 1-11 are identical (the vast majority of active players)

## R6: Cache Miss Behavior

**Decision**: Return hardcoded fallback defaults on cache miss (no MariaDB hydration).

**Rationale**:
1. Level config is a global singleton, not per-player — no `ensure_hydrated()` needed
2. Hardcoded defaults match the current behavior exactly (for levels 1-11)
3. No dependency on `FrappeClient` for level calculation — simpler, faster, more resilient
4. The Frappe `on_update` hook pushes to Redis on every save, so custom config is always fresh after an admin change
5. If the cache key expires (1h TTL), defaults kick in until the next admin save or until a periodic re-push is added (future improvement)
6. This matches the `SettingsService` pattern: `return GamificationSettings()` (defaults) when Frappe is unavailable

## R7: Profile Page Callers

**Decision**: Only `fastapi_app/services/profile_page.py` needs updating. It's the sole consumer of `calculate_level` and `LEVEL_THRESHOLDS` in the FastAPI codebase.

**Rationale**: Grep confirms only 3 files reference level constants/function:
1. `fastapi_app/core/constants.py` — source (being replaced)
2. `fastapi_app/services/profile_page.py` — consumer (needs update)
3. `fastapi_app/tests/test_xp_calculation.py` — tests (needs migration)

The `get_hero()` method uses both `calculate_level()` and `LEVEL_THRESHOLDS[level]` for `xp_level_start`/`xp_level_end`. Both will be replaced with formula-based computation from the new module.
