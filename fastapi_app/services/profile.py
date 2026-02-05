"""Profile caching service for batch profile lookups.

Per CONTEXT.md (Phase 14):
- Batch fetch profiles for leaderboard enrichment
- Redis caching with 1-hour TTL
- Pipeline MGET for single round-trip batch operations
- Fallback to "Anonymous XXXX" for missing profiles
"""

import json
from typing import Optional

import redis.asyncio as redis
import structlog

from fastapi_app.models.profile import PlayerProfile
from fastapi_app.services.frappe_client import FrappeClient

logger = structlog.get_logger(__name__)


class ProfileService:
	"""Cache and batch-fetch player profiles for leaderboard enrichment.

	Per RESEARCH.md:
	- Cache with 1 hour TTL
	- Pipeline MGET for batch operations (no N+1 queries)
	- Frappe API batch fetch on cache miss
	- Graceful fallback for missing profiles
	"""

	CACHE_TTL = 3600  # 1 hour per success criteria
	MAX_FRAPPE_BATCH = 50  # Limit batch to avoid timeouts (per RESEARCH.md)

	def __init__(
		self,
		redis_client: redis.Redis,
		frappe_client: FrappeClient,
		key_prefix: str = "memora:",
	):
		self.redis = redis_client
		self.frappe = frappe_client
		self.prefix = key_prefix

	def _cache_key(self, player_id: str) -> str:
		"""Generate Redis key for profile cache."""
		return f"{self.prefix}profile:{player_id}"

	def _apply_fallback(self, player_id: str) -> PlayerProfile:
		"""Generate fallback profile for missing data.

		Per CONTEXT.md: "Anonymous {last 4 digits of player_id}" format.
		"""
		last_four = player_id[-4:] if len(player_id) >= 4 else player_id
		return PlayerProfile(
			player_id=player_id,
			display_name=f"Anonymous {last_four}",
			avatar="default_avatar",
		)

	async def get_profiles_batch(
		self, player_ids: list[str]
	) -> dict[str, PlayerProfile]:
		"""Batch fetch profiles using Redis pipeline.

		Per RESEARCH.md:
		- Pipeline MGET batches reduce RTT from N to 1
		- Target: <25ms for 100 entries (success criteria)
		- Cache misses trigger Frappe batch fetch
		- Still-missing profiles get fallback

		Args:
			player_ids: List of player IDs to fetch profiles for

		Returns:
			Dict mapping player_id to PlayerProfile (all requested IDs included)
		"""
		if not player_ids:
			return {}

		# Build cache keys
		keys = [self._cache_key(pid) for pid in player_ids]

		# Pipeline MGET - single round-trip for all keys
		pipe = self.redis.pipeline()
		pipe.mget(keys)
		results = await pipe.execute()

		# results[0] contains the MGET response (list of values or None)
		cached_values = results[0] if results else []

		profiles: dict[str, PlayerProfile] = {}
		cache_misses: list[str] = []

		for pid, data in zip(player_ids, cached_values):
			if data:
				# Handle bytes response
				data_str = data.decode() if isinstance(data, bytes) else data
				try:
					profiles[pid] = PlayerProfile.model_validate_json(data_str)
				except Exception as e:
					logger.warning("profile_parse_error", player_id=pid, error=str(e))
					cache_misses.append(pid)
			else:
				cache_misses.append(pid)

		if cache_misses:
			logger.debug(
				"profile_cache_misses",
				count=len(cache_misses),
				total=len(player_ids),
			)
			# Fetch from Frappe
			fetched = await self._fetch_from_frappe_batch(cache_misses)
			profiles.update(fetched)

			# Apply fallback for any still-missing profiles
			for pid in cache_misses:
				if pid not in profiles:
					profiles[pid] = self._apply_fallback(pid)
					logger.debug("profile_fallback_applied", player_id=pid)

		return profiles

	async def _fetch_from_frappe_batch(
		self, player_ids: list[str]
	) -> dict[str, PlayerProfile]:
		"""Fetch profiles from Frappe API and cache them.

		Per RESEARCH.md:
		- Limit batch to 50 profiles to avoid timeouts
		- Cache each fetched profile with TTL

		Args:
			player_ids: List of player IDs to fetch

		Returns:
			Dict mapping player_id to PlayerProfile for successfully fetched profiles
		"""
		if not player_ids:
			return {}

		# Limit batch size per RESEARCH.md pitfall
		batch = player_ids[: self.MAX_FRAPPE_BATCH]
		if len(player_ids) > self.MAX_FRAPPE_BATCH:
			logger.warning(
				"profile_batch_truncated",
				requested=len(player_ids),
				fetched=self.MAX_FRAPPE_BATCH,
			)

		try:
			result = await self.frappe.call(
				"memora_admin.api.profile.get_profiles_batch",
				{"player_ids": batch},
			)
		except Exception as e:
			logger.error("profile_frappe_fetch_error", error=str(e))
			return {}

		if not result:
			return {}

		profiles: dict[str, PlayerProfile] = {}
		cache_pipe = self.redis.pipeline()

		for item in result:
			try:
				# Handle empty display_name as missing (per CONTEXT.md)
				display_name = item.get("display_name") or ""
				avatar = item.get("avatar") or "default_avatar"
				player_id = item.get("player_id", "")

				if not player_id:
					continue

				# Apply fallback if display_name is empty
				if not display_name:
					profile = self._apply_fallback(player_id)
				else:
					profile = PlayerProfile(
						player_id=player_id,
						display_name=display_name,
						avatar=avatar,
					)

				profiles[player_id] = profile

				# Cache with TTL
				key = self._cache_key(player_id)
				cache_pipe.set(key, profile.model_dump_json(), ex=self.CACHE_TTL)

			except Exception as e:
				logger.warning(
					"profile_parse_error_from_frappe",
					item=item,
					error=str(e),
				)

		# Execute cache writes
		if profiles:
			await cache_pipe.execute()
			logger.debug("profiles_cached", count=len(profiles))

		return profiles

	async def set_profile(
		self,
		player_id: str,
		display_name: str,
		avatar: str,
	) -> None:
		"""Set profile in cache with TTL.

		Used by cache push from Frappe hook (doc_events).

		Args:
			player_id: Player identifier
			display_name: Display name to cache
			avatar: Avatar identifier to cache
		"""
		profile = PlayerProfile(
			player_id=player_id,
			display_name=display_name,
			avatar=avatar,
		)

		key = self._cache_key(player_id)
		await self.redis.set(key, profile.model_dump_json(), ex=self.CACHE_TTL)
		logger.debug("profile_set", player_id=player_id)

	async def invalidate(self, player_id: str) -> None:
		"""Invalidate profile cache for a player.

		Called by pub/sub handler when profile is updated.

		Args:
			player_id: Player identifier to invalidate
		"""
		key = self._cache_key(player_id)
		await self.redis.delete(key)
		logger.debug("profile_invalidated", player_id=player_id)
