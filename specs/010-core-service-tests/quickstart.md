# Quickstart: Core Service Tests (Phase 2)

## Prerequisites

1. **Redis running** at `redis://127.0.0.1:13000` (shared with Frappe)
2. **pytest installed** (already in place from Phase 1):
   ```bash
   pip install pytest pytest-asyncio httpx
   ```
3. **Phase 1 infrastructure** complete: `conftest.py` with fixtures

## Run All Phase 2 Tests

```bash
cd /home/corex/aurevia-bench/apps/memora_admin
python -m pytest fastapi_app/tests/test_access_service.py fastapi_app/tests/test_progress_service.py fastapi_app/tests/test_wallet_service.py -v
```

## Run Individual Service Tests

```bash
# AccessService (11 tests)
python -m pytest fastapi_app/tests/test_access_service.py -v

# ProgressService (8 tests)
python -m pytest fastapi_app/tests/test_progress_service.py -v

# WalletService (12 tests)
python -m pytest fastapi_app/tests/test_wallet_service.py -v
```

## Run All Tests (Phase 1 + Phase 2)

```bash
python -m pytest fastapi_app/tests/ -v
```

## Expected Output

```
fastapi_app/tests/test_access_service.py::TestGrantRevoke::test_grant_access_sadd PASSED
fastapi_app/tests/test_access_service.py::TestGrantRevoke::test_revoke_access_srem PASSED
fastapi_app/tests/test_access_service.py::TestCheckAccess::test_check_access_granted_true PASSED
fastapi_app/tests/test_access_service.py::TestCheckAccess::test_check_access_ungranted_false PASSED
fastapi_app/tests/test_access_service.py::TestCheckAccess::test_grant_idempotent PASSED
fastapi_app/tests/test_access_service.py::TestPlanAccess::test_check_with_plan_explicit_first PASSED
fastapi_app/tests/test_access_service.py::TestPlanAccess::test_check_with_plan_fallback PASSED
fastapi_app/tests/test_access_service.py::TestPlanAccess::test_check_with_plan_track_key_no_plan PASSED
fastapi_app/tests/test_access_service.py::TestHydration::test_hydration_skips_when_exists PASSED
fastapi_app/tests/test_access_service.py::TestHydration::test_hydration_calls_frappe PASSED
fastapi_app/tests/test_access_service.py::TestHydration::test_hydration_no_client_logs_warning PASSED
...
31 passed in <5s
```

## Key Test Patterns

### Service Instantiation

```python
# With mock Frappe (most tests)
service = AccessService(redis_client, key_prefix=test_prefix, frappe_client=mock_frappe)

# Without Frappe (hydration-skip tests)
service = AccessService(redis_client, key_prefix=test_prefix, frappe_client=None)
```

### Pre-seeding Redis State

```python
# Access grants
key = f"{test_prefix}access:{player_id}"
await redis_client.sadd(key, "SUB-MATH-G5")

# Progress bitmap
key = f"{test_prefix}progress:{user}:{subject}:v1"
await redis_client.setbit(key, 5, 1)

# Wallet hash
key = f"{test_prefix}wallet:{player_id}"
await redis_client.hset(key, mapping={"xp": "100", "streak": "3", "streak_date": "2026-02-17"})

# Plan free subjects
key = f"{test_prefix}plan:{plan_id}:free_subjects"
await redis_client.sadd(key, "MATH-G5")
```

### Asserting Redis State

```python
# Set membership
assert await redis_client.sismember(f"{test_prefix}access:{player}", "SUB-MATH") == 1

# Bitmap bit
assert await redis_client.getbit(f"{test_prefix}progress:{user}:{subj}:v1", 5) == 1

# Hash field
xp = await redis_client.hget(f"{test_prefix}wallet:{player}", "xp")
assert xp == "150"

# Dirty set (hardcoded prefix)
assert await redis_client.sismember("memora:dirty:wallets", player_id) == 1
```

### Mock Frappe Setup

```python
# Hydration mock
mock_frappe.call.return_value = ["SUB-MATH", "SUB-SCI"]

# Verify call
mock_frappe.call.assert_called_once_with(
    "memora_admin.api.subscriptions.get_player_access_keys",
    {"player_id": "PLAYER-001"},
)

# Verify NOT called
mock_frappe.call.assert_not_called()
```

## Troubleshooting

| Issue | Solution |
|-------|----------|
| `ConnectionError: Redis connection refused` | Ensure Redis is running: `redis-cli -p 13000 ping` |
| Tests pollute each other | Check `test_prefix` is used in service constructor |
| Dirty set assertions fail | Remember dirty keys use hardcoded `memora:dirty:*`, not test_prefix |
| Streak test fails | Use `get_amman_today()` / `get_amman_yesterday()` from wallet.py for date values |
| Hydration test fails | Ensure `mock_frappe.call.return_value` is set BEFORE calling the method |
