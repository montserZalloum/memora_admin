"""Review service for FSRS spaced repetition review operations (item-level)."""

import json

import redis.asyncio as redis
import structlog

from fastapi_app.core.redis_keys import reviews_overview_key
from fastapi_app.services.frappe_client import FrappeClient

logger = structlog.get_logger()

REVIEW_OVERVIEW_TTL = 300  # 5 minutes


class ReviewService:
	"""Service for review operations with Redis caching and Frappe API.

	Caches the review overview (due counts per subject) in Redis with 5-min TTL.
	Due items and submit are always fresh (no cache).
	All data operations delegate to Frappe whitelisted methods.
	"""

	def __init__(self, redis_client: redis.Redis, frappe_client: FrappeClient):
		self.redis = redis_client
		self.frappe = frappe_client

	async def get_overview(self, player_id: str) -> list[dict]:
		"""Get review overview with Redis caching (5-min TTL).

		Returns list of dicts with subject and due_count keys.
		"""
		key = reviews_overview_key(player_id)

		cached = await self.redis.get(key)
		if cached:
			data = cached.decode() if isinstance(cached, bytes) else cached
			logger.debug("review_overview_cache_hit", player=player_id)
			return json.loads(data)

		logger.info("review_overview_cache_miss", player=player_id)
		result = await self.frappe.call(
			"memora_admin.api.reviews.get_review_overview",
			{"player_id": player_id},
		)

		subjects = result if isinstance(result, list) else []

		await self.redis.set(key, json.dumps(subjects), ex=REVIEW_OVERVIEW_TTL)
		return subjects

	async def get_due_items(self, player_id: str, subject_id: str) -> dict:
		"""Get due items for a subject (no cache -- always fresh).

		Returns dict with items list and has_more boolean.
		"""
		result = await self.frappe.call(
			"memora_admin.api.reviews.get_due_items",
			{"player_id": player_id, "subject_id": subject_id},
		)

		if isinstance(result, dict):
			return result
		return {"items": [], "has_more": False}

	async def submit_reviews(self, player_id: str, subject_id: str, items: list[dict]) -> dict:
		"""Submit review results via Frappe API.

		Items must be JSON-serialized before passing to Frappe.
		Cache invalidation happens AFTER the Frappe call completes.
		"""
		result = await self.frappe.call(
			"memora_admin.api.reviews.submit_reviews",
			{
				"player_id": player_id,
				"subject_id": subject_id,
				"items": json.dumps(items),
			},
		)

		# Invalidate cached overview after submit
		await self.invalidate_overview(player_id)

		if isinstance(result, dict):
			return result
		return {"processed": 0, "remaining_due": 0, "has_more": False}

	async def invalidate_overview(self, player_id: str):
		"""Invalidate cached review overview after submit."""
		key = reviews_overview_key(player_id)
		await self.redis.delete(key)
		logger.info("review_overview_cache_invalidated", player=player_id)
