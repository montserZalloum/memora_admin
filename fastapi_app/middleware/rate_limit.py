"""Global per-IP rate limiting middleware."""

import time

import redis.asyncio as aioredis
import structlog
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from fastapi_app.core.redis_keys import global_ratelimit_key
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


def _extract_client_ip(request: Request) -> str:
	"""Extract client IP from X-Forwarded-For or request.client.host."""
	forwarded_for = request.headers.get("X-Forwarded-For")
	if forwarded_for:
		# First entry is the real client IP (set by nginx)
		return forwarded_for.split(",")[0].strip()
	if request.client:
		return request.client.host
	return "unknown"


class GlobalRateLimitMiddleware(BaseHTTPMiddleware):
	"""
	Middleware that enforces global per-IP rate limits.

	Applied to all requests except exempt paths (health checks, webhooks).
	Adds X-RateLimit-* headers to all non-exempt responses.
	Fails open on Redis errors (request passes through).

	Redis pool is retrieved from request.app.state.redis_pool (set during lifespan).
	"""

	def __init__(self, app, limit: int, window: int):
		super().__init__(app)
		self.limit = limit
		self.window = window

	async def dispatch(self, request: Request, call_next) -> Response:
		"""Check rate limit before passing request through."""
		path = request.url.path

		# Skip exempt paths entirely
		if _is_exempt(path):
			return await call_next(request)

		# Extract client IP
		client_ip = _extract_client_ip(request)

		# Check rate limit (fail-open: any error lets request through)
		try:
			pool = request.app.state.redis_pool
			redis_client = aioredis.Redis(connection_pool=pool)
			limiter = GlobalRateLimiter(redis_client)
			key = global_ratelimit_key(client_ip)
			allowed, count, ttl = await limiter.check(key, self.limit, self.window)
		except Exception:
			logger.warning("rate_limit_redis_unavailable", path=path, ip=client_ip)
			return await call_next(request)

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
