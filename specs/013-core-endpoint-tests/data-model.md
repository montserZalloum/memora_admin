# Data Model: Core Endpoint Tests

**Feature Branch**: `013-core-endpoint-tests`
**Date**: 2026-02-17

## Test Infrastructure Entities

### TestFixtureState

Represents the Redis state that must be pre-seeded for endpoint tests.

| Field | Type | Purpose |
|-------|------|---------|
| `session_key` | `str` | `memora:session:{user_id}` — auth session for `get_current_user` |
| `session_data` | `dict` | `{"fid": family_id}` — session family_id for auth validation |
| `game_session_key` | `str` | `memora:gamesession:{user_id}` — active game session hash |
| `hierarchy_key` | `str` | `memora:hierarchy:{subject_id}` — cached hierarchy JSON |
| `settings_key` | `str` | `memora:settings:gamification` — cached gamification settings |
| `wallet_key` | `str` | `memora:wallet:{player_id}` — wallet hash (xp, streak) |
| `access_key` | `str` | `memora:access:{player_id}` — access grants set |
| `stats_key` | `str` | `memora:stats:{user}:{subject}:v{ver}` — cached stats hash |

### MinimalHierarchy

Minimal hierarchy JSON structure needed for endpoint tests. Full hierarchy has deep nesting; tests need only enough to validate access checks and lesson lookups.

```python
{
    "subject_id": "SUB-TEST-001",
    "version": 1,
    "is_linear": False,
    "bit_range": 10,
    "excluded_bits": [],
    "free_units": [],        # Empty = no free content
    "free_topics": [],       # Empty = no free content
    "tracks": [
        {
            "track_id": "TRK-TEST-001",
            "is_linear": False,
            "units": [
                {
                    "unit_id": "UNIT-TEST-001",
                    "is_linear": False,
                    "is_free": False,
                    "topics": [
                        {
                            "topic_id": "TOPIC-TEST-001",
                            "is_linear": False,
                            "is_free": False,
                            "lessons": [
                                {
                                    "lesson_id": "LESSON-TEST-001",
                                    "bit_index": 0,
                                    "xp": 0,
                                    "max_hearts": 3,
                                    "is_reviewable": True
                                }
                            ]
                        }
                    ]
                }
            ]
        }
    ]
}
```

### GamificationSettings

Cached settings JSON needed for session end tests.

```python
{
    "base_lesson_xp": 10,
    "replay_xp": 3,
    "max_hearts": 3,
    "xp_per_heart": 2,
    "max_streak_multiplier_percent": 50,
    "session_timeout_days": 30,
    "max_devices_per_player": 3
}
```

## Test File → Endpoint Module Mapping

| Test File | Endpoint Module | Routes Tested | Test Count |
|-----------|----------------|---------------|------------|
| `test_health_endpoints.py` | `health.py` | 2 | ~4 |
| `test_auth_endpoints.py` | `auth.py` | 10 | ~25 |
| `test_session_endpoints.py` | `sessions.py` | 3 | ~15 |
| `test_progress_endpoints.py` | `progress.py` | 6 | ~10 |
| `test_wallet_endpoints.py` | `wallet.py` | 2 | ~4 |
| `test_access_endpoints.py` | `access.py` | 3 | ~6 |
| **Total** | **6 modules** | **26 routes** | **~64** |

## Conftest Changes Required

### Session Key Fix (R-001)

**Before** (broken):
```python
session_key = f"{test_prefix}memora:session:{player_id}"
```

**After** (fixed):
```python
session_key = f"memora:session:{player_id}"
# + explicit cleanup in teardown
```

### New Helper Fixtures Needed

| Fixture/Helper | Purpose | Used By |
|----------------|---------|---------|
| `seed_hierarchy(redis, subject_id, **overrides)` | Seed Redis hierarchy cache | session, progress tests |
| `seed_game_session(redis, user_id, lesson_id, subject_id)` | Seed active game session hash | session tests |
| `seed_settings(redis)` | Seed gamification settings cache | session end tests |
| `seed_wallet(redis, player_id, xp=0, streak=0)` | Seed wallet hash | session end, wallet tests |
| `seed_access_grants(redis, player_id, keys)` | Seed access grant set | session, progress tests |
| `cleanup_player_keys(redis, player_id)` | Delete all `memora:*` keys for player | all authed tests |
| `make_hierarchy_json(has_free=False, lesson_count=1)` | Build hierarchy dict | shared helper |
