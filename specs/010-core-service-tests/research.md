# Research: Core Service Tests (Phase 2)

**Feature**: 010-core-service-tests | **Date**: 2026-02-17

## Research Questions & Findings

### RQ-1: How do service constructors accept test isolation parameters?

**Decision**: All three services use identical constructor pattern — `key_prefix` parameter defaults to `"memora:"` and is used in all Redis key generation methods.

**Evidence**:
```python
# AccessService, ProgressService, WalletService — identical pattern:
def __init__(self, redis_client, key_prefix="memora:", frappe_client=None):
    self.redis = redis_client
    self.prefix = key_prefix
    self.frappe = frappe_client
```

**Test Pattern**: Instantiate with `key_prefix=test_prefix` from conftest fixture:
```python
service = AccessService(redis_client, key_prefix=test_prefix, frappe_client=mock_frappe)
```

**Rationale**: No wrapper needed — services are already test-friendly by design.
**Alternatives considered**: Dependency override via FastAPI (used for endpoint tests, but unnecessary for direct service tests).

---

### RQ-2: How does dirty tracking work for each service?

**Decision**: Each service uses a different dirty tracking strategy. Tests must verify dirty set membership after mutations.

| Service | Dirty Key | Member Format | When Added |
|---------|-----------|---------------|------------|
| AccessService | *None* | N/A | Access grants are synchronous (Frappe hooks push to Redis) |
| ProgressService | `memora:dirty:progress` (hardcoded) | `"{user}:{subject}:v{version}"` | Every `complete_lesson()` call |
| WalletService | `memora:dirty:wallets` (hardcoded) | `"{player_id}"` | `award_xp()` always; `update_streak()` only when `was_updated=True` |

**Important**: Dirty keys use hardcoded `"memora:dirty:*"` prefix (NOT the test prefix). Tests must:
1. Verify membership with `SISMEMBER` on the hardcoded key
2. Clean up by `SREM` in teardown (or accept that `cleanup_keys` only cleans `test_prefix*` keys)

**Rationale**: Dirty keys are shared infrastructure consumed by sync tasks. They intentionally don't use the service's key_prefix.

---

### RQ-3: How does the Lua STREAK_UPDATE_SCRIPT work and what are all its branches?

**Decision**: The Lua script has 4 mutually exclusive branches based on `is_replay`, `streak_date == today`, `streak_date == yesterday`, and fallback.

**Complete Branch Table**:

| # | Condition | streak Result | was_updated | HSET calls |
|---|-----------|---------------|-------------|------------|
| 1 | `is_replay == 1` | Current (unchanged) | 0 (False) | None |
| 2 | `streak_date == today` | Current (unchanged) | 0 (False) | None |
| 3 | `streak_date == yesterday` | current + 1 | 1 (True) | streak, streak_date |
| 4 | Other (missed day or first) | 1 | 1 (True) | streak, streak_date |

**Test Setup Per Branch**:
- Branch 1: Any initial state + `is_replay=True`
- Branch 2: Pre-seed `HSET key streak_date {today}` + `is_replay=False`
- Branch 3: Pre-seed `HSET key streak 5 streak_date {yesterday}` + `is_replay=False`
- Branch 4: Pre-seed `HSET key streak 5 streak_date {2_days_ago}` + `is_replay=False` (or empty hash)

**Date Utilities**: `wallet.get_amman_today()` and `wallet.get_amman_yesterday()` use `Asia/Amman` timezone. Tests should use these same functions for date values to avoid timezone mismatch.

**Rationale**: Testing the Lua script requires pre-seeding Redis hash fields directly, then calling `update_streak()` which executes the registered script.
**Alternatives considered**: Mocking `get_amman_today()`/`get_amman_yesterday()` for deterministic dates — rejected because using the actual utility functions ensures correct timezone behavior and the tests don't depend on wall clock (streak logic compares stored date vs passed date, not clock time).

---

### RQ-4: How does hydration work and what Frappe API methods are called?

**Decision**: Each service calls a different Frappe whitelisted method via `self.frappe.call()`.

| Service | Frappe Method | Expected Return | Redis Command |
|---------|---------------|-----------------|---------------|
| AccessService | `memora_admin.api.subscriptions.get_player_access_keys` | `list[str]` (access keys) | `SADD key *result` |
| ProgressService | `memora_admin.api.subscriptions.get_player_progress` | `{"passed_lessons_bitset": hex_str}` | `SETRANGE key 0 bytes.fromhex(hex)` |
| WalletService | `memora_admin.api.wallet.get_player_wallet` | `{"total_xp": int, "current_streak": int}` | `HSET key mapping={xp, streak}` |

**Mock Setup Pattern**:
```python
# AccessService hydration mock
mock_frappe.call.return_value = ["SUB-MATH-G5", "SUB-SCI-G5"]

# ProgressService hydration mock
mock_frappe.call.return_value = {"passed_lessons_bitset": "80010000"}

# WalletService hydration mock
mock_frappe.call.return_value = {"total_xp": 1500, "current_streak": 7}
```

**Hydration Guard**: All services check `self.redis.exists(key)` first. If key exists, hydration is skipped (no Frappe call). Tests for "hydration skips existing" must pre-seed the Redis key before calling the method.

**Rationale**: Mock at `frappe.call()` boundary — this is the single HTTP integration point.

---

### RQ-5: How should check_access_with_plan be tested for plan-based fallback?

**Decision**: The method checks explicit grants first, then falls back to plan free subjects. Tests must pre-seed `{prefix}plan:{plan_id}:free_subjects` set.

**Logic**:
```python
# 1. Check explicit grant (calls ensure_hydrated internally)
if await self.check_access(player_id, content_key):
    return True

# 2. Only SUB-* keys trigger plan fallback
if plan_id and content_key.startswith("SUB-"):
    subject_id = content_key.replace("SUB-", "")
    if await self.is_subject_free_in_plan(plan_id, subject_id):
        return True

return False
```

**Test Cases**:
- `test_check_with_plan_explicit_first`: Grant `SUB-MATH` explicitly → returns True without checking plan
- `test_check_with_plan_fallback`: No grant, but `SADD {prefix}plan:PLAN-001:free_subjects MATH-G5` → `check_access_with_plan(player, "SUB-MATH-G5", "PLAN-001")` returns True
- `test_check_with_plan_track_key_no_plan`: `TRK-*` keys never trigger plan fallback → returns False

**Rationale**: Validates the additive access model (explicit OR plan membership) and the SUB-* filtering logic.

---

### RQ-6: How are dirty tracking keys cleaned up in tests?

**Decision**: Dirty keys (`memora:dirty:progress`, `memora:dirty:wallets`) use hardcoded prefixes that don't match the `test_prefix` pattern. The `cleanup_keys` autouse fixture only cleans `test:{uuid}:*` keys.

**Solution**: Tests that add to dirty sets must manually clean them in teardown:
```python
# Option A: Use pytest fixture for dirty key cleanup
@pytest.fixture(autouse=True)
async def cleanup_dirty_keys(redis_client):
    yield
    # Remove any test entries from dirty sets
    await redis_client.delete("memora:dirty:progress")
    await redis_client.delete("memora:dirty:wallets")
```

**However**: This is aggressive (deletes ALL dirty entries). Better approach:
```python
# Option B: Remove only our test entries
yield
await redis_client.srem(DIRTY_PROGRESS_KEY, f"{test_player}:{test_subject}:v1")
await redis_client.srem(DIRTY_WALLETS_KEY, test_player_id)
```

**Decision**: Use Option B (targeted cleanup) per test to avoid affecting other processes.

**Rationale**: Preserves production dirty set entries while keeping tests isolated.

---

### RQ-7: What is the hex bitmap hydration format?

**Decision**: Progress bitmaps are stored as hex strings in MariaDB and restored via `bytes.fromhex()` + `SETRANGE`.

**Bit Ordering**: Redis bitmap uses MSB-first ordering:
- Bit 0 = leftmost bit of byte 0 (mask 0x80)
- Bit 7 = rightmost bit of byte 0 (mask 0x01)
- Bit 8 = leftmost bit of byte 1

**Example**: To set bits 0 and 15:
- Bit 0 → byte 0, mask 0x80 → byte = 0x80
- Bit 15 → byte 1, mask 0x01 → byte = 0x01
- Hex string: `"8001"`
- `bytes.fromhex("8001")` → `b'\x80\x01'`
- `SETRANGE key 0 b'\x80\x01'` → Redis bitmap with bits 0 and 15 set

**Test Approach**: Mock Frappe to return `{"passed_lessons_bitset": "8001"}`, then verify `GETBIT key 0 == 1` and `GETBIT key 15 == 1` and `BITCOUNT key == 2`.

**Rationale**: Validates the full hydration round-trip (hex → bytes → SETRANGE → GETBIT).
