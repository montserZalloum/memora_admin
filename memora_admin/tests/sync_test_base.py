"""
Base class for sync task tests with Redis helpers.

Provides:
- Redis connection setup/cleanup
- Helper methods to seed/verify Redis data
- Wrapper around FrappeTestCase for sync testing
"""

import json
import uuid
from typing import List

import frappe
import redis
from frappe.tests.utils import FrappeTestCase

from fastapi_app.core.redis_keys import (
	dirty_progress_key,
	dirty_wallets_key,
	interaction_buffer_key,
	progress_key as _progress_key_fn,
	wallet_key as _wallet_key_fn,
)


class SyncTestCase(FrappeTestCase):
	"""
	Base class for sync task integration tests.

	Manages Redis connection, test data seeding, and cleanup.
	All sync test files inherit from this class.
	"""

	@classmethod
	def setUpClass(cls):
		"""Set up class-level fixtures."""
		super().setUpClass()

	def setUp(self):
		"""
		Initialize test environment.

		- Connect to Redis (same instance as Frappe)
		- Generate unique test ID prefix
		- Initialize cleanup list
		"""
		super().setUp()

		# Connect to Redis via Frappe config (synchronous client)
		# Use decode_responses=False to handle binary bitmap data
		redis_url = frappe.conf.redis_cache or "redis://127.0.0.1:13000"
		self.r = redis.from_url(redis_url, decode_responses=False)

		# Generate unique test ID prefix for this test run
		self._test_id = uuid.uuid4().hex[:8]

		# List to track Redis keys that need cleanup
		self._cleanup_keys: List[str] = []

	def tearDown(self):
		"""
		Clean up test data.

		- Delete all tracked Redis keys
		- Rollback FrappeTestCase (auto-done by parent)
		"""
		# Delete all tracked Redis keys
		for key in self._cleanup_keys:
			try:
				self.r.delete(key)
			except redis.RedisError:
				pass  # Key may have been deleted already

		self._cleanup_keys = []
		super().tearDown()

	def _redis_cleanup(self, keys: List[str]) -> None:
		"""
		Delete specific Redis keys on demand.

		Args:
			keys: List of Redis key names to delete
		"""
		for key in keys:
			try:
				self.r.delete(key)
				if key in self._cleanup_keys:
					self._cleanup_keys.remove(key)
			except redis.RedisError:
				pass

	def _make_wallet_record(self, player_name: str) -> str:
		"""
		Create a Memora Player Wallet document.

		Args:
			player_name: Memora Player Profile name (e.g., "PLAY-00001")

		Returns:
			wallet_name: Auto-generated wallet document name
		"""
		wallet_doc = frappe.get_doc({
			"doctype": "Memora Player Wallet",
			"player": player_name,
			"total_xp": 0,
			"current_streak": 0,
			"dirty_flag": 0,
			"status": "Active",
		})
		wallet_doc.insert(ignore_permissions=True)
		return wallet_doc.name

	def _seed_redis_wallet(self, player_id: str, xp: int, streak: int) -> None:
		"""
		Seed Redis wallet hash and mark player as dirty.

		Args:
			player_id: Memora Player Profile name
			xp: XP value to set
			streak: Streak value to set

		Tracks Redis keys for cleanup.
		"""
		# Create wallet hash
		wkey = _wallet_key_fn(player_id)
		self.r.hset(wkey, mapping={
			"xp": str(xp),
			"streak": str(streak),
		})
		self._cleanup_keys.append(wkey)

		# Add to dirty set
		dirty_key = dirty_wallets_key()
		self.r.sadd(dirty_key, player_id)
		if dirty_key not in self._cleanup_keys:
			self._cleanup_keys.append(dirty_key)

	def _seed_redis_progress(
		self,
		user_id: str,
		subject_id: str,
		version: int,
		bit_positions: List[int],
	) -> None:
		"""
		Seed Redis progress bitmap and mark as dirty.

		Args:
			user_id: Player name
			subject_id: Subject name
			version: Version number
			bit_positions: List of bit positions to set (e.g., [0, 7])

		Tracks Redis keys for cleanup.
		"""
		# Create bitmap key
		bitmap_key = _progress_key_fn(user_id, subject_id, version)

		# Set specified bit positions
		for pos in bit_positions:
			self.r.setbit(bitmap_key, pos, 1)

		self._cleanup_keys.append(bitmap_key)

		# Add to dirty set
		dirty_member = f"{user_id}:{subject_id}:v{version}"
		dirty_key = dirty_progress_key()
		self.r.sadd(dirty_key, dirty_member)
		if dirty_key not in self._cleanup_keys:
			self._cleanup_keys.append(dirty_key)

	def _push_interaction(self, data: dict) -> None:
		"""
		Push interaction JSON to Redis buffer.

		Args:
			data: Interaction dict with player, lesson, stage_id, event_type, etc.

		Tracks Redis key for cleanup.
		"""
		buffer_key = interaction_buffer_key()
		self.r.rpush(buffer_key, json.dumps(data))
		if buffer_key not in self._cleanup_keys:
			self._cleanup_keys.append(buffer_key)
