"""Announcement service — cache read, hydration, and filtering."""

import json
import time
from datetime import date, datetime

import redis.asyncio as redis
import structlog

from fastapi_app.core.redis_keys import ANNOUNCEMENTS_CACHE_TTL, announcements_active_key
from fastapi_app.models.announcements import AnnouncementItem
from fastapi_app.services.frappe_client import FrappeClient

logger = structlog.get_logger()

# Process-local cache for announcements list (same pattern as _local_hierarchy_cache).
# Single global value — all players see the same announcements before plan/date filtering.
_local_announcements_cache: tuple[list, float] | None = None
_LOCAL_TTL = 300  # 5 minutes; pubsub invalidation clears this early on content change


class AnnouncementService:
	def __init__(self, redis_client: redis.Redis, frappe_client: FrappeClient):
		self.redis = redis_client
		self.frappe = frappe_client

	async def get_active_announcements(self) -> list[dict]:
		"""Get all active announcements from local cache, Redis, or Frappe."""
		global _local_announcements_cache

		# 1. Local in-process cache (sub-microsecond)
		if _local_announcements_cache is not None:
			data, exp = _local_announcements_cache
			if time.monotonic() < exp:
				return data

		# 2. Redis cache
		key = announcements_active_key()
		cached = await self.redis.get(key)
		if cached is not None:
			announcements = json.loads(cached)
			_local_announcements_cache = (announcements, time.monotonic() + _LOCAL_TTL)
			return announcements

		# 3. Cache miss: fetch from Frappe
		result = await self.frappe.call(
			"memora_admin.memora_admin.api.announcements.get_active_announcements",
		)
		announcements = result or []

		await self.redis.set(key, json.dumps(announcements), ex=ANNOUNCEMENTS_CACHE_TTL)
		_local_announcements_cache = (announcements, time.monotonic() + _LOCAL_TTL)
		logger.info("announcements_cache_hydrated", count=len(announcements))
		return announcements

	async def get_for_player(
		self,
		player_plan: str | None,
		lang: str,
	) -> list[AnnouncementItem]:
		"""Get announcements filtered for a specific player.

		Args:
			player_plan: Player's current plan ID (None = only "all" announcements).
			lang: Language code ("ar" or "en").

		Returns:
			Filtered and localized list of AnnouncementItem, newest first.
		"""
		all_announcements = await self.get_active_announcements()
		today_str = date.today().isoformat()
		result = []

		for ann in all_announcements:
			# Date filter
			start = ann.get("effective_start_date")
			end = ann.get("effective_end_date")
			if not start or not end:
				continue
			if today_str < start or today_str > end:
				continue

			# Plan filter
			audience = ann.get("target_audience", "all")
			if audience == "specific_plans":
				if player_plan is None or player_plan not in ann.get("target_plans", []):
					continue

			# Language selection
			title = ann.get(f"title_{lang}") or ann.get("title_ar", "")
			body = ann.get(f"body_{lang}") or ann.get("body_ar", "")

			# Parse created_at
			created_at_str = ann.get("created_at", "")
			try:
				created_at = datetime.fromisoformat(created_at_str)
			except (ValueError, TypeError):
				created_at = datetime.now()

			result.append(
				AnnouncementItem(
					id=ann["id"],
					title=title,
					body=body,
					display_frequency=ann.get("display_frequency", "always"),
					created_at=created_at,
				)
			)

		# Sort by created_at descending (newest first)
		result.sort(key=lambda a: a.created_at, reverse=True)
		return result

	async def invalidate(self) -> None:
		"""Delete cached announcements (called by pubsub handler)."""
		global _local_announcements_cache
		_local_announcements_cache = None
		await self.redis.delete(announcements_active_key())
		logger.info("announcements_cache_invalidated_via_pubsub")
