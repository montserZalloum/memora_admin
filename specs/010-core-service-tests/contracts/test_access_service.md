# Test Contract: AccessService

**File**: `fastapi_app/tests/test_access_service.py`
**Service Under Test**: `fastapi_app/services/access.py` → `AccessService`
**Constructor**: `AccessService(redis_client, key_prefix="memora:", frappe_client=None)`

## Setup Pattern

```python
@pytest.fixture
def access_service(redis_client, test_prefix, mock_frappe):
    return AccessService(redis_client, key_prefix=test_prefix, frappe_client=mock_frappe)

@pytest.fixture
def access_service_no_frappe(redis_client, test_prefix):
    return AccessService(redis_client, key_prefix=test_prefix, frappe_client=None)
```

## Test Cases (11 total)

### Grant & Revoke (Tests 1-2)

| # | Test Name | Setup | Action | Assertion |
|---|-----------|-------|--------|-----------|
| 1 | `test_grant_access_sadd` | None | `grant_access("PLAYER-001", ["SUB-MATH", "SUB-SCI"])` | Returns `2`. `SMEMBERS {prefix}access:PLAYER-001` == `{"SUB-MATH", "SUB-SCI"}` |
| 2 | `test_revoke_access_srem` | Grant `["SUB-MATH", "SUB-SCI"]` | `revoke_access("PLAYER-001", ["SUB-MATH"])` | Returns `1`. `SMEMBERS` == `{"SUB-SCI"}` |

### Check Access (Tests 3-5)

| # | Test Name | Setup | Action | Assertion |
|---|-----------|-------|--------|-----------|
| 3 | `test_check_access_granted_true` | Grant `["SUB-MATH"]` | `check_access("PLAYER-001", "SUB-MATH")` | Returns `True` |
| 4 | `test_check_access_ungranted_false` | None (empty set) | `check_access("PLAYER-001", "SUB-MATH")` | Returns `False`. `mock_frappe.call` was called (hydration attempted) |
| 5 | `test_grant_idempotent` | Grant `["SUB-MATH"]` | `grant_access("PLAYER-001", ["SUB-MATH"])` | Returns `0` (no new keys) |

### Plan-Based Access (Tests 6-8)

| # | Test Name | Setup | Action | Assertion |
|---|-----------|-------|--------|-----------|
| 6 | `test_check_with_plan_explicit_first` | Grant `["SUB-MATH-G5"]` | `check_access_with_plan("PLAYER-001", "SUB-MATH-G5", "PLAN-001")` | Returns `True`. Plan free subjects NOT checked (short-circuit) |
| 7 | `test_check_with_plan_fallback` | No grant. `SADD {prefix}plan:PLAN-001:free_subjects MATH-G5` | `check_access_with_plan("PLAYER-001", "SUB-MATH-G5", "PLAN-001")` | Returns `True` (plan fallback) |
| 8 | `test_check_with_plan_track_key_no_plan` | No grant. Plan has free subjects | `check_access_with_plan("PLAYER-001", "TRK-TRACK-001", "PLAN-001")` | Returns `False` (TRK- keys skip plan check) |

### Hydration (Tests 9-11)

| # | Test Name | Setup | Action | Assertion |
|---|-----------|-------|--------|-----------|
| 9 | `test_hydration_skips_when_exists` | `SADD {prefix}access:PLAYER-001 SUB-X` | `ensure_hydrated("PLAYER-001")` | `mock_frappe.call` NOT called |
| 10 | `test_hydration_calls_frappe` | No Redis data | `ensure_hydrated("PLAYER-001")` with `mock_frappe.call.return_value = ["SUB-MATH", "SUB-SCI"]` | `mock_frappe.call` called with `("memora_admin.api.subscriptions.get_player_access_keys", {"player_id": "PLAYER-001"})`. `SMEMBERS` == `{"SUB-MATH", "SUB-SCI"}` |
| 11 | `test_hydration_no_client_logs_warning` | No Redis data, use `access_service_no_frappe` | `ensure_hydrated("PLAYER-001")` | No crash. `SMEMBERS` returns empty set |
