"""Premium catalog service with process-local cache and per-player filtering."""

import time

import structlog

from fastapi_app.models.premium_catalog import PremiumCatalogResponse, PremiumVoucherCatalogResponse
from fastapi_app.services.frappe_client import FrappeClient
from fastapi_app.services.premium import PremiumService

logger = structlog.get_logger(__name__)

# Process-local caches keyed by plan_id.
_local_premium_cache: dict[str, tuple[dict | None, float]] = {}
_local_voucher_cache: dict[str, tuple[dict | None, float]] = {}
_LOCAL_TTL = 300  # 5 minutes
_VOUCHER_TTL = 60  # 60 seconds — voucher availability changes more often
_MAX_CACHE_ENTRIES = 200


class PremiumCatalogService:
	"""Fetch premium pricing for a plan and combine with player state."""

	def __init__(
		self,
		frappe_client: FrappeClient,
		premium_service: PremiumService,
	):
		self.frappe = frappe_client
		self.premium = premium_service

	async def get_player_premium_catalog(
		self,
		plan_id: str,
		player_id: str,
	) -> PremiumCatalogResponse:
		"""Get premium catalog entry for a player's plan.

		1. Fetch pricing info (cached 5 min)
		2. Check if player already has usable premium
		3. Check if player has a pending purchase
		4. Return combined response
		"""
		# Step 1: Get pricing info
		pricing = await self._get_plan_premium_info(plan_id)
		if not pricing:
			return PremiumCatalogResponse(available=False)

		plan_name = pricing.get("plan_name", plan_id)
		price = pricing.get("price", 0)
		currency = pricing.get("currency", "JOD")

		# Step 2: Check existing premium
		premium_state = await self.premium.is_plan_premium_usable(player_id, plan_id)
		if premium_state.usable:
			return PremiumCatalogResponse(
				available=False,
				plan_id=plan_id,
				plan_name=plan_name,
				price=price,
				currency=currency,
				has_premium=True,
			)

		# Step 3: Check pending purchase
		has_pending = await self.premium.has_pending_purchase(player_id, plan_id)
		if has_pending:
			return PremiumCatalogResponse(
				available=False,
				plan_id=plan_id,
				plan_name=plan_name,
				price=price,
				currency=currency,
				has_pending_purchase=True,
			)

		return PremiumCatalogResponse(
			available=True,
			plan_id=plan_id,
			plan_name=plan_name,
			price=price,
			currency=currency,
		)

	async def _get_plan_premium_info(self, plan_id: str) -> dict | None:
		"""Get premium pricing for a plan. Process-local cached (5 min TTL)."""
		entry = _local_premium_cache.get(plan_id)
		if entry is not None:
			info, expiry = entry
			if time.monotonic() < expiry:
				return info

		# Cache miss — fetch from Frappe
		try:
			result = await self.frappe.call(
				"memora_admin.memora_admin.api.premium_catalog.get_premium_info_for_plan",
				{"plan_id": plan_id},
			)
		except Exception:
			logger.warning("premium_catalog_frappe_error", plan_id=plan_id)
			return None

		if len(_local_premium_cache) >= _MAX_CACHE_ENTRIES:
			_local_premium_cache.clear()
		_local_premium_cache[plan_id] = (result, time.monotonic() + _LOCAL_TTL)

		if result:
			logger.debug("premium_catalog_cached", plan_id=plan_id, price=result.get("price"))

		return result

	async def get_premium_voucher_catalog(
		self,
		plan_id: str,
	) -> PremiumVoucherCatalogResponse:
		"""Check if plan_premium voucher cards exist for a plan.

		Plan-level only (no player check) — frontend already knows premium state.
		Cached 60s since voucher availability changes with redemptions.
		"""
		entry = _local_voucher_cache.get(plan_id)
		if entry is not None:
			info, expiry = entry
			if time.monotonic() < expiry:
				return self._build_voucher_response(info)

		try:
			result = await self.frappe.call(
				"memora_admin.memora_admin.api.premium_catalog.get_premium_voucher_for_plan",
				{"plan_id": plan_id},
			)
		except Exception:
			logger.warning("premium_voucher_catalog_frappe_error", plan_id=plan_id)
			return PremiumVoucherCatalogResponse(available=False)

		if len(_local_voucher_cache) >= _MAX_CACHE_ENTRIES:
			_local_voucher_cache.clear()
		_local_voucher_cache[plan_id] = (result, time.monotonic() + _VOUCHER_TTL)
		return self._build_voucher_response(result)

	@staticmethod
	def _build_voucher_response(result: dict | None) -> PremiumVoucherCatalogResponse:
		if not result:
			return PremiumVoucherCatalogResponse(available=False)
		return PremiumVoucherCatalogResponse(
			available=True,
			plan_id=result.get("plan_id"),
			plan_name=result.get("plan_name"),
			face_value=result.get("face_value", "0"),
		)
