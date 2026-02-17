# Test Contract: WalletService

**File**: `fastapi_app/tests/test_wallet_service.py`
**Service Under Test**: `fastapi_app/services/wallet.py` → `WalletService`
**Constructor**: `WalletService(redis_client, key_prefix="memora:", frappe_client=None)`
**Lua Script**: `STREAK_UPDATE_SCRIPT` (lines 72-106 of wallet.py)

## Setup Pattern

```python
from fastapi_app.core.constants import DIRTY_WALLETS_KEY
from fastapi_app.services.wallet import WalletService, get_amman_today, get_amman_yesterday

@pytest.fixture
def wallet_service(redis_client, test_prefix, mock_frappe):
    return WalletService(redis_client, key_prefix=test_prefix, frappe_client=mock_frappe)

@pytest.fixture
def wallet_service_no_frappe(redis_client, test_prefix):
    return WalletService(redis_client, key_prefix=test_prefix, frappe_client=None)
```

## Constants

```python
TEST_PLAYER = "PLAYER-TEST-001"
# Redis key: {test_prefix}wallet:PLAYER-TEST-001
# Hash fields: xp (int), streak (int), streak_date (YYYY-MM-DD)
```

## Test Cases (12 total)

### XP Operations (Tests 1-3)

| # | Test Name | Setup | Action | Assertion |
|---|-----------|-------|--------|-----------|
| 1 | `test_award_xp_increment` | None | `award_xp("PLAYER-001", 100)` then `award_xp("PLAYER-001", 50)` | First returns `100`, second returns `150`. `HGET key xp` == `"150"` |
| 2 | `test_award_xp_marks_dirty` | None | `award_xp("PLAYER-001", 100)` | `SISMEMBER memora:dirty:wallets PLAYER-001` == True |
| 3 | `test_get_wallet_defaults` | None (empty hash, mock returns None) | `get_wallet("PLAYER-001")` | Returns `{"xp": 0, "streak": 0}` |

### Wallet Hydration (Tests 4, 11-12)

| # | Test Name | Setup | Action | Assertion |
|---|-----------|-------|--------|-----------|
| 4 | `test_get_wallet_hydrates` | No Redis. `mock_frappe.call.return_value = {"total_xp": 1500, "current_streak": 7}` | `get_wallet("PLAYER-001")` | Returns `{"xp": 1500, "streak": 7}`. `mock_frappe.call` called with `("memora_admin.api.wallet.get_player_wallet", {"player_id": "PLAYER-001"})`. `HGETALL key` has xp=1500, streak=7 |
| 11 | `test_hydration_seeds_redis` | No Redis. `mock_frappe.call.return_value = {"total_xp": 500, "current_streak": 3}` | `ensure_hydrated("PLAYER-001")` | `HGET key xp` == `"500"`, `HGET key streak` == `"3"` |
| 12 | `test_hydration_skips_existing` | `HSET key xp 100` | `ensure_hydrated("PLAYER-001")` | `mock_frappe.call` NOT called. `HGET key xp` == `"100"` (unchanged) |

### Streak Logic - Lua Script (Tests 5-10)

**Date Setup**: Use `get_amman_today()` and `get_amman_yesterday()` for consistent timezone handling.

| # | Test Name | Pre-seed Hash | is_replay | Expected streak | Expected was_updated |
|---|-----------|---------------|-----------|-----------------|---------------------|
| 5 | `test_streak_first_completion` | Empty hash (no fields) | `False` | `1` | `True` |
| 6 | `test_streak_consecutive` | `streak=5, streak_date={yesterday}` | `False` | `6` | `True` |
| 7 | `test_streak_missed_day` | `streak=5, streak_date={2_days_ago}` | `False` | `1` | `True` |
| 8 | `test_streak_same_day` | `streak=3, streak_date={today}` | `False` | `3` | `False` |
| 9 | `test_streak_replay_no_change` | `streak=5, streak_date={yesterday}` | `True` | `5` | `False` |
| 10 | `test_streak_marks_dirty` | Empty hash | `False` | `1` | `True` → `SISMEMBER memora:dirty:wallets PLAYER-001` == True. Then call with `is_replay=True` → dirty set NOT modified again |

**Pre-seeding for streak tests**:
```python
key = f"{test_prefix}wallet:{TEST_PLAYER}"
today = get_amman_today()
yesterday = get_amman_yesterday()
two_days_ago = (datetime.now(ZoneInfo("Asia/Amman")) - timedelta(days=2)).strftime("%Y-%m-%d")

# For consecutive test:
await redis_client.hset(key, mapping={"streak": "5", "streak_date": yesterday})

# For missed day test:
await redis_client.hset(key, mapping={"streak": "5", "streak_date": two_days_ago})

# For same day test:
await redis_client.hset(key, mapping={"streak": "3", "streak_date": today})
```

## Dirty Key Cleanup

```python
@pytest.fixture(autouse=True)
async def cleanup_dirty_wallets(redis_client):
    yield
    await redis_client.srem(DIRTY_WALLETS_KEY, "PLAYER-TEST-001")
```
