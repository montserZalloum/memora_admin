"""Global per-IP rate limiting middleware (pure ASGI — no BaseHTTPMiddleware)."""

import time

import redis.asyncio as aioredis
import structlog
from starlette.datastructures import MutableHeaders
from starlette.responses import JSONResponse

from fastapi_app.core.redis_keys import global_ratelimit_key
from fastapi_app.services.global_rate_limit import GlobalRateLimiter

logger = structlog.get_logger()

# Paths exempt from global per-IP rate limiting (prefix match).
# Live-challenge endpoints are exempt because 10k users behind a school NAT
# or carrier gateway would exhaust the global 100 req/IP/60s budget.
# Per-player rate limits protect all authenticated endpoints individually:
#   lc_join: 5, lc_submit: 2, lc_read: 20 (detail/questions/result/leaderboard).
# GET /status is unauthenticated (no player key) but sub-2ms Redis-only — low risk.
EXEMPT_PREFIXES = (
	"/api/v1/health/",
	"/api/v1/webhooks/payment",
	"/api/v1/live-challenge/",
)


def _is_exempt(path: str) -> bool:
	"""Check if request path is exempt from rate limiting."""
	for prefix in EXEMPT_PREFIXES:
		if path.startswith(prefix):
			return True
	return False


class GlobalRateLimitMiddleware:
	"""
	Middleware that enforces global per-IP rate limits (pure ASGI).

	Applied to all requests except exempt paths (health checks, webhooks).
	Adds X-RateLimit-* headers to all non-exempt responses.

	Fail behavior on Redis errors is configurable:
	- fail_open=True (default): request passes through with warning log
	- fail_open=False: returns 503 Service Unavailable with Retry-After header

	Redis pool is retrieved from scope["app"].state.redis_pool (set during lifespan).
	"""

	def __init__(self, app, limit: int, window: int, fail_open: bool = True):
		self.app = app
		self.limit = limit
		self.window = window
		self.fail_open = fail_open
		self._redis_client: aioredis.Redis | None = None
		self._limiter: GlobalRateLimiter | None = None

	def _get_limiter(self, app_state) -> GlobalRateLimiter:
		"""Lazily initialize and reuse the shared Redis-backed limiter."""
		if self._limiter is None:
			self._redis_client = aioredis.Redis(connection_pool=app_state.redis_pool)
			self._limiter = GlobalRateLimiter(self._redis_client)
		return self._limiter

	async def __call__(self, scope, receive, send):
		"""Check rate limit before passing request through."""
		if scope["type"] != "http":
			await self.app(scope, receive, send)
			return

		path = scope["path"]

		# Skip exempt paths entirely
		if _is_exempt(path):
			await self.app(scope, receive, send)
			return

		# Extract client IP directly from ASGI scope headers (no Request object)
		client_ip = "unknown"
		for name, value in scope.get("headers", []):
			if name == b"x-forwarded-for":
				client_ip = value.decode("latin-1").split(",")[0].strip()
				break
		if client_ip == "unknown":
			client = scope.get("client")
			if client:
				client_ip = client[0]

		# Check rate limit (fail-open: any error lets request through)
		try:
			limiter = self._get_limiter(scope["app"].state)
			key = global_ratelimit_key(client_ip)
			allowed, count, ttl = await limiter.check(key, self.limit, self.window)
		except Exception:
			if self.fail_open:
				logger.warning("rate_limit_redis_unavailable", path=path, ip=client_ip, fail_open=True)
				await self.app(scope, receive, send)
				return
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
				await response(scope, receive, send)
				return

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
			await response(scope, receive, send)
			return

		# Request allowed — pass through and inject rate-limit headers into response
		remaining = max(0, self.limit - count)
		reset_time = int(time.time()) + ttl if ttl > 0 else int(time.time()) + self.window
		limit_str = str(self.limit)
		remaining_str = str(remaining)
		reset_str = str(reset_time)

		async def send_with_headers(message):
			if message["type"] == "http.response.start":
				headers = MutableHeaders(scope=message)
				headers.append("X-RateLimit-Limit", limit_str)
				headers.append("X-RateLimit-Remaining", remaining_str)
				headers.append("X-RateLimit-Reset", reset_str)
			await send(message)

		await self.app(scope, receive, send_with_headers)
