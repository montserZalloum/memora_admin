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

# TTL for player_plan and plan_season_seq caches (24 hours)
SEASON_SEQ_CACHE_TTL = 86400


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

	async def _resolve_season_seq(self, player_id: str) -> int | None:
		"""Resolve season_seq for a player via two-cache lookup.

		Cache 1: memora:player_plan:{player_id} → plan_id
		Cache 2: memora:plan_season_seq:{plan_id} → season_seq

		On cache miss, fetches from Frappe and caches for 24h.
		Returns None if player has no plan (lets Frappe fallback to default).
		"""
		# Step 1: player → plan
		plan_key = f"{self.prefix}player_plan:{player_id}"
		plan_id = await self.redis.get(plan_key)
		if plan_id is not None:
			plan_id = plan_id.decode() if isinstance(plan_id, bytes) else plan_id
		else:
			result = await self.frappe.call(
				"memora_admin.api.profile.get_player_plan",
				{"player_id": player_id},
			)
			plan_id = result.get("plan") if result else None
			if plan_id:
				await self.redis.set(plan_key, plan_id, ex=SEASON_SEQ_CACHE_TTL)
			else:
				logger.debug("player_has_no_plan", player=player_id)
				return None

		# Step 2: plan → season_seq
		seq_key = f"{self.prefix}plan_season_seq:{plan_id}"
		season_seq = await self.redis.get(seq_key)
		if season_seq is not None:
			season_seq = season_seq.decode() if isinstance(season_seq, bytes) else season_seq
			return int(season_seq)
		else:
			result = await self.frappe.call(
				"memora_admin.api.profile.get_plan_season_seq",
				{"plan_id": plan_id},
			)
			seq = result.get("season_seq", 1) if result else 1
			await self.redis.set(seq_key, str(seq), ex=SEASON_SEQ_CACHE_TTL)
			return int(seq)

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

		# Items learned: count of Memory State records (SRS items encountered)
		# Cached in Redis with 5-min TTL. On cache miss, fetches from Frappe API.
		items_cache_key = f"{self.prefix}items_learned:{player_id}:{subject_id or 'all'}"
		cached_items = await self.redis.get(items_cache_key)
		if cached_items is not None:
			items_str = cached_items.decode() if isinstance(cached_items, bytes) else cached_items
			items_learned = int(items_str)
		else:
			# Pre-resolve season_seq to avoid 3-table JOIN inside Frappe
			season_seq = await self._resolve_season_seq(player_id)
			params = {"player_id": player_id, "subject_id": subject_id}
			if season_seq is not None:
				params["season_seq"] = season_seq
			result = await self.frappe.call(
				"memora_admin.api.profile.get_items_learned_count",
				params,
			)
			items_learned = result.get("items_learned", 0) if result else 0
			await self.redis.set(items_cache_key, str(items_learned), ex=MASTERY_CACHE_TTL)

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

		# Phase 2: Check archive keys for past days that returned None.
		# The archive task (leaderboard_reset.py) copies yesterday's data to
		# memora:lb:archive:daily:{date} at 00:10 AM daily. After Redis data loss,
		# today's key is recreated on lesson completion, but past days are lost.
		today_str = today.strftime("%Y-%m-%d")
		archive_indices = []
		for i, score in enumerate(scores):
			if score is None and days[i]["date"] != today_str:
				archive_indices.append(i)

		if archive_indices:
			archive_pipe = self.redis.pipeline()
			for i in archive_indices:
				date_str = days[i]["date"]
				if subject_id:
					archive_key = f"{self.prefix}lb:archive:daily:{date_str}:subject:{subject_id}"
				else:
					archive_key = f"{self.prefix}lb:archive:daily:{date_str}"
				archive_pipe.zscore(archive_key, player_id)
			archive_scores = await archive_pipe.execute()
			for j, i in enumerate(archive_indices):
				if archive_scores[j] is not None:
					scores[i] = archive_scores[j]

		# Phase 3: Per-player daily XP summary hash (Redis, MariaDB-backed).
		# Covers days still missing after Phase 1 + Phase 2 (e.g. after Redis data loss).
		still_missing = [i for i in archive_indices if scores[i] is None]
		if still_missing:
			daily_xp_key = f"{self.prefix}daily_xp:{player_id}"
			daily_xp_data = await self.redis.hgetall(daily_xp_key)
			if not daily_xp_data:
				# Cache miss — recover from MariaDB via Frappe
				try:
					frappe_result = await self.frappe.call(
						"memora_admin.api.profile.get_player_daily_xp_json",
						{"player_id": player_id},
					)
					raw = frappe_result.get("daily_xp_json", "{}") if frappe_result else "{}"
				except Exception:
					raw = "{}"
				try:
					restored = json.loads(raw)
				except (json.JSONDecodeError, TypeError):
					restored = {}
				if restored:
					# Repopulate hash in Redis with 8-day TTL
					pipe3 = self.redis.pipeline()
					for d, v in restored.items():
						pipe3.hset(daily_xp_key, d, v)
					pipe3.expire(daily_xp_key, 8 * 86400)
					await pipe3.execute()
					daily_xp_data = {
						(k.decode() if isinstance(k, bytes) else k): str(v)
						for k, v in restored.items()
					}
			for i in still_missing:
				key = days[i]["date"]
				val = daily_xp_data.get(key)
				if val is not None:
					scores[i] = float(val)

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
		"""Get memory mastery breakdown: mature/learning counts.

		Reads from Redis HASH counters directly (no Frappe round-trip on warm cache).
		On cache miss, calls Frappe API which populates the counters as a side effect.

		Args:
			player_id: Player's user ID.
			subject_id: Optional subject filter.

		Returns:
			Dict with subject, mature, learning.
		"""
		# Resolve season_seq (needed for counter key)
		season_seq = await self._resolve_season_seq(player_id)
		if season_seq is None:
			season_seq = 1

		# Build counter key
		if subject_id:
			counter_key = f"{self.prefix}mastery:{player_id}:{subject_id}:s{season_seq}"
		else:
			counter_key = f"{self.prefix}mastery:{player_id}:all:s{season_seq}"

		# Try Redis HASH first
		data = await self.redis.hgetall(counter_key)
		if data:
			mature = max(0, int(data.get(b"mature", data.get("mature", 0))))
			learning = max(0, int(data.get(b"learning", data.get("learning", 0))))
			logger.debug("mastery_counter_hit", player=player_id, subject=subject_id)
			return {
				"subject": subject_id,
				"mature": mature,
				"learning": learning,
			}

		# Cache miss: call Frappe API (which populates the counters as side effect)
		logger.info("mastery_counter_miss", player=player_id, subject=subject_id)
		params = {"player_id": player_id, "subject_id": subject_id, "season_seq": season_seq}
		result = await self.frappe.call(
			"memora_admin.api.profile.get_memory_mastery",
			params,
		)

		return {
			"subject": subject_id,
			"mature": result.get("mature", 0) if result else 0,
			"learning": result.get("learning", 0) if result else 0,
		}

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
