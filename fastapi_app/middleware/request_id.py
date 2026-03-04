"""Request ID middleware for correlation."""

import uuid

import structlog
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response


class RequestIDMiddleware(BaseHTTPMiddleware):
	"""Middleware to add request ID for correlation."""

	async def dispatch(self, request: Request, call_next) -> Response:
		"""Add request ID to context and response headers."""
		request_id = str(uuid.uuid4())[:8]
		structlog.contextvars.clear_contextvars()
		structlog.contextvars.bind_contextvars(request_id=request_id)

		response = await call_next(request)
		response.headers["X-Request-ID"] = request_id
		return response
