# Quickstart: Session + Auth Service Tests

**Feature**: 011-session-auth-tests
**Date**: 2026-02-17

## Prerequisites

- Redis running at `redis://127.0.0.1:13000`
- Python 3.11+ with pytest, pytest-asyncio, redis.asyncio installed
- `user-agents` package installed (for DeviceService fingerprint generation)

## Running Tests

```bash
# Run all Phase 3 tests
python3 -m pytest fastapi_app/tests/test_game_session_service.py fastapi_app/tests/test_otp_service.py fastapi_app/tests/test_session_service.py fastapi_app/tests/test_rate_limiter.py fastapi_app/tests/test_device_service.py -v

# Run a single test file
python3 -m pytest fastapi_app/tests/test_game_session_service.py -v

# Run all FastAPI tests (Phase 1 + 2 + 3)
python3 -m pytest fastapi_app/tests/ -v
```

## Test File Structure

Each test file follows this pattern:

```python
"""Tests for {ServiceName}."""
import json
import pytest
from fastapi_app.services.{service_module} import {ServiceClass}

# Test constants
TEST_USER = "PLAYER-TEST-001"
TEST_SUBJECT = "MATH-G5"

class TestFeatureGroup:
    """Group related test scenarios."""

    async def test_happy_path(self, redis_client, test_prefix, service_fixture):
        """Given X, When Y, Then Z."""
        # Act
        result = await service.method(args)

        # Assert service return value
        assert result == expected

        # Assert Redis state directly
        key = f"{test_prefix}key:{id}"
        raw = await redis_client.get(key)
        assert raw is not None
```

## Service Fixture Pattern

```python
@pytest.fixture
def game_session_service(redis_client, test_prefix):
    """GameSessionService with test prefix isolation."""
    return GameSessionService(redis_client, key_prefix=test_prefix)
```

## Key Isolation

All services accept `key_prefix` parameter. Tests pass `test_prefix` (e.g., `test:a1b2c3d4:`) to isolate Redis keys. The `cleanup_keys` autouse fixture in `conftest.py` auto-deletes all matching keys after each test.

**Exception**: `DIRTY_PROGRESS_KEY` and `INTERACTION_BUFFER_KEY` are hardcoded global constants. Tests that use `complete_session` must clean these up with an additional autouse fixture.

## File Map

| File | Service | Tests | Priority |
|------|---------|-------|----------|
| `test_game_session_service.py` | GameSessionService | ~8 | P1 |
| `test_otp_service.py` | OTPService | ~12 | P1 |
| `test_session_service.py` | SessionService | ~5 | P2 |
| `test_rate_limiter.py` | RateLimiter | ~6 | P2 |
| `test_device_service.py` | DeviceService | ~8 | P2 |
| **Total** | | **~39** | |
