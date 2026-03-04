"""Lightweight ASGI middleware for request timing."""

import time

import structlog
from starlette.requests import Request

logger = structlog.get_logger()


class RequestMetricsMiddleware:
	def __init__(self, app):
		self.app = app

	async def __call__(self, scope, receive, send):
		if scope["type"] != "http":
			await self.app(scope, receive, send)
			return

		start = time.perf_counter()
		status_code = 500

		async def send_wrapper(message):
			nonlocal status_code
			if message["type"] == "http.response.start":
				status_code = message["status"]
			await send(message)

		try:
			await self.app(scope, receive, send_wrapper)
		finally:
			duration_ms = round((time.perf_counter() - start) * 1000, 2)
			request = Request(scope)
			logger.info(
				"http_request_timed",
				method=request.method,
				path=request.url.path,
				status_code=status_code,
				duration_ms=duration_ms,
			)
