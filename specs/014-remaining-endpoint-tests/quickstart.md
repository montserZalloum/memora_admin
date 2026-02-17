# Quickstart: Remaining Endpoint Tests

**Feature**: 014-remaining-endpoint-tests
**Date**: 2026-02-17

## Prerequisites

All tools are already installed in the bench environment:
- Python 3.11+
- pytest 8.4.2
- pytest-asyncio 0.26.0
- httpx 0.28.1
- redis.asyncio

Redis must be running at `redis://127.0.0.1:13000`.

## Development Workflow

### 1. Create a test file

Each test file follows this template:

```python
"""Tests for {endpoint_group} endpoints."""

import pytest
from unittest.mock import AsyncMock, patch

pytestmark = pytest.mark.asyncio


class TestEndpointGroupName:
    """Description of test group."""

    async def test_happy_path(self, authed_client, redis_client):
        client, token, player_id, family_id = authed_client
        try:
            # Mock the service layer
            with patch("fastapi_app.api.v1.endpoints.{module}.{dependency}") as mock:
                mock_service = AsyncMock()
                mock.return_value = mock_service
                mock_service.method.return_value = {...}

                response = await client.get("/api/v1/{endpoint}")
                assert response.status_code == 200
                data = response.json()
                assert "expected_field" in data
        finally:
            await cleanup_player_keys(redis_client, player_id)

    async def test_unauthenticated(self, app_client):
        response = await app_client.get("/api/v1/{endpoint}")
        assert response.status_code == 401
```

### 2. Key fixtures available

| Fixture | Returns | Use For |
|---------|---------|---------|
| `app_client` | `AsyncClient` | Unauthenticated requests, public endpoints |
| `authed_client` | `(client, token, player_id, family_id)` | Player-authenticated endpoints |
| `admin_client` | `(client, token, email, family_id)` | Admin endpoints |
| `redis_client` | `redis.Redis` | Direct Redis seeding/cleanup |
| `mock_frappe` | `AsyncMock` | FrappeClient mock (injected via app_client) |

### 3. Helper functions (from conftest.py)

```python
from fastapi_app.tests.conftest import (
    seed_wallet,          # seed_wallet(redis, player_id, xp, streak)
    seed_hierarchy,       # seed_hierarchy(redis, subject_id, ...)
    seed_game_session,    # seed_game_session(redis, player_id, lesson_id, subject_id)
    seed_access_grants,   # seed_access_grants(redis, player_id, ["SUB-X"])
    seed_settings,        # seed_settings(redis, **overrides)
    cleanup_player_keys,  # cleanup_player_keys(redis, player_id)
    make_hierarchy_json,  # make_hierarchy_json(subject_id, ...)
)
```

### 4. Mock patching pattern

Always patch at the endpoint module level:

```python
with patch("fastapi_app.api.v1.endpoints.catalog.get_frappe_client") as mock:
    mock_frappe_client = AsyncMock()
    mock.return_value = mock_frappe_client
    mock_frappe_client.call.return_value = {...}
```

For service-level mocking (when endpoints use service dependencies):

```python
# The mock_frappe fixture is already injected via app_client dependency overrides.
# Configure it per test:
mock_frappe.call.return_value = {"your": "data"}
```

### 5. Run tests

```bash
# Run all Phase 6 tests
cd /home/corex/aurevia-bench/apps/memora_admin
python3 -m pytest fastapi_app/tests/test_catalog_endpoints.py fastapi_app/tests/test_purchase_endpoints.py fastapi_app/tests/test_plans_endpoints.py fastapi_app/tests/test_profile_endpoints.py fastapi_app/tests/test_leaderboard_endpoints.py fastapi_app/tests/test_review_endpoints.py fastapi_app/tests/test_settings_endpoints.py fastapi_app/tests/test_subscription_endpoints.py fastapi_app/tests/test_voucher_endpoints.py fastapi_app/tests/test_webhook_endpoints.py fastapi_app/tests/test_notification_endpoints.py -v

# Run single file
python3 -m pytest fastapi_app/tests/test_catalog_endpoints.py -v

# Run full suite (all phases)
python3 -m pytest fastapi_app/tests/ -v --tb=short
```

### 6. WebSocket testing pattern

```python
from starlette.testclient import TestClient
from fastapi_app.main import app

# WebSocket tests use TestClient (sync) not AsyncClient
def test_ws_connect(self):
    client = TestClient(app)
    with client.websocket_connect("/api/v1/notifications/ws?token=valid_jwt") as ws:
        # Connection established
        pass  # or ws.receive_text() for message tests
```

### 7. Public endpoint testing

For endpoints that don't require auth (plans manifest, settings, webhooks):

```python
async def test_public_endpoint(self, app_client):
    # Use app_client directly (no auth token)
    response = await app_client.get("/api/v1/settings/gamification")
    assert response.status_code == 200  # Not 401
```
