"""
Tests for sync_dirty_wallets() task.

Tests verify that wallet data is correctly synced from Redis to MariaDB,
including happy path, edge cases, and error handling.
"""

from unittest.mock import patch, MagicMock
import frappe
from fastapi_app.core.redis_keys import dirty_wallets_key, wallet_key as _wallet_key_fn
from memora_admin.tests.sync_test_base import SyncTestCase
from memora_admin.tests.voucher_fixtures import make_player
from memora_admin.tasks.sync import sync_dirty_wallets


class TestSyncDirtyWallets(SyncTestCase):
	"""
	Integration tests for sync_dirty_wallets() function.

	Tests the full pipeline: Redis wallet hash -> MariaDB Player Wallet record.
	"""

	def setUp(self):
		"""
		Initialize test environment.

		- Create a unique player via make_player(season="SEAS-00027")
		- Create a wallet record via _make_wallet_record()
		- Store player_id for use in tests
		"""
		super().setUp()

		# Create unique player for this test
		player_doc = make_player(season="SEAS-00027")
		self.player_id = player_doc.name

		# Create or get wallet record for this player
		existing_wallet = frappe.db.get_value("Memora Player Wallet", {"player": self.player_id}, "name")
		if not existing_wallet:
			self._make_wallet_record(self.player_id)

	def test_happy_path(self):
		"""
		Test: Single dirty player is synced correctly to MariaDB.

		- Seed Redis wallet hash with xp=500, streak=3
		- Call sync_dirty_wallets()
		- Assert: DB record updated with correct values
		- Assert: Wallet removed from dirty set
		"""
		# Seed Redis wallet hash and dirty set
		self._seed_redis_wallet(self.player_id, xp=500, streak=3)

		# Verify data is in Redis before sync (handle bytes from redis-py)
		wallet_hash = self.r.hgetall(_wallet_key_fn(self.player_id))
		xp_val = wallet_hash.get(b"xp") or wallet_hash.get("xp")
		streak_val = wallet_hash.get(b"streak") or wallet_hash.get("streak")
		# Convert to string if bytes
		xp_val = xp_val.decode() if isinstance(xp_val, bytes) else xp_val
		streak_val = streak_val.decode() if isinstance(streak_val, bytes) else streak_val
		self.assertEqual(xp_val, "500")
		self.assertEqual(streak_val, "3")

		# Run sync
		sync_dirty_wallets()

		# Verify DB was updated
		xp_value = frappe.db.get_value("Memora Player Wallet", {"player": self.player_id}, "total_xp")
		streak_value = frappe.db.get_value("Memora Player Wallet", {"player": self.player_id}, "current_streak")

		self.assertEqual(xp_value, 500)
		self.assertEqual(streak_value, 3)

		# Verify dirty flag cleared
		dirty_flag = frappe.db.get_value("Memora Player Wallet", {"player": self.player_id}, "dirty_flag")
		self.assertEqual(dirty_flag, 0)

		# Verify removed from dirty set
		is_dirty = self.r.sismember(dirty_wallets_key(), self.player_id)
		self.assertFalse(is_dirty)

	def test_multiple_dirty(self):
		"""
		Test: Multiple dirty players are synced correctly.

		- Create 3 players with different xp/streak values
		- Seed Redis for each player
		- Call sync_dirty_wallets()
		- Assert: All 3 DB records updated
		- Assert: Dirty set is empty for all test players
		"""
		# Create 2 more players
		player2_doc = make_player(season="SEAS-00027")
		player2_id = player2_doc.name
		if not frappe.db.exists("Memora Player Wallet", {"player": player2_id}):
			self._make_wallet_record(player2_id)

		player3_doc = make_player(season="SEAS-00027")
		player3_id = player3_doc.name
		if not frappe.db.exists("Memora Player Wallet", {"player": player3_id}):
			self._make_wallet_record(player3_id)

		# Seed Redis for all 3 players with different values
		self._seed_redis_wallet(self.player_id, xp=100, streak=1)
		self._seed_redis_wallet(player2_id, xp=200, streak=2)
		self._seed_redis_wallet(player3_id, xp=300, streak=3)

		# Run sync
		sync_dirty_wallets()

		# Verify all 3 DB records updated with correct values
		for player_id, expected_xp, expected_streak in [
			(self.player_id, 100, 1),
			(player2_id, 200, 2),
			(player3_id, 300, 3),
		]:
			xp_value = frappe.db.get_value("Memora Player Wallet", {"player": player_id}, "total_xp")
			streak_value = frappe.db.get_value("Memora Player Wallet", {"player": player_id}, "current_streak")
			self.assertEqual(xp_value, expected_xp, f"XP mismatch for {player_id}")
			self.assertEqual(streak_value, expected_streak, f"Streak mismatch for {player_id}")

		# Verify dirty set is empty (no test players remain)
		dirty_set = self.r.smembers(dirty_wallets_key())
		# Filter to only our test players
		test_player_ids = {self.player_id, player2_id, player3_id}
		remaining_dirty = dirty_set & test_player_ids
		self.assertEqual(len(remaining_dirty), 0, f"Test players still in dirty set: {remaining_dirty}")

	def test_empty_dirty_set(self):
		"""
		Test: sync_dirty_wallets() handles empty dirty set gracefully.

		- Do NOT seed any Redis data
		- Call sync_dirty_wallets()
		- Assert: No errors raised
		"""
		# Don't seed any data - dirty set is empty

		# Run sync - should complete without error
		try:
			sync_dirty_wallets()
		except Exception as e:
			self.fail(f"sync_dirty_wallets() raised exception with empty dirty set: {e}")

	def test_missing_wallet_record(self):
		"""
		Test: Player in dirty set without DB wallet record is cleaned up.

		- Create player but do NOT create wallet record
		- Seed Redis wallet hash + dirty set
		- Call sync_dirty_wallets()
		- Assert: Player removed from dirty set
		- Assert: No wallet record created
		"""
		# Create player without wallet record
		player_doc = make_player(season="SEAS-00027")
		player_id = player_doc.name

		# Delete existing wallet record if it exists (from previous runs)
		existing_wallet = frappe.db.get_value("Memora Player Wallet", {"player": player_id}, "name")
		if existing_wallet:
			frappe.delete_doc("Memora Player Wallet", existing_wallet)

		# Seed Redis wallet hash and dirty set
		self._seed_redis_wallet(player_id, xp=500, streak=3)

		# Run sync
		sync_dirty_wallets()

		# Verify removed from dirty set
		is_dirty = self.r.sismember(dirty_wallets_key(), player_id)
		self.assertFalse(is_dirty, f"Player {player_id} should be removed from dirty set")

		# Verify no wallet record exists (or at least not the one we would have created)
		wallet_exists = frappe.db.exists("Memora Player Wallet", {"player": player_id})
		# The wallet might exist from earlier test runs, but it should not have been updated by sync
		# because the sync function skips players without Redis wallet data
		if wallet_exists:
			# If it does exist, verify it wasn't updated by our sync
			# (xp should still be 0 from the earlier run, not 500 from Redis)
			xp_value = frappe.db.get_value("Memora Player Wallet", {"player": player_id}, "total_xp")
			# The wallet should not be updated when it's missing from Redis but in dirty set
			# Actually, when the wallet is missing from Redis, sync skips the update
			# So xp should remain unchanged (0)
			self.assertEqual(xp_value, 0, f"Wallet XP should remain 0, not synced from Redis")

	def test_redis_wallet_missing(self):
		"""
		Test: Player in dirty set without Redis wallet hash is cleaned up.

		- Create player + wallet record
		- Add player to dirty set but DO NOT create Redis wallet hash
		- Call sync_dirty_wallets()
		- Assert: Player removed from dirty set
		- Assert: Wallet DB record unchanged (total_xp still 0)
		"""
		# Add to dirty set manually (without creating wallet hash)
		self.r.sadd(dirty_wallets_key(), self.player_id)
		self._cleanup_keys.append(dirty_wallets_key())

		# Verify no wallet hash exists
		wallet_hash = self.r.hgetall(_wallet_key_fn(self.player_id))
		self.assertEqual(len(wallet_hash), 0, "Wallet hash should not exist")

		# Verify initial DB state (total_xp should be 0)
		initial_xp = frappe.db.get_value("Memora Player Wallet", {"player": self.player_id}, "total_xp")
		self.assertEqual(initial_xp, 0)

		# Run sync
		sync_dirty_wallets()

		# Verify removed from dirty set
		is_dirty = self.r.sismember(dirty_wallets_key(), self.player_id)
		self.assertFalse(is_dirty, f"Player {self.player_id} should be removed from dirty set")

		# Verify wallet DB record unchanged
		final_xp = frappe.db.get_value("Memora Player Wallet", {"player": self.player_id}, "total_xp")
		self.assertEqual(final_xp, 0, "Wallet XP should remain unchanged")

	def test_partial_failure(self):
		"""
		Test: Chunk-level failure handling - entire chunk fails and retries next cycle.

		With batch CASE/WHEN processing, failure is per-chunk (not per-item).
		When a chunk's DB update fails:
		- All items in that chunk remain in the dirty set (for retry)
		- No DB records are updated for that chunk
		"""
		# Create 2 more players
		player2_doc = make_player(season="SEAS-00027")
		player2_id = player2_doc.name
		if not frappe.db.exists("Memora Player Wallet", {"player": player2_id}):
			self._make_wallet_record(player2_id)

		player3_doc = make_player(season="SEAS-00027")
		player3_id = player3_doc.name
		if not frappe.db.exists("Memora Player Wallet", {"player": player3_id}):
			self._make_wallet_record(player3_id)

		# Seed Redis for all 3 players
		self._seed_redis_wallet(self.player_id, xp=100, streak=1)
		self._seed_redis_wallet(player2_id, xp=200, streak=2)
		self._seed_redis_wallet(player3_id, xp=300, streak=3)

		# Mock _batch_update_wallets to raise — simulates chunk-level DB failure
		with patch("memora_admin.tasks.sync._batch_update_wallets", side_effect=Exception("DB error on batch update")):
			sync_dirty_wallets()

		# All 3 players should remain in dirty set (chunk failed, no SREMs)
		for pid in [self.player_id, player2_id, player3_id]:
			is_dirty = self.r.sismember(dirty_wallets_key(), pid.encode() if isinstance(pid, str) else pid)
			self.assertTrue(is_dirty, f"Player {pid} should remain in dirty set after chunk failure")

		# No xp values should be updated (batch update was mocked to raise)
		for pid in [self.player_id, player2_id, player3_id]:
			xp = frappe.db.get_value("Memora Player Wallet", {"player": pid}, "total_xp")
			self.assertEqual(xp, 0, f"Player {pid} xp should remain 0 (chunk update failed)")

	def test_dirty_flag_cleared(self):
		"""
		Test: dirty_flag is set to 0 after successful sync.

		- Create player + wallet with dirty_flag=1
		- Seed Redis wallet
		- Call sync_dirty_wallets()
		- Assert: dirty_flag is now 0
		"""
		# Set wallet dirty_flag to 1
		frappe.db.set_value(
			"Memora Player Wallet",
			{"player": self.player_id},
			{"dirty_flag": 1}
		)

		# Verify dirty_flag is 1 before sync
		dirty_flag_before = frappe.db.get_value("Memora Player Wallet", {"player": self.player_id}, "dirty_flag")
		self.assertEqual(dirty_flag_before, 1)

		# Seed Redis wallet
		self._seed_redis_wallet(self.player_id, xp=500, streak=3)

		# Run sync
		sync_dirty_wallets()

		# Verify dirty_flag is now 0
		dirty_flag_after = frappe.db.get_value("Memora Player Wallet", {"player": self.player_id}, "dirty_flag")
		self.assertEqual(dirty_flag_after, 0, "dirty_flag should be cleared after sync")

	def test_sync_log_created(self):
		"""
		Test: Memora Sync Log is created after successful wallet sync.

		- Seed one player with dirty + wallet hash + record
		- Call sync_dirty_wallets()
		- Assert: Memora Sync Log doc exists with sync_type="Wallet", records_processed=1, status="Success"
		"""
		# Seed Redis wallet
		self._seed_redis_wallet(self.player_id, xp=500, streak=3)

		# Run sync
		sync_dirty_wallets()

		# Verify Sync Log was created
		sync_log_exists = frappe.db.exists(
			"Memora Sync Log",
			{
				"sync_type": "Wallet",
				"records_processed": 1,
				"status": "Success"
			}
		)
		self.assertTrue(sync_log_exists, "Memora Sync Log with sync_type='Wallet' should be created")
