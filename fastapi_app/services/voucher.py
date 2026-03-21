"""Voucher service: HMAC computation, failed-attempt rate limiting, Frappe delegation."""

import hashlib
import hmac as hmac_module

import redis.asyncio as redis
import structlog

from fastapi_app.core.redis_keys import voucher_fail_ip_key, voucher_fail_player_key
from fastapi_app.services.frappe_client import FrappeAPIError, FrappeClient

logger = structlog.get_logger(__name__)

# ---------------------------------------------------------------------------
# Lua scripts for atomic rate-limit operations
# ---------------------------------------------------------------------------

# Check if count >= limit; return TTL if exceeded, else 0
CHECK_LIMIT_SCRIPT = """
local count = redis.call("GET", KEYS[1])
if count and tonumber(count) >= tonumber(ARGV[1]) then
    local ttl = redis.call("TTL", KEYS[1])
    return ttl
end
return 0
"""

# Atomic increment with conditional TTL set on first increment
INCREMENT_SCRIPT = """
local count = redis.call("INCR", KEYS[1])
if count == 1 then
    redis.call("EXPIRE", KEYS[1], ARGV[1])
end
return count
"""

# ---------------------------------------------------------------------------
# Error codes that count as failures (increment rate-limit counters)
# ---------------------------------------------------------------------------

FAILURE_ERRORS = {
	"INVALID_PIN",
	"NOT_ALLOCATED",
	"ALREADY_REDEEMED",
	"EXPIRED",
	"VOID",
	"BATCH_INACTIVE",
	"SEASON_INACTIVE",
	"ALL_GRANTS_OWNED",
	"GRANT_NOT_IN_BATCH",
	"ALREADY_OWNED",
	"PLAN_NOT_ELIGIBLE",
	"ALREADY_HAS_PREMIUM",
}


class VoucherService:
	"""Compute HMAC, enforce failed-attempt rate limits, delegate to Frappe."""

	PLAYER_LIMIT = 5  # max failed attempts per player per hour
	IP_LIMIT = 20  # max failed attempts per IP per hour
	WINDOW_SECONDS = 3600  # 1 hour

	def __init__(
		self,
		redis_client: redis.Redis,
		frappe_client: FrappeClient,
		hmac_secret: str,
	):
		if not hmac_secret:
			raise ValueError(
				"VOUCHER_HMAC_SECRET is not configured in .env. "
				"It must match voucher_hmac_secret in Frappe site_config.json. "
				"Without it, PIN lookups will always fail with INVALID_PIN."
			)
		self.redis = redis_client
		self.frappe = frappe_client
		self.hmac_secret = hmac_secret
		self._check_script = None
		self._incr_script = None

	# ------------------------------------------------------------------
	# HMAC
	# ------------------------------------------------------------------

	def _compute_hmac(self, pin: str) -> str:
		"""Compute HMAC-SHA256 of the plaintext PIN (matches Frappe's compute_hmac)."""
		return hmac_module.new(
			self.hmac_secret.encode("utf-8"),
			pin.encode("utf-8"),
			hashlib.sha256,
		).hexdigest()

	# ------------------------------------------------------------------
	# Rate limiting (failed attempts only)
	# ------------------------------------------------------------------

	async def check_rate_limit(self, player_id: str, ip: str) -> int | None:
		"""Check if player or IP is rate-limited.

		Returns:
			retry_after seconds (int > 0) if limited, else None.
		"""
		if self._check_script is None:
			self._check_script = self.redis.register_script(CHECK_LIMIT_SCRIPT)

		player_key = voucher_fail_player_key(player_id)
		retry = await self._check_script(keys=[player_key], args=[self.PLAYER_LIMIT])
		if retry and int(retry) > 0:
			return int(retry)

		ip_key = voucher_fail_ip_key(ip)
		retry = await self._check_script(keys=[ip_key], args=[self.IP_LIMIT])
		if retry and int(retry) > 0:
			return int(retry)

		return None

	async def record_failure(self, player_id: str, ip: str) -> None:
		"""Increment failure counters for player and IP (called only on failed attempts)."""
		if self._incr_script is None:
			self._incr_script = self.redis.register_script(INCREMENT_SCRIPT)

		player_key = voucher_fail_player_key(player_id)
		ip_key = voucher_fail_ip_key(ip)

		await self._incr_script(keys=[player_key], args=[self.WINDOW_SECONDS])
		await self._incr_script(keys=[ip_key], args=[self.WINDOW_SECONDS])

	# ------------------------------------------------------------------
	# Frappe delegation
	# ------------------------------------------------------------------

	async def preview(self, pin: str, player_id: str) -> dict:
		"""Preview voucher: compute HMAC and delegate to Frappe preview_voucher.

		Returns:
			dict with face_value + grants on success, or {"error": "CODE"} on failure.
		"""
		pin_hmac = self._compute_hmac(pin)
		try:
			result = await self.frappe.call(
				"memora_admin.memora_admin.api.voucher.preview_voucher",
				{"pin_hmac": pin_hmac, "player_id": player_id},
			)
			return result if isinstance(result, dict) else {"error": "UNEXPECTED_RESPONSE"}
		except FrappeAPIError as e:
			logger.error(
				"voucher_preview_frappe_error",
				player_id=player_id,
				status_code=e.status_code,
				message=e.message,
			)
			return {"error": "SERVICE_ERROR"}

	async def redeem(self, pin: str, player_id: str, grant_id: str | None, ip: str) -> dict:
		"""Redeem voucher: compute HMAC and delegate to Frappe redeem_voucher.

		Returns:
			dict with status + transaction_id on success, or {"error": "CODE"} on failure.
		"""
		pin_hmac = self._compute_hmac(pin)
		try:
			result = await self.frappe.call(
				"memora_admin.memora_admin.api.voucher.redeem_voucher",
				{
					"pin_hmac": pin_hmac,
					"player_id": player_id,
					"product_grant_id": grant_id or "",
					"ip_address": ip,
				},
			)
			return result if isinstance(result, dict) else {"error": "UNEXPECTED_RESPONSE"}
		except FrappeAPIError as e:
			logger.error(
				"voucher_redeem_frappe_error",
				player_id=player_id,
				grant_id=grant_id,
				status_code=e.status_code,
				message=e.message,
			)
			return {"error": "SERVICE_ERROR"}
