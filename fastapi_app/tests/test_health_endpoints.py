"""Tests for health check endpoints."""

import pytest
import redis.asyncio as redis
from httpx import AsyncClient
from unittest.mock import patch, AsyncMock

# Mark all tests as async
pytestmark = pytest.mark.asyncio


class TestLivenessEndpoint:
	"""Tests for GET /api/v1/health/live (liveness check)."""

	async def test_liveness_ok(self, app_client: AsyncClient) -> None:
		"""
		Liveness check should return 200 with status and api_version.

		GET /api/v1/health/live (no auth required)
		→ 200 OK
		→ {status: "alive", api_version: "v1"}
		"""
		response = await app_client.get("/api/v1/health/live")

		assert response.status_code == 200
		data = response.json()
		assert data["status"] == "alive"
		assert data["api_version"] == "v1"

	async def test_liveness_no_auth_required(self, app_client: AsyncClient) -> None:
		"""
		Liveness check must not require authentication.

		GET /api/v1/health/live (without Authorization header)
		→ 200 OK
		"""
		# Ensure no Authorization header
		if "Authorization" in app_client.headers:
			del app_client.headers["Authorization"]

		response = await app_client.get("/api/v1/health/live")

		assert response.status_code == 200


class TestReadinessEndpoint:
	"""Tests for GET /api/v1/health/ready (readiness check)."""

	async def test_readiness_ok(self, app_client: AsyncClient) -> None:
		"""
		Readiness check should verify Redis and return 200 when healthy.

		GET /api/v1/health/ready
		→ 200 OK
		→ {status: "ready", dependencies: {redis: "ok"}}
		"""
		response = await app_client.get("/api/v1/health/ready")

		assert response.status_code == 200
		data = response.json()
		assert data["status"] == "ready"
		assert data["api_version"] == "v1"
		assert "dependencies" in data
		assert data["dependencies"]["redis"] == "ok"

	async def test_readiness_redis_down(self, app_client: AsyncClient, redis_client: redis.Redis) -> None:
		"""
		Readiness check should return 503 when Redis is unreachable.

		Mock redis.ping() to raise ConnectionError
		→ 503 Service Unavailable
		→ {status: "not_ready", dependencies: {redis: "unreachable"}}
		"""
		# Mock the redis.ping() to raise ConnectionError
		with patch.object(redis_client, "ping", side_effect=redis.ConnectionError("connection failed")):
			response = await app_client.get("/api/v1/health/ready")

		assert response.status_code == 503
		data = response.json()
		assert data["status"] == "not_ready"
		assert data["dependencies"]["redis"] == "unreachable"
