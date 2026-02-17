# Quickstart: FastAPI Test Foundation

## Prerequisites

1. **Redis running** at `redis://127.0.0.1:13000` (same instance as Frappe)
2. **Python packages** (all pre-installed in bench environment):
   - pytest 8.4.2+
   - pytest-asyncio 0.26.0+
   - httpx 0.28.1+

## Run All Tests

```bash
cd /home/corex/aurevia-bench/apps/memora_admin
python3 -m pytest fastapi_app/tests/ -v
```

Expected output: 15 tests pass in <5 seconds.

## Run Specific Test Groups

```bash
# XP calculation tests only (11 tests)
python3 -m pytest fastapi_app/tests/test_xp_calculation.py -k "not level" -v

# Level calculation tests only (4 tests)
python3 -m pytest fastapi_app/tests/test_xp_calculation.py -k "level" -v

# Collect tests without running (verify fixture discovery)
python3 -m pytest fastapi_app/tests/ --co
```

## Verify Test Isolation

After running tests, confirm no test keys remain in Redis:

```bash
redis-cli -p 13000 KEYS "test:*"
# Expected: (empty array)
```

## Adding New Tests (Phase 2+)

1. Create a new file in `fastapi_app/tests/test_<service>.py`
2. Import fixtures from `conftest.py` (automatic via pytest discovery)
3. Use `redis_client` and `test_prefix` for Redis operations
4. Use `mock_frappe` to stub FrappeClient calls
5. Use `app_client` or `authed_client` for endpoint tests

Example:
```python
async def test_my_service(redis_client, test_prefix, mock_frappe):
    mock_frappe.call.return_value = {"some": "data"}
    # Test logic using real Redis with test_prefix isolation
    await redis_client.set(f"{test_prefix}mykey", "value")
    # Cleanup is automatic (autouse cleanup_keys fixture)
```
