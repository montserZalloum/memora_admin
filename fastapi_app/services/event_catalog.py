"""Event catalog service with process-local cache and per-player filtering."""

import asyncio
import time

import redis.asyncio as redis
import structlog

from fastapi_app.models.event_catalog import CatalogEvent
from fastapi_app.services.event_access import EventAccessService
from fastapi_app.services.frappe_client import FrappeClient
from fastapi_app.services.premium import PremiumService

logger = structlog.get_logger(__name__)

# Process-local cache keyed by plan_id.
# Short TTL — event list changes frequently with status transitions.
_local_event_cache: dict[str, tuple[list[CatalogEvent], float]] = {}
_LOCAL_TTL = 60  # 60 seconds
_MAX_CACHE_ENTRIES = 200


class EventCatalogService:
	"""Fetch paid upcoming events and filter out already-accessible ones."""

	def __init__(
		self,
		redis_client: redis.Redis,
		frappe_client: FrappeClient,
		premium_service: PremiumService,
		event_access_service: EventAccessService,
	):
		self.redis = redis_client
		self.frappe = frappe_client
		self.premium = premium_service
		self.event_access = event_access_service

	async def get_player_event_catalog(
		self,
		plan_id: str,
		player_id: str,
	) -> list[CatalogEvent]:
		"""Get purchasable paid events for a player.

		1. Premium users get empty list (they join paid events for free)
		2. Fetch paid upcoming events for this plan (cached 60s)
		3. Exclude events where player already has access
		"""
		# Step 1: Premium check
		premium_state = await self.premium.is_plan_premium_usable(player_id, plan_id)
		if premium_state.usable:
			logger.debug("event_catalog_premium_skip", player_id=player_id, plan_id=plan_id)
			return []

		# Step 2: Fetch events (process-local cache)
		events = await self._get_plan_events(plan_id)
		if not events:
			return []

		# Step 3: Filter out events player already has access to
		access_checks = await asyncio.gather(
			*(self.event_access.has_active_access(player_id, ev.event_id) for ev in events)
		)

		result = [
			ev for ev, access_state in zip(events, access_checks)
			if not access_state.has_access
		]

		logger.debug(
			"event_catalog_filtered",
			plan_id=plan_id,
			player_id=player_id,
			total=len(events),
			visible=len(result),
		)
		return result

	async def _get_plan_events(self, plan_id: str) -> list[CatalogEvent]:
		"""Get paid upcoming events for a plan. Process-local cached (60s TTL)."""
		entry = _local_event_cache.get(plan_id)
		if entry is not None:
			events, expiry = entry
			if time.monotonic() < expiry:
				return events

		# Cache miss — fetch from Frappe
		result = await self.frappe.call(
			"memora_admin.memora_admin.api.event_catalog.get_paid_events_for_plan",
			{"plan_id": plan_id},
		)

		if not result:
			if len(_local_event_cache) >= _MAX_CACHE_ENTRIES:
				_local_event_cache.clear()
			_local_event_cache[plan_id] = ([], time.monotonic() + _LOCAL_TTL)
			return []

		events = [
			CatalogEvent(
				event_id=ev["name"],
				event_name=ev["event_name"],
				description=ev.get("description"),
				scheduled_start=ev["scheduled_start"],
				price=ev.get("price", 0.0),
				currency=ev.get("currency", ""),
			)
			for ev in result
		]

		if len(_local_event_cache) >= _MAX_CACHE_ENTRIES:
			_local_event_cache.clear()
		_local_event_cache[plan_id] = (events, time.monotonic() + _LOCAL_TTL)
		logger.info("event_catalog_cached", plan_id=plan_id, event_count=len(events))
		return events
