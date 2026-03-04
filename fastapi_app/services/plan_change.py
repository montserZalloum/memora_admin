"""PlanChangeService — orchestrates plan change with freeze, cleanup, and Frappe API call."""

import time

import redis.asyncio as redis
import structlog

from fastapi_app.core.redis_keys import (
	FREEZE_KEY_TTL,
	LB_PREFIX,
	PLAN_CHANGE_COOLDOWN_TTL,
	access_key,
	cache_invalidation_channel,
	daily_xp_key,
	dirty_progress_key,
	dirty_wallets_key,
	freeze_key,
	game_session_key,
	pending_key,
	plan_change_ts_key,
	player_fsrs_pattern,
	player_fsrs_processed_pattern,
	player_items_learned_pattern,
	player_mastery_pattern,
	player_plan_key,
	player_progress_pattern,
	player_stats_pattern,
	practice_session_key,
	profile_key,
	reviews_overview_key,
	session_key,
	wallet_key,
)
from fastapi_app.services.frappe_client import FrappeClient

logger = structlog.get_logger()


class PlanChangeError:
	"""Error container for plan change failures."""

	def __init__(self, code: str, message: str, retry_after: str | None = None):
		self.code = code
		self.message = message
		self.retry_after = retry_after


class PlanChangeResult:
	"""Success container for plan change results."""

	def __init__(self, history_id: str, previous_plan: str, new_plan: str, trigger_reason: str):
		self.history_id = history_id
		self.previous_plan = previous_plan
		self.new_plan = new_plan
		self.trigger_reason = trigger_reason


class PlanChangeService:
	def __init__(self, redis_client: redis.Redis, frappe_client: FrappeClient) -> None:
		self.redis = redis_client
		self.frappe = frappe_client

	async def execute(
		self, player_id: str, new_plan_id: str, current_plan_id: str
	) -> PlanChangeResult | PlanChangeError:
		"""Execute a complete plan change flow.

		Steps:
		1. Check cooldown (fast Redis check)
		2. Acquire freeze lock (SET NX EX 30)
		3. Pre-cleanup (remove from dirty sets, delete game session)
		4. Call Frappe API (atomic DB transaction)
		5. Post-cleanup (DEL direct keys + SCAN patterns + ZREM leaderboards)
		6. Set cooldown timestamp
		7. Publish cache invalidation
		8. Release freeze

		Returns PlanChangeResult on success, PlanChangeError on failure.
		"""
		# 1. Fast cooldown check
		cooldown_err = await self._check_cooldown(player_id)
		if cooldown_err:
			return cooldown_err

		# 2. Acquire freeze
		acquired = await self._acquire_freeze(player_id)
		if not acquired:
			return PlanChangeError(
				code="PLAN_CHANGE_IN_PROGRESS",
				message="A plan change is already in progress. Please try again.",
			)

		try:
			# 3. Pre-cleanup
			await self._pre_cleanup(player_id)

			# 4. Call Frappe API
			result = await self._call_frappe_api(player_id, new_plan_id)
			if isinstance(result, PlanChangeError):
				return result

			# 5. Post-cleanup (non-fatal per FR-022)
			await self._post_cleanup(player_id)

			# 6. Set cooldown
			await self._set_cooldown(player_id)

			# 7. Publish invalidation
			await self._publish_invalidation(player_id)

			return result

		finally:
			# 8. Always release freeze
			await self._release_freeze(player_id)

	async def _check_cooldown(self, player_id: str) -> PlanChangeError | None:
		"""Fast Redis cooldown check. Returns error if cooldown active."""
		try:
			ts = await self.redis.get(plan_change_ts_key(player_id))
			if ts is not None:
				last_change = float(ts)
				elapsed = time.time() - last_change
				if elapsed < PLAN_CHANGE_COOLDOWN_TTL:
					retry_at = last_change + PLAN_CHANGE_COOLDOWN_TTL
					from datetime import datetime, timezone

					retry_after = datetime.fromtimestamp(retry_at, tz=timezone.utc).isoformat()
					return PlanChangeError(
						code="COOLDOWN_ACTIVE",
						message="You can change your plan again after the cooldown period.",
						retry_after=retry_after,
					)
		except Exception as e:
			# Redis failure — fall through to Frappe API validation as safety net
			logger.warning("cooldown_check_redis_error", error=str(e), player_id=player_id)
		return None

	async def _acquire_freeze(self, player_id: str) -> bool:
		"""Acquire per-player freeze lock via SET NX EX. Returns True if acquired."""
		key = freeze_key(player_id)
		acquired = await self.redis.set(key, str(time.time()), nx=True, ex=FREEZE_KEY_TTL)
		return bool(acquired)

	async def _pre_cleanup(self, player_id: str) -> None:
		"""Pre-Frappe cleanup: remove from dirty sets and delete game session."""
		try:
			# Delete game session
			await self.redis.delete(game_session_key(player_id))

			# SREM from dirty wallets
			await self.redis.srem(dirty_wallets_key(), player_id)

			# Derive progress entries from SCAN and SREM from dirty progress
			progress_pattern = player_progress_pattern(player_id)
			cursor = 0
			dirty_members = []
			while True:
				cursor, keys = await self.redis.scan(cursor, match=progress_pattern, count=200)
				for key in keys:
					# Extract {subject}:v{version} from memora:progress:{player_id}:{subject}:v{version}
					# Key format: memora:progress:PLAYER-00001:SUBJ-001:v1
					suffix = key.split(f"{player_id}:", 1)[-1] if f"{player_id}:" in key else ""
					if suffix:
						dirty_members.append(f"{player_id}:{suffix}")
				if cursor == 0:
					break

			if dirty_members:
				pipe = self.redis.pipeline()
				for member in dirty_members:
					pipe.srem(dirty_progress_key(), member)
				await pipe.execute()

		except Exception as e:
			logger.warning("pre_cleanup_error", error=str(e), player_id=player_id)

	async def _call_frappe_api(self, player_id: str, new_plan_id: str) -> PlanChangeResult | PlanChangeError:
		"""Call Frappe whitelisted API for atomic DB operations."""
		result = await self.frappe.call(
			"memora_admin.api.plan_change.execute_plan_change",
			params={"player_id": player_id, "new_plan_id": new_plan_id},
		)

		if not result or not isinstance(result, dict):
			return PlanChangeError(
				code="INTERNAL_ERROR",
				message="Unexpected response from plan change API.",
			)

		if result.get("status") == "error":
			return PlanChangeError(
				code=result.get("code", "INTERNAL_ERROR"),
				message=result.get("message", "Plan change failed."),
				retry_after=result.get("retry_after"),
			)

		return PlanChangeResult(
			history_id=result["history_id"],
			previous_plan=result["previous_plan"],
			new_plan=new_plan_id,
			trigger_reason=result.get("trigger_reason", ""),
		)

	async def _post_cleanup(self, player_id: str) -> None:
		"""Post-Frappe cleanup: delete all Redis caches for the player.

		Non-fatal per FR-022 — cache self-healing handles any misses.
		"""
		try:
			# --- Direct DEL keys (10 keys) ---
			direct_keys = [
				session_key(player_id),
				game_session_key(player_id),
				wallet_key(player_id),
				access_key(player_id),
				daily_xp_key(player_id),
				player_plan_key(player_id),
				profile_key(player_id),
				reviews_overview_key(player_id),
				practice_session_key(player_id),
				pending_key(player_id),
			]
			if direct_keys:
				await self.redis.delete(*direct_keys)

			# --- SCAN + DEL pattern keys (6 patterns) ---
			scan_patterns = [
				player_progress_pattern(player_id),
				player_stats_pattern(player_id),
				player_items_learned_pattern(player_id),
				player_mastery_pattern(player_id),
				player_fsrs_pattern(player_id),
				player_fsrs_processed_pattern(player_id),
			]
			for pattern in scan_patterns:
				cursor = 0
				while True:
					cursor, keys = await self.redis.scan(cursor, match=pattern, count=200)
					if keys:
						await self.redis.delete(*keys)
					if cursor == 0:
						break

			# --- Leaderboard ZREM (SCAN memora:lb:* and ZREM player) ---
			lb_pattern = f"{LB_PREFIX}:*"
			cursor = 0
			pipe = self.redis.pipeline()
			pipe_count = 0
			while True:
				cursor, keys = await self.redis.scan(cursor, match=lb_pattern, count=200)
				for key in keys:
					pipe.zrem(key, player_id)
					pipe_count += 1
					if pipe_count >= 100:
						await pipe.execute()
						pipe = self.redis.pipeline()
						pipe_count = 0
				if cursor == 0:
					break
			if pipe_count > 0:
				await pipe.execute()

		except Exception as e:
			logger.warning("post_cleanup_error", error=str(e), player_id=player_id)

	async def _set_cooldown(self, player_id: str) -> None:
		"""Set cooldown timestamp after successful plan change."""
		try:
			await self.redis.set(
				plan_change_ts_key(player_id),
				str(time.time()),
				ex=PLAN_CHANGE_COOLDOWN_TTL,
			)
		except Exception as e:
			logger.warning("set_cooldown_error", error=str(e), player_id=player_id)

	async def _publish_invalidation(self, player_id: str) -> None:
		"""Publish cache invalidation event for the player."""
		import json

		try:
			msg = json.dumps(
				{
					"type": "plan_changed",
					"player_id": player_id,
					"reason": "plan_changed",
					"timestamp": time.time(),
				}
			)
			await self.redis.publish(cache_invalidation_channel(), msg)
		except Exception as e:
			logger.warning("publish_invalidation_error", error=str(e), player_id=player_id)

	async def get_available_plans(self, current_plan_id: str) -> list[dict]:
		"""Fetch available plans from Frappe API, excluding current plan.

		Returns list of plan dicts with name, plan_name, grade, grade_name,
		major, major_name, season, season_title.
		"""
		result = await self.frappe.call(
			"memora_admin.api.plan_change.get_available_plans",
			params={"current_plan_id": current_plan_id},
		)
		if not result or not isinstance(result, dict):
			return []
		return result.get("plans", [])

	async def _release_freeze(self, player_id: str) -> None:
		"""Release per-player freeze lock."""
		try:
			await self.redis.delete(freeze_key(player_id))
		except Exception as e:
			logger.warning("release_freeze_error", error=str(e), player_id=player_id)
