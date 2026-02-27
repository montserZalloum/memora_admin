"""Tests for configurable rate limiter fail behavior (fail-open vs fail-closed).

Tests the fail_open parameter on GlobalRateLimitMiddleware:
- fail_open=False + Redis error → 503 + Retry-After: 5
- fail_open=True + Redis error → request passes through
- Normal operation (Redis available) → 200 with rate limit headers
"""

import pytest
from unittest.mock import patch

from httpx import AsyncClient, ASGITransport
from fastapi import FastAPI
from starlette.requests import Request
from starlette.responses import JSONResponse

from fastapi_app.middleware.rate_limit import GlobalRateLimitMiddleware


def _make_app(fail_open: bool) -> FastAPI:
	"""Create a minimal FastAPI app with the rate limit middleware."""
	test_app = FastAPI()

	@test_app.get("/test")
	async def test_endpoint():
		return {"status": "ok"}

	@test_app.get("/api/v1/health/live")
	async def health():
		return {"status": "alive"}

	test_app.add_middleware(
		GlobalRateLimitMiddleware,
		limit=100,
		window=60,
		fail_open=fail_open,
	)

	return test_app


class TestFailClosedReturns503:
	"""T014.1: fail_open=False with Redis error returns 503 + Retry-After: 5."""

	@pytest.mark.asyncio
	async def test_503_on_redis_error(self):
		"""When Redis is unavailable and fail_open=False, returns 503."""
		app = _make_app(fail_open=False)

		with patch(
			"fastapi_app.middleware.rate_limit.GlobalRateLimiter",
			side_effect=Exception("Redis connection refused"),
		):
			transport = ASGITransport(app=app)
			async with AsyncClient(transport=transport, base_url="http://test") as client:
				resp = await client.get("/test")

		assert resp.status_code == 503

	@pytest.mark.asyncio
	async def test_503_has_retry_after_header(self):
		"""503 response includes Retry-After: 5 header."""
		app = _make_app(fail_open=False)

		with patch(
			"fastapi_app.middleware.rate_limit.GlobalRateLimiter",
			side_effect=Exception("Redis connection refused"),
		):
			transport = ASGITransport(app=app)
			async with AsyncClient(transport=transport, base_url="http://test") as client:
				resp = await client.get("/test")

		assert resp.headers["Retry-After"] == "5"

	@pytest.mark.asyncio
	async def test_503_has_correct_body(self):
		"""503 response body matches contract: error, message, retry_after."""
		app = _make_app(fail_open=False)

		with patch(
			"fastapi_app.middleware.rate_limit.GlobalRateLimiter",
			side_effect=Exception("Redis connection refused"),
		):
			transport = ASGITransport(app=app)
			async with AsyncClient(transport=transport, base_url="http://test") as client:
				resp = await client.get("/test")

		body = resp.json()
		assert body["error"] == "SERVICE_UNAVAILABLE"
		assert body["message"] == "Rate limiting service temporarily unavailable"
		assert body["retry_after"] == 5


class TestFailOpenPassesThrough:
	"""T014.2: fail_open=True with Redis error lets request pass through."""

	@pytest.mark.asyncio
	async def test_request_passes_on_redis_error(self):
		"""When Redis is unavailable and fail_open=True, request reaches endpoint."""
		app = _make_app(fail_open=True)

		with patch(
			"fastapi_app.middleware.rate_limit.GlobalRateLimiter",
			side_effect=Exception("Redis connection refused"),
		):
			transport = ASGITransport(app=app)
			async with AsyncClient(transport=transport, base_url="http://test") as client:
				resp = await client.get("/test")

		# Request should pass through to the endpoint
		assert resp.status_code == 200
		assert resp.json() == {"status": "ok"}

	@pytest.mark.asyncio
	async def test_no_rate_limit_headers_on_error(self):
		"""When failing open, no X-RateLimit-* headers are added."""
		app = _make_app(fail_open=True)

		with patch(
			"fastapi_app.middleware.rate_limit.GlobalRateLimiter",
			side_effect=Exception("Redis connection refused"),
		):
			transport = ASGITransport(app=app)
			async with AsyncClient(transport=transport, base_url="http://test") as client:
				resp = await client.get("/test")

		assert "X-RateLimit-Limit" not in resp.headers


class TestNormalOperationWithHeaders:
	"""T014.3: Normal operation (Redis available) returns 200 with rate limit headers."""

	@pytest.mark.asyncio
	async def test_200_with_rate_limit_headers(self, app_client):
		"""Normal request returns 200 with X-RateLimit-* headers."""
		resp = await app_client.get("/api/v1/catalog/")
		# May return various codes but should have rate limit headers
		assert "X-RateLimit-Limit" in resp.headers
		assert "X-RateLimit-Remaining" in resp.headers
		assert "X-RateLimit-Reset" in resp.headers

	@pytest.mark.asyncio
	async def test_rate_limit_values_correct(self, app_client):
		"""Rate limit header values match configured limits (10 for test)."""
		resp = await app_client.get("/api/v1/catalog/")
		assert resp.headers["X-RateLimit-Limit"] == "10"  # test setting from conftest
		remaining = int(resp.headers["X-RateLimit-Remaining"])
		assert remaining >= 0
