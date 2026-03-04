"""Global per-IP rate limiting middleware."""

import time

import redis.asyncio as aioredis
import structlog
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from fastapi_app.core.redis_keys import global_ratelimit_key
from fastapi_app.core.request_meta import get_client_ip
from fastapi_app.services.global_rate_limit import GlobalRateLimiter

logger = structlog.get_logger()

# Paths exempt from rate limiting (prefix match)
EXEMPT_PREFIXES = (
	"/api/v1/health/",
	"/api/v1/webhooks/payment",
)


def _is_exempt(path: str) -> bool:
	"""Check if request path is exempt from rate limiting."""
	for prefix in EXEMPT_PREFIXES:
		if path.startswith(prefix):
			return True
	return False


class GlobalRateLimitMiddleware(BaseHTTPMiddleware):
	"""
	Middleware that enforces global per-IP rate limits.

	Applied to all requests except exempt paths (health checks, webhooks).
	Adds X-RateLimit-* headers to all non-exempt responses.

	Fail behavior on Redis errors is configurable:
	- fail_open=True (default): request passes through with warning log
	- fail_open=False: returns 503 Service Unavailable with Retry-After header

	Redis pool is retrieved from request.app.state.redis_pool (set during lifespan).
	"""

	def __init__(self, app, limit: int, window: int, fail_open: bool = True):
		super().__init__(app)
		self.limit = limit
		self.window = window
		self.fail_open = fail_open
		self._redis_client: aioredis.Redis | None = None
		self._limiter: GlobalRateLimiter | None = None

	def _get_limiter(self, request: Request) -> GlobalRateLimiter:
		"""Lazily initialize and reuse the shared Redis-backed limiter."""
		if self._limiter is None:
			pool = request.app.state.redis_pool
			self._redis_client = aioredis.Redis(connection_pool=pool)
			self._limiter = GlobalRateLimiter(self._redis_client)
		return self._limiter

	async def dispatch(self, request: Request, call_next) -> Response:
		"""Check rate limit before passing request through."""
		path = request.url.path

		# Skip exempt paths entirely
		if _is_exempt(path):
			return await call_next(request)

		# Extract client IP
		client_ip = get_client_ip(request)

		# Check rate limit (fail-open: any error lets request through)
		try:
			limiter = self._get_limiter(request)
			key = global_ratelimit_key(client_ip)
			allowed, count, ttl = await limiter.check(key, self.limit, self.window)
		except Exception:
			if self.fail_open:
				logger.warning("rate_limit_redis_unavailable", path=path, ip=client_ip, fail_open=True)
				return await call_next(request)
			else:
				logger.warning(
					"rate_limit_redis_unavailable_closed", path=path, ip=client_ip, fail_open=False
				)
				response = JSONResponse(
					status_code=503,
					content={
						"error": "SERVICE_UNAVAILABLE",
						"message": "Rate limiting service temporarily unavailable",
						"retry_after": 5,
					},
				)
				response.headers["Retry-After"] = "5"
				return response

		if not allowed:
			# 429 Too Many Requests
			retry_after = max(ttl, 1)
			response = JSONResponse(
				status_code=429,
				content={"error": "RATE_LIMITED", "retry_after": retry_after},
			)
			response.headers["Retry-After"] = str(retry_after)
			response.headers["X-RateLimit-Limit"] = str(self.limit)
			response.headers["X-RateLimit-Remaining"] = "0"
			response.headers["X-RateLimit-Reset"] = str(int(time.time()) + retry_after)
			return response

		# Request allowed — pass through and add headers to response
		response = await call_next(request)
		remaining = max(0, self.limit - count)
		reset_time = int(time.time()) + ttl if ttl > 0 else int(time.time()) + self.window
		response.headers["X-RateLimit-Limit"] = str(self.limit)
		response.headers["X-RateLimit-Remaining"] = str(remaining)
		response.headers["X-RateLimit-Reset"] = str(reset_time)
		return response
