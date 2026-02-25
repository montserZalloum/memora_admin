"""Contract tests for GET /api/v1/health/redis endpoint.

Tests:
- Healthy response (200, all metrics within thresholds)
- Degraded response when buffer is large (mock LLEN >10000)
- Unhealthy/503 response when Redis is unreachable (mock connection error)
- Endpoint requires no authentication
- Response matches RedisHealthReport schema (all 11 fields present)
"""

from unittest.mock import AsyncMock, patch

import pytest
import redis.asyncio as redis

from fastapi_app.tests.conftest import (
	app_client,
	cleanup_keys,
	mock_frappe,
	redis_client,
	test_prefix,
)


HEALTH_URL = "/api/v1/health/redis"

EXPECTED_FIELDS = {
	"status",
	"used_memory_mb",
	"max_memory_mb",
	"memory_usage_percent",
	"interaction_buffer_length",
	"dirty_wallets_count",
	"dirty_progress_count",
	"connected_clients",
	"aof_enabled",
	"uptime_seconds",
	"total_keys",
}


@pytest.mark.asyncio
async def test_healthy_response(app_client):
	"""Healthy Redis returns 200 with all metrics within thresholds."""
	resp = await app_client.get(HEALTH_URL)
	assert resp.status_code == 200

	data = resp.json()
	assert data["status"] == "healthy"
	assert data["used_memory_mb"] >= 0
	assert data["max_memory_mb"] >= 0
	assert data["memory_usage_percent"] >= 0
	assert data["interaction_buffer_length"] >= 0
	assert data["dirty_wallets_count"] >= 0
	assert data["dirty_progress_count"] >= 0
	assert data["connected_clients"] >= 1  # At least our connection
	assert isinstance(data["aof_enabled"], bool)
	assert data["uptime_seconds"] >= 0
	assert data["total_keys"] >= 0


@pytest.mark.asyncio
async def test_response_schema_has_all_fields(app_client):
	"""Response matches RedisHealthReport schema — all 11 fields present."""
	resp = await app_client.get(HEALTH_URL)
	assert resp.status_code == 200

	data = resp.json()
	assert set(data.keys()) == EXPECTED_FIELDS


@pytest.mark.asyncio
async def test_no_auth_required(app_client):
	"""Endpoint does not require authentication — no Authorization header needed."""
	# app_client has no Authorization header set (it's the base client)
	resp = await app_client.get(HEALTH_URL)
	# Should NOT return 401/403
	assert resp.status_code in (200, 503)


@pytest.mark.asyncio
async def test_degraded_when_buffer_large(app_client, redis_client, test_prefix):
	"""Returns degraded status when interaction buffer exceeds 10000."""
	# Use a test-prefixed key to avoid polluting real buffer
	test_buffer_key = f"{test_prefix}buffer:interactions"
	pipe = redis_client.pipeline()
	for _ in range(10001):
		pipe.rpush(test_buffer_key, "test")
	await pipe.execute()

	try:
		# Patch the key function to point to our test key
		with patch("fastapi_app.api.v1.endpoints.health.interaction_buffer_key", return_value=test_buffer_key):
			resp = await app_client.get(HEALTH_URL)
			assert resp.status_code == 200
			data = resp.json()
			assert data["status"] in ("degraded", "unhealthy")
			assert data["interaction_buffer_length"] >= 10001
	finally:
		await redis_client.delete(test_buffer_key)


@pytest.mark.asyncio
async def test_degraded_when_dirty_sets_large(app_client, redis_client, test_prefix):
	"""Returns degraded status when dirty set count exceeds 1000."""
	# Use a test-prefixed key to avoid polluting real dirty set
	test_dirty_key = f"{test_prefix}dirty:wallets"
	pipe = redis_client.pipeline()
	for i in range(1001):
		pipe.sadd(test_dirty_key, f"player-{i}")
	await pipe.execute()

	try:
		# Patch the key function to point to our test key
		with patch("fastapi_app.api.v1.endpoints.health.dirty_wallets_key", return_value=test_dirty_key):
			resp = await app_client.get(HEALTH_URL)
			assert resp.status_code == 200
			data = resp.json()
			assert data["status"] in ("degraded", "unhealthy")
			assert data["dirty_wallets_count"] >= 1001
	finally:
		await redis_client.delete(test_dirty_key)


@pytest.mark.asyncio
async def test_unhealthy_503_when_redis_unreachable(app_client):
	"""Returns 503 with unhealthy status when Redis is unreachable."""
	from fastapi_app.main import app
	from fastapi_app.api.deps import get_redis

	# Save original override before replacing
	original_override = app.dependency_overrides.get(get_redis)

	# Create a client pointing to a non-existent Redis
	bad_client = redis.Redis(host="127.0.0.1", port=19999, decode_responses=True)
	app.dependency_overrides[get_redis] = lambda: bad_client

	try:
		resp = await app_client.get(HEALTH_URL)
		assert resp.status_code == 503
		data = resp.json()
		assert data["status"] == "unhealthy"
		assert set(data.keys()) == EXPECTED_FIELDS
	finally:
		# Restore original override
		if original_override is not None:
			app.dependency_overrides[get_redis] = original_override
		else:
			app.dependency_overrides.pop(get_redis, None)
		await bad_client.aclose()
