"""Tests for RequestMetricsMiddleware - request timing instrumentation."""

from unittest.mock import patch

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from starlette.requests import Request
from starlette.responses import JSONResponse

from fastapi_app.middleware.request_metrics import RequestMetricsMiddleware


def _make_app(raise_error: bool = False) -> FastAPI:
	"""Create a minimal app with the metrics middleware."""
	test_app = FastAPI()

	@test_app.get("/ok")
	async def ok_endpoint():
		return {"status": "ok"}

	@test_app.get("/error")
	async def error_endpoint():
		raise ValueError("boom")

	test_app.add_middleware(RequestMetricsMiddleware)
	return test_app


class TestRequestMetricsMiddleware:
	"""RequestMetricsMiddleware logs timing for normal and error paths."""

	async def test_normal_request_logs_timing(self):
		"""Normal 200 request emits http_request_timed log with correct fields."""
		app = _make_app()
		transport = ASGITransport(app=app)

		with patch("fastapi_app.middleware.request_metrics.logger") as mock_logger:
			async with AsyncClient(transport=transport, base_url="http://test") as client:
				resp = await client.get("/ok")

			assert resp.status_code == 200
			mock_logger.info.assert_called_once()
			call_kwargs = mock_logger.info.call_args
			assert call_kwargs[0][0] == "http_request_timed"
			assert call_kwargs[1]["method"] == "GET"
			assert call_kwargs[1]["path"] == "/ok"
			assert call_kwargs[1]["status_code"] == 200
			assert call_kwargs[1]["duration_ms"] >= 0

	async def test_error_request_logs_timing_with_500(self):
		"""Unhandled exception still emits timing log with status_code=500."""
		app = _make_app()
		transport = ASGITransport(app=app, raise_app_exceptions=False)

		with patch("fastapi_app.middleware.request_metrics.logger") as mock_logger:
			async with AsyncClient(transport=transport, base_url="http://test") as client:
				resp = await client.get("/error")

			assert resp.status_code == 500
			mock_logger.info.assert_called_once()
			call_kwargs = mock_logger.info.call_args
			assert call_kwargs[0][0] == "http_request_timed"
			assert call_kwargs[1]["status_code"] == 500
			assert call_kwargs[1]["duration_ms"] >= 0
