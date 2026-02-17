# Quickstart: Core Endpoint Tests

**Feature Branch**: `013-core-endpoint-tests`

## Prerequisites

- Python 3.11+
- Redis running at `redis://127.0.0.1:13000`
- Existing test infrastructure from Phases 1-4 (131 passing service tests)

## Running Tests

```bash
cd /home/corex/aurevia-bench/apps/memora_admin

# Run all tests (existing + new endpoint tests)
python3 -m pytest fastapi_app/tests/ -v

# Run only endpoint tests
python3 -m pytest fastapi_app/tests/ -k "endpoint" -v

# Run specific endpoint test file
python3 -m pytest fastapi_app/tests/test_health_endpoints.py -v
python3 -m pytest fastapi_app/tests/test_auth_endpoints.py -v
python3 -m pytest fastapi_app/tests/test_session_endpoints.py -v
python3 -m pytest fastapi_app/tests/test_progress_endpoints.py -v
python3 -m pytest fastapi_app/tests/test_wallet_endpoints.py -v
python3 -m pytest fastapi_app/tests/test_access_endpoints.py -v
```

## Implementation Order

1. **Fix conftest.py** — Session key prefix mismatch (R-001 from research.md)
2. **Add helper fixtures** — `seed_hierarchy`, `seed_game_session`, `seed_settings`, etc.
3. **test_health_endpoints.py** (4 tests) — Simplest, validates infrastructure works
4. **test_wallet_endpoints.py** (4 tests) — Simple auth + service call pattern
5. **test_access_endpoints.py** (6 tests) — Admin auth + CRUD pattern
6. **test_auth_endpoints.py** (25 tests) — Complex, many mock patterns
7. **test_progress_endpoints.py** (10 tests) — Access control + hierarchy
8. **test_session_endpoints.py** (15 tests) — Most complex, full game loop

## Key Patterns

### Making Authenticated Requests

```python
async def test_example(authed_client):
    client, token, player_id, family_id = authed_client
    resp = await client.get("/api/v1/wallet")
    assert resp.status_code == 200
```

### Making Admin Requests

```python
async def test_admin_example(admin_client):
    client, token, email, family_id = admin_client
    resp = await client.get("/api/v1/wallet/PLAYER-001")
    assert resp.status_code == 200
```

### Seeding Redis State

```python
async def test_with_state(authed_client, redis_client):
    client, token, player_id, family_id = authed_client

    # Seed hierarchy
    hierarchy = make_hierarchy_json(subject_id="SUB-001")
    await redis_client.set("memora:hierarchy:SUB-001", json.dumps(hierarchy), ex=3600)

    # Seed access grant
    await redis_client.sadd(f"memora:access:{player_id}", "SUB-SUB-001")

    resp = await client.get("/api/v1/progress/SUB-001")
    assert resp.status_code == 200
```

### Mocking FrappeClient for Auth Routes

```python
async def test_login(app_client, mock_frappe):
    # Configure mock response
    mock_frappe.call.return_value = {
        "player_id": "PLAYER-001",
        "mobile": "201000000000",
        "plan": "PLAN-001",
        "display_name": "Test Player",
    }

    resp = await app_client.post(
        "/api/v1/auth/player/login",
        json={"mobile": "201000000000", "password": "test123"},
        headers={"X-Device-ID": "test-device-001"},
    )
    assert resp.status_code == 200
```

## Verification

After implementation, verify:

```bash
# All ~195 tests pass (131 existing + ~64 new)
python3 -m pytest fastapi_app/tests/ -v --tb=short

# Tests complete in under 30 seconds
python3 -m pytest fastapi_app/tests/ --durations=10

# No tests use time.sleep (constitution Gate 1)
grep -r "time.sleep" fastapi_app/tests/test_*_endpoints.py && echo "FAIL: time.sleep found" || echo "PASS"

# No tests import from excluded scope (constitution Gate 1)
grep -r "voucher\|allocation" fastapi_app/tests/test_*_endpoints.py && echo "FAIL: excluded import" || echo "PASS"
```
