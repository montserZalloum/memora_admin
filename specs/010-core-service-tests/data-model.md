# Data Model: Core Service Tests (Phase 2)

**Feature**: 010-core-service-tests | **Date**: 2026-02-17

## Test Data Entities

This feature produces tests, not production code. The "data model" describes the test data structures: service instances, Redis state, and mock return values.

### Entity 1: AccessService Test State

| Field | Type | Source | Purpose |
|-------|------|--------|---------|
| `player_id` | `str` | Test constant (e.g., `"PLAYER-TEST-001"`) | Redis key construction |
| `content_keys` | `list[str]` | Test constants (e.g., `["SUB-MATH-G5", "TRK-TRACK-001"]`) | Grant/revoke/check operations |
| `plan_id` | `str` | Test constant (e.g., `"PLAN-TEST-001"`) | Plan free subject lookup |

**Redis State (per test)**:
- `{test_prefix}access:{player_id}` → SET of content keys
- `{test_prefix}plan:{plan_id}:free_subjects` → SET of subject IDs (without `SUB-` prefix)

**Mock Return Values**:
- `mock_frappe.call.return_value = ["SUB-MATH-G5", "SUB-SCI-G5"]` for hydration

---

### Entity 2: ProgressService Test State

| Field | Type | Source | Purpose |
|-------|------|--------|---------|
| `user_id` | `str` | Test constant (e.g., `"USER-TEST-001"`) | Redis key construction |
| `subject_id` | `str` | Test constant (e.g., `"MATH-G5"`) | Redis key construction |
| `version` | `int` | Default `1` | Bitmap versioning |
| `bit_index` | `int` | Test constant (e.g., `0`, `5`, `15`) | Lesson position in bitmap |

**Redis State (per test)**:
- `{test_prefix}progress:{user_id}:{subject_id}:v{version}` → BITMAP (string of bytes)
- `memora:dirty:progress` → SET containing `"{user_id}:{subject_id}:v{version}"`

**Mock Return Values**:
- `mock_frappe.call.return_value = {"passed_lessons_bitset": "8001", "completion_percentage": 25}` for hydration

---

### Entity 3: WalletService Test State

| Field | Type | Source | Purpose |
|-------|------|--------|---------|
| `player_id` | `str` | Test constant (e.g., `"PLAYER-TEST-001"`) | Redis key construction |
| `xp_amount` | `int` | Test constant (e.g., `100`) | XP increment |
| `is_replay` | `bool` | Test parameter | Streak update control |

**Redis State (per test)**:
- `{test_prefix}wallet:{player_id}` → HASH with fields: `xp` (int), `streak` (int), `streak_date` (YYYY-MM-DD)
- `memora:dirty:wallets` → SET containing `"{player_id}"`

**Mock Return Values**:
- `mock_frappe.call.return_value = {"total_xp": 1500, "current_streak": 7}` for hydration

---

### Entity 4: Shared Test Constants

```python
# Player identifiers (unique per test via test_prefix)
TEST_PLAYER = "PLAYER-TEST-001"
TEST_USER = "USER-TEST-001"

# Content keys
TEST_SUBJECT_KEY = "SUB-MATH-G5"
TEST_TRACK_KEY = "TRK-TRACK-001"
TEST_SUBJECT_ID = "MATH-G5"

# Plan
TEST_PLAN_ID = "PLAN-TEST-001"

# Progress
TEST_BIT_INDEX = 5
TEST_VERSION = 1

# XP
TEST_XP_AMOUNT = 100
```

---

## Relationships

```text
conftest.py fixtures
    ├── redis_client ──────→ Service.__init__(redis_client=...)
    ├── test_prefix ───────→ Service.__init__(key_prefix=...)
    ├── mock_frappe ───────→ Service.__init__(frappe_client=...)
    └── cleanup_keys ──────→ SCAN+DEL {test_prefix}* (autouse)

AccessService
    ├── grant_access() ────→ SADD {prefix}access:{player}
    ├── check_access() ────→ SISMEMBER {prefix}access:{player}
    ├── check_access_with_plan() → SISMEMBER {prefix}plan:{plan}:free_subjects
    └── ensure_hydrated() ─→ frappe.call() → SADD (mock boundary)

ProgressService
    ├── complete_lesson() ──→ SETBIT {prefix}progress:{u}:{s}:v{v}
    │                        → SADD memora:dirty:progress (hardcoded)
    ├── is_complete() ──────→ GETBIT {prefix}progress:{u}:{s}:v{v}
    ├── get_completed_count() → BITCOUNT {prefix}progress:{u}:{s}:v{v}
    └── ensure_hydrated() ──→ frappe.call() → SETRANGE (mock boundary)

WalletService
    ├── award_xp() ─────────→ HINCRBY {prefix}wallet:{player} xp
    │                        → SADD memora:dirty:wallets (hardcoded)
    ├── get_wallet() ───────→ HGETALL {prefix}wallet:{player}
    ├── update_streak() ────→ EVALSHA STREAK_UPDATE_SCRIPT
    │                        → SADD memora:dirty:wallets (conditional)
    └── ensure_hydrated() ──→ frappe.call() → HSET (mock boundary)
```
