"""Profile page aggregation service.

Composes existing services into profile-page-shaped responses.
Does NOT duplicate business logic from underlying services.
"""

import asyncio
import json
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import redis.asyncio as redis
import structlog

from fastapi_app.core.constants import MASTERY_CACHE_TTL
from fastapi_app.core.level_config import calculate_level, get_level_config, get_threshold
from fastapi_app.core.redis_keys import (
	daily_xp_key as _daily_xp_key_fn,
)
from fastapi_app.core.redis_keys import (
	items_learned_key as _items_learned_key_fn,
)
from fastapi_app.core.redis_keys import (
	lb_archive_daily_key,
	lb_daily_key,
)
from fastapi_app.core.redis_keys import (
	mastery_key as _mastery_key_fn,
)
from fastapi_app.core.redis_keys import (
	plan_season_seq_key as _plan_season_seq_key_fn,
)
from fastapi_app.core.redis_keys import (
	player_plan_key as _player_plan_key_fn,
)
from fastapi_app.core.redis_keys import (
	profile_key as _profile_key_fn,
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
	):
		self.redis = redis_client
		self.frappe = frappe_client

	async def _resolve_season_seq(self, player_id: str) -> int | None:
		"""Resolve season_seq for a player via two-cache lookup.

		Cache 1: memora:player_plan:{player_id} → plan_id
		Cache 2: memora:plan_season_seq:{plan_id} → season_seq

		On cache miss, fetches from Frappe and caches for 24h.
		Returns None if player has no plan (lets Frappe fallback to default).
		"""
		# Step 1: player → plan
		plan_key = _player_plan_key_fn(player_id)
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
		seq_key = _plan_season_seq_key_fn(plan_id)
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
		profile_service = ProfileService(self.redis, self.frappe)

		# Parallel: all three are independent reads (3 RTT → 1)
		wallet, profiles, config = await asyncio.gather(
			wallet_service.get_wallet(player_id),
			profile_service.get_profiles_batch([player_id]),
			get_level_config(self.redis),
		)
		total_xp = wallet.get("xp", 0)
		profile = profiles.get(player_id)
		display_name = profile.display_name if profile else "Anonymous"
		avatar = profile.avatar if profile else "default_avatar"
		level, level_title, xp_in_level, xp_for_next_level = calculate_level(total_xp, config)

		# XP boundaries for the current level
		xp_level_start = get_threshold(level, config.a, config.b)
		xp_level_end = get_threshold(level + 1, config.a, config.b) if level < config.max_level else 0

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
		# Parallel: wallet + items_learned cache check are independent (2 RTT → 1)
		wallet_service = WalletService(self.redis, frappe_client=self.frappe)
		items_cache_key = _items_learned_key_fn(player_id, subject_id)

		wallet, cached_items = await asyncio.gather(
			wallet_service.get_wallet(player_id),
			self.redis.get(items_cache_key),
		)
		streak = wallet.get("streak", 0)

		# Total XP (always from wallet — alltime ZSETs removed)
		total_xp = wallet.get("xp", 0)

		# Items learned: count of Memory State records (SRS items encountered)
		# Cached in Redis with 5-min TTL. On cache miss, fetches from Frappe API.
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

	async def get_weekly_activity(self, player_id: str) -> dict:
		"""Get weekly activity: XP per day for the last 7 days ending today.

		Uses Redis pipeline with 7 ZSCORE calls (single round-trip).

		Args:
			player_id: Player's user ID.

		Returns:
			Dict with week_start, days (list of {date, day_name, xp}), total_xp.
		"""
		# Get today at midnight in Amman timezone
		now = datetime.now(AMMAN_TZ)
		today = now.replace(hour=0, minute=0, second=0, microsecond=0)

		# Calculate the start of the 7-day period (6 days ago)
		week_start = today - timedelta(days=6)

		# Phase 1: fetch 7 primary daily scores in one round-trip.
		today_str = today.strftime("%Y-%m-%d")
		days = []
		pipe = self.redis.pipeline()
		for i in range(7):
			day = week_start + timedelta(days=i)
			date_str = day.strftime("%Y-%m-%d")
			pipe.zscore(lb_daily_key(date_str, None), player_id)
			days.append(
				{
					"date": date_str,
					"day_name": day.strftime("%a"),
				}
			)
		scores = await pipe.execute()

		# Phase 2: only check archive keys for past days still missing from primary.
		archive_indices = []
		for i, score in enumerate(scores):
			if score is None and days[i]["date"] != today_str:
				archive_indices.append(i)

		if archive_indices:
			archive_pipe = self.redis.pipeline()
			for i in archive_indices:
				archive_pipe.zscore(lb_archive_daily_key(days[i]["date"], None), player_id)
			archive_scores = await archive_pipe.execute()
			for j, i in enumerate(archive_indices):
				if archive_scores[j] is not None:
					scores[i] = archive_scores[j]

		# Phase 3: Per-player daily XP summary hash (Redis, MariaDB-backed).
		# Covers past days still missing after Phase 1 + Phase 2 (e.g. after Redis data loss).
		still_missing = [
			i for i, score in enumerate(scores) if score is None and days[i]["date"] != today_str
		]
		if still_missing:
			daily_xp_key = _daily_xp_key_fn(player_id)
			daily_xp_data = await self.redis.hgetall(daily_xp_key)
			# Check if hash is missing ANY of the dates we need (partial population after eviction)
			missing_dates = {days[i]["date"] for i in still_missing}
			hash_has_gaps = not daily_xp_data or not missing_dates.issubset(daily_xp_data.keys())
			if hash_has_gaps:
				# Recover from MariaDB and merge into hash (preserving any existing entries)
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
					# Merge MariaDB data into Redis hash (HSETNX = keep Redis values on conflict)
					pipe3 = self.redis.pipeline()
					for d, v in restored.items():
						pipe3.hsetnx(daily_xp_key, d, v)
					pipe3.expire(daily_xp_key, 8 * 86400)
					await pipe3.execute()
					# Build merged view: MariaDB as base, Redis overwrites
					merged = {str(k): str(v) for k, v in restored.items()}
					for k, v in daily_xp_data.items():
						merged[k if isinstance(k, str) else k.decode()] = v if isinstance(v, str) else str(v)
					daily_xp_data = merged
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
		counter_key = _mastery_key_fn(player_id, subject_id, season_seq)

		# Try Redis HASH first
		data = await self.redis.hgetall(counter_key)
		if data:
			raw_mature = int(data.get(b"mature", data.get("mature", 0)))
			raw_learning = int(data.get(b"learning", data.get("learning", 0)))
			# A negative field means the counter drifted; fall through to the
			# Frappe rebuild instead of trusting (and clamping) corrupt counts.
			if raw_mature >= 0 and raw_learning >= 0:
				logger.debug("mastery_counter_hit", player=player_id, subject=subject_id)
				return {
					"subject": subject_id,
					"mature": raw_mature,
					"learning": raw_learning,
				}
			await self.redis.delete(counter_key)
			logger.warning("mastery_counter_drift", player=player_id, subject=subject_id)

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
		await self.redis.delete(_profile_key_fn(player_id))
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
			device_service = DeviceService(self.redis)
			await device_service.remove_device(player_id, device_id)
			logger.info("logout_with_device_removal", player=player_id, device=device_id)
		else:
			logger.info("logout_session_only", player=player_id)

		return {"success": True, "message": "Logged out successfully"}
