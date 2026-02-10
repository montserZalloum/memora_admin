"""Profile page aggregation service.

Composes existing services into profile-page-shaped responses.
Does NOT duplicate business logic from underlying services.
"""

import json
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import redis.asyncio as redis
import structlog

from fastapi_app.core.constants import (
	LEVEL_THRESHOLDS,
	MASTERY_CACHE_TTL,
	calculate_level,
)
from fastapi_app.services.frappe_client import FrappeClient
from fastapi_app.services.profile import ProfileService
from fastapi_app.services.wallet import WalletService

logger = structlog.get_logger(__name__)

# Asia/Amman timezone for weekly activity date boundaries
AMMAN_TZ = ZoneInfo("Asia/Amman")


class ProfilePageService:
	"""Aggregation service for profile page endpoints.

	Composes: WalletService (XP, streak), ProfileService (display_name, avatar),
	Redis leaderboard ZSETs (per-subject XP, weekly activity),
	Frappe APIs (mastery, avatar update), StatsService (items learned).
	"""

	def __init__(
		self,
		redis_client: redis.Redis,
		frappe_client: FrappeClient,
		key_prefix: str = "memora:",
	):
		self.redis = redis_client
		self.frappe = frappe_client
		self.prefix = key_prefix

	async def get_hero(self, player_id: str) -> dict:
		"""Get hero section data: avatar, display_name, level, XP progress.

		Args:
			player_id: Player's user ID.

		Returns:
			Dict with display_name, avatar, level, level_title, current_xp,
			xp_in_level, xp_for_next_level, xp_level_start, xp_level_end.
		"""
		wallet_service = WalletService(self.redis, frappe_client=self.frappe)
		wallet = await wallet_service.get_wallet(player_id)
		total_xp = wallet.get("xp", 0)

		profile_service = ProfileService(self.redis, self.frappe)
		profiles = await profile_service.get_profiles_batch([player_id])
		profile = profiles.get(player_id)
		display_name = profile.display_name if profile else "Anonymous"
		avatar = profile.avatar if profile else "default_avatar"

		level, level_title, xp_in_level, xp_for_next_level = calculate_level(total_xp)

		# XP boundaries for the current level
		xp_level_start = LEVEL_THRESHOLDS[level - 1]
		xp_level_end = LEVEL_THRESHOLDS[level] if level < len(LEVEL_THRESHOLDS) else 0

		return {
			"display_name": display_name,
			"avatar": avatar,
			"level": level,
			"level_title": level_title,
			"current_xp": total_xp,
			"xp_in_level": xp_in_level,
			"xp_for_next_level": xp_for_next_level,
			"xp_level_start": xp_level_start,
			"xp_level_end": xp_level_end,
		}

	async def get_stats(self, player_id: str, subject_id: str | None = None) -> dict:
		"""Get stats grid: streak, items_learned, total_xp.

		Streak is always global (from wallet). XP and items_learned support
		optional subject filtering.

		Args:
			player_id: Player's user ID.
			subject_id: Optional subject filter. None = combined across all subjects.

		Returns:
			Dict with subject, streak, items_learned, total_xp.
		"""
		# Streak: always global
		wallet_service = WalletService(self.redis, frappe_client=self.frappe)
		wallet = await wallet_service.get_wallet(player_id)
		streak = wallet.get("streak", 0)

		# Total XP
		if subject_id is None:
			total_xp = wallet.get("xp", 0)
		else:
			score = await self.redis.zscore(f"{self.prefix}lb:alltime:subject:{subject_id}", player_id)
			total_xp = int(score) if score is not None else 0

		# Items learned
		if subject_id is not None:
			raw = await self.redis.hget(f"{self.prefix}stats:{player_id}:{subject_id}:v1", "completed")
			if raw is not None:
				raw_str = raw.decode() if isinstance(raw, bytes) else raw
				items_learned = int(raw_str)
			else:
				items_learned = 0
		else:
			# Sum across all subjects
			keys = await self.redis.keys(f"{self.prefix}stats:{player_id}:*")
			if keys:
				pipe = self.redis.pipeline()
				for key in keys:
					pipe.hget(key, "completed")
				results = await pipe.execute()
				items_learned = 0
				for val in results:
					if val is not None:
						val_str = val.decode() if isinstance(val, bytes) else val
						items_learned += int(val_str)
			else:
				items_learned = 0

		return {
			"subject": subject_id,
			"streak": streak,
			"items_learned": items_learned,
			"total_xp": total_xp,
		}

	async def get_weekly_activity(self, player_id: str, subject_id: str | None = None) -> dict:
		"""Get weekly activity: XP per day for the last 7 days ending today.

		Uses Redis pipeline with 7 ZSCORE calls (single round-trip).

		Args:
			player_id: Player's user ID.
			subject_id: Optional subject filter.

		Returns:
			Dict with subject, week_start, days (list of {date, day_name, xp}), total_xp.
		"""
		# Get today at midnight in Amman timezone
		now = datetime.now(AMMAN_TZ)
		today = now.replace(hour=0, minute=0, second=0, microsecond=0)

		# Calculate the start of the 7-day period (6 days ago)
		week_start = today - timedelta(days=6)

		# Build 7 Redis keys for the last 7 days (ending today)
		days = []
		pipe = self.redis.pipeline()
		for i in range(7):
			day = week_start + timedelta(days=i)
			date_str = day.strftime("%Y-%m-%d")
			if subject_id:
				key = f"{self.prefix}lb:daily:{date_str}:subject:{subject_id}"
			else:
				key = f"{self.prefix}lb:daily:{date_str}"
			pipe.zscore(key, player_id)
			days.append(
				{
					"date": date_str,
					"day_name": day.strftime("%a"),
				}
			)

		# Single round-trip for all 7 ZSCORE calls
		scores = await pipe.execute()

		total_xp = 0
		for i, score in enumerate(scores):
			xp = int(score) if score is not None else 0
			days[i]["xp"] = xp
			total_xp += xp

		return {
			"subject": subject_id,
			"week_start": week_start.strftime("%Y-%m-%d"),
			"days": days,
			"total_xp": total_xp,
		}

	async def get_mastery(self, player_id: str, subject_id: str | None = None) -> dict:
		"""Get memory mastery breakdown: mature/learning/new counts.

		Cached in Redis with 5-min TTL. On cache miss, fetches from Frappe API.

		Args:
			player_id: Player's user ID.
			subject_id: Optional subject filter.

		Returns:
			Dict with subject, mature, learning, new_items, total.
		"""
		cache_key = f"{self.prefix}mastery:{player_id}:{subject_id or 'all'}"

		# Check cache first
		cached = await self.redis.get(cache_key)
		if cached:
			data = cached.decode() if isinstance(cached, bytes) else cached
			logger.debug("mastery_cache_hit", player=player_id, subject=subject_id)
			return json.loads(data)

		logger.info("mastery_cache_miss", player=player_id, subject=subject_id)

		# Fetch from Frappe API
		result = await self.frappe.call(
			"memora_admin.api.profile.get_memory_mastery",
			{"player_id": player_id, "subject_id": subject_id},
		)

		mastery = {
			"subject": subject_id,
			"mature": result.get("mature", 0) if result else 0,
			"learning": result.get("learning", 0) if result else 0,
			"new_items": result.get("new_items", 0) if result else 0,
			"total": result.get("total", 0) if result else 0,
		}

		# Cache with TTL
		await self.redis.set(cache_key, json.dumps(mastery), ex=MASTERY_CACHE_TTL)

		return mastery

	async def update_avatar(self, player_id: str, avatar: str) -> dict:
		"""Update player avatar via Frappe API and invalidate profile cache.

		Args:
			player_id: Player's user ID.
			avatar: Avatar identifier string.

		Returns:
			Dict with avatar and success status.

		Raises:
			FrappeAPIError: If avatar is invalid or profile not found.
		"""
		result = await self.frappe.call(
			"memora_admin.api.profile.update_player_avatar",
			{"player_id": player_id, "avatar": avatar},
		)

		# Invalidate profile cache so next fetch picks up new avatar
		await self.redis.delete(f"{self.prefix}profile:{player_id}")
		logger.info("avatar_updated", player=player_id, avatar=avatar)

		return result if isinstance(result, dict) else {"avatar": avatar, "success": True}

	async def logout(self, player_id: str, device_id: str | None = None) -> dict:
		"""Invalidate session and optionally remove device.

		Args:
			player_id: Player's user ID.
			device_id: Optional device UUID to remove (frees device slot).

		Returns:
			Dict with success and message.
		"""
		from fastapi_app.services.device import DeviceService
		from fastapi_app.services.session import SessionService

		session_service = SessionService(self.redis)
		await session_service.invalidate_session(player_id)

		if device_id:
			device_service = DeviceService(self.redis, key_prefix=self.prefix)
			await device_service.remove_device(player_id, device_id)
			logger.info("logout_with_device_removal", player=player_id, device=device_id)
		else:
			logger.info("logout_session_only", player=player_id)

		return {"success": True, "message": "Logged out successfully"}
