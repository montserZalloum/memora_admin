"""Request ID middleware for correlation (pure ASGI — no BaseHTTPMiddleware)."""

import uuid

import structlog
from starlette.datastructures import MutableHeaders


class RequestIDMiddleware:
	"""Middleware to add request ID for correlation.

	Pure ASGI implementation: wraps the send callable to inject X-Request-ID
	into the response start message. No extra task creation per request.
	"""

	def __init__(self, app):
		self.app = app

	async def __call__(self, scope, receive, send):
		if scope["type"] != "http":
			await self.app(scope, receive, send)
			return

		request_id = str(uuid.uuid4())[:8]
		structlog.contextvars.clear_contextvars()
		structlog.contextvars.bind_contextvars(request_id=request_id)

		async def send_with_id(message):
			if message["type"] == "http.response.start":
				headers = MutableHeaders(scope=message)
				headers.append("X-Request-ID", request_id)
			await send(message)

		await self.app(scope, receive, send_with_id)
