"""Player access set management via Redis for O(1) access checks."""

from __future__ import annotations

from typing import TYPE_CHECKING

import redis.asyncio as redis
import structlog

if TYPE_CHECKING:
	from fastapi_app.services.frappe_client import FrappeClient

logger = structlog.get_logger()


class AccessService:
	"""Manages player access grants via Redis sets.

	Key pattern: memora:access:{player_id} -> set of content keys
	Content keys follow pattern: SUB-{subject} or TRK-{track}

	Per CONTEXT.md:
	- Grants are additive (direct OR plan membership)
	- Grants are permanent until explicitly revoked
	- Grant granularity: Subject-level or Track-level

	Hydration: After a Redis flush, access grants are lost. The ensure_hydrated()
	method restores them from MariaDB via the Frappe API, following the same
	pattern as WalletService.ensure_hydrated().
	"""

	def __init__(
		self,
		redis_client: redis.Redis,
		key_prefix: str = "memora:",
		frappe_client: FrappeClient | None = None,
	):
		self.redis = redis_client
		self.prefix = key_prefix
		self.frappe = frappe_client

	def _access_key(self, player_id: str) -> str:
		"""Generate Redis key for player's access set."""
		return f"{self.prefix}access:{player_id}"

	async def ensure_hydrated(self, player_id: str) -> None:
		"""Ensure access set exists in Redis, hydrating from MariaDB if missing.

		After a Redis flush (bench clear-cache, restart, etc.), access grants in Redis
		are lost. Without hydration, access checks return False for all content,
		effectively locking out all subscribed users.

		This method checks if the access set exists and, if not, loads the active
		subscriptions from MariaDB via the Frappe API and seeds Redis.

		Follows the same pattern as WalletService.ensure_hydrated().

		Args:
		    player_id: Player's user ID
		"""
		key = self._access_key(player_id)

		# Fast path: access set already exists in Redis
		exists = await self.redis.exists(key)
		if exists:
			return

		# Access set missing from Redis -- hydrate from MariaDB
		if not self.frappe:
			logger.warning(
				"access_hydration_skipped",
				player_id=player_id,
				reason="no_frappe_client",
			)
			return

		try:
			result = await self.frappe.call(
				"memora_admin.api.subscriptions.get_player_access_keys",
				{"player_id": player_id},
			)

			if result and isinstance(result, list) and len(result) > 0:
				# Seed Redis with MariaDB access keys using SADD
				await self.redis.sadd(key, *result)

				logger.info(
					"access_hydrated_from_mariadb",
					player_id=player_id,
					keys=result,
					count=len(result),
				)
			else:
				logger.debug(
					"access_hydration_empty",
					player_id=player_id,
				)

		except Exception as e:
			# Don't fail the access check if hydration fails -- log and continue.
			# The check will return False (no access), which is the pre-existing
			# behavior when Redis is empty. At least we tried.
			logger.error(
				"access_hydration_failed",
				player_id=player_id,
				error=str(e),
			)

	async def check_access(self, player_id: str, content_key: str) -> bool:
		"""
		Check if player has access to content.
		O(1) complexity via SISMEMBER (after hydration check).

		Ensures access set is hydrated from MariaDB before checking,
		preventing false negatives after Redis cache flush.

		Args:
		    player_id: Player's user ID
		    content_key: Access key (e.g., "SUB-MATH", "TRK-MATH-01")

		Returns:
		    True if player has grant for this content
		"""
		# Ensure access set exists in Redis before checking.
		# Without this, SISMEMBER on a missing key returns 0, denying access.
		await self.ensure_hydrated(player_id)

		key = self._access_key(player_id)
		result = await self.redis.sismember(key, content_key)
		# Handle both int (0/1) and bool responses
		return bool(result)

	async def grant_access(self, player_id: str, content_keys: list[str]) -> int:
		"""
		Grant access to content.
		Idempotent - re-granting same key is safe (SADD ignores duplicates).

		Returns:
		    Number of NEW grants added (0 if all existed)
		"""
		if not content_keys:
			return 0
		key = self._access_key(player_id)
		return await self.redis.sadd(key, *content_keys)

	async def revoke_access(self, player_id: str, content_keys: list[str]) -> int:
		"""
		Revoke access to content.

		Returns:
		    Number of grants removed
		"""
		if not content_keys:
			return 0
		key = self._access_key(player_id)
		return await self.redis.srem(key, *content_keys)

	async def get_player_grants(self, player_id: str) -> set[str]:
		"""
		Get all content keys player has access to.
		O(N) - use sparingly, prefer check_access for single checks.

		Ensures access set is hydrated from MariaDB before reading,
		preventing empty results after Redis cache flush.
		"""
		# Ensure access set exists in Redis before reading.
		await self.ensure_hydrated(player_id)

		key = self._access_key(player_id)
		members = await self.redis.smembers(key)
		# Handle bytes or str responses
		return {m.decode() if isinstance(m, bytes) else m for m in members}

	# =========================================================================
	# Plan-Aware Access Methods (Level 1: Plan membership grants)
	# =========================================================================

	def _plan_free_subjects_key(self, plan_id: str) -> str:
		"""Generate Redis key for plan's free subjects set."""
		return f"{self.prefix}plan:{plan_id}:free_subjects"

	async def is_subject_free_in_plan(self, plan_id: str, subject_id: str) -> bool:
		"""Check if subject is marked non-premium in player's plan.

		Per CONTEXT.md: is_premium=0 on Memora Plan Subject means the subject
		is included in the plan without requiring an explicit grant.

		O(1) complexity via SISMEMBER.

		Args:
		    plan_id: The plan identifier
		    subject_id: The subject identifier

		Returns:
		    True if subject is free in the plan (is_premium=0)
		"""
		if not plan_id:
			return False
		key = self._plan_free_subjects_key(plan_id)
		result = await self.redis.sismember(key, subject_id)
		return bool(result)

	async def get_plan_free_subjects(self, plan_id: str | None) -> list[str]:
		"""Get subjects marked as non-premium in player's plan (from Redis cache).

		Args:
		    plan_id: The plan identifier, or None if player has no plan

		Returns:
		    List of subject IDs that are free in the plan
		"""
		if not plan_id:
			return []
		key = self._plan_free_subjects_key(plan_id)
		members = await self.redis.smembers(key)
		return [m.decode() if isinstance(m, bytes) else m for m in members]

	async def check_access_with_plan(self, player_id: str, content_key: str, plan_id: str | None) -> bool:
		"""Check access via explicit grant OR plan membership.

		Per CONTEXT.md: Grants are additive (direct OR plan membership).

		Returns True if:
		1. Player has explicit grant (SUB-* in Redis set), OR
		2. Subject is in player's plan with is_premium=0

		Args:
		    player_id: Player's user ID
		    content_key: Access key (e.g., "SUB-MATH")
		    plan_id: Player's plan ID (from JWT), or None

		Returns:
		    True if player has access through either method
		"""
		# Check explicit grant first (fast path)
		# Note: check_access() already calls ensure_hydrated()
		if await self.check_access(player_id, content_key):
			return True

		# Check plan membership (if plan provided and content is subject-level)
		if plan_id and content_key.startswith("SUB-"):
			subject_id = content_key.replace("SUB-", "")
			if await self.is_subject_free_in_plan(plan_id, subject_id):
				return True

		return False
