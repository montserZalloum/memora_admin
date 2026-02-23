"""Tests for WalletService - XP and streak management via Redis."""

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from fastapi_app.core.constants import DIRTY_WALLETS_KEY
from fastapi_app.core.redis_keys import wallet_key
from fastapi_app.services.wallet import WalletService, get_amman_today, get_amman_yesterday

# Test constants
TEST_PLAYER = "PLAYER-TEST-001"
AMMAN_TZ = ZoneInfo("Asia/Amman")


@pytest.fixture
def wallet_service(redis_client, test_prefix, mock_frappe):
	"""WalletService with all dependencies."""
	return WalletService(redis_client, frappe_client=mock_frappe)


@pytest.fixture
def wallet_service_no_frappe(redis_client, test_prefix):
	"""WalletService without FrappeClient (for hydration skip tests)."""
	return WalletService(redis_client, frappe_client=None)


@pytest.fixture(autouse=True)
async def cleanup_dirty_wallets(redis_client):
	"""Auto-cleanup dirty wallet keys after each test."""
	yield
	await redis_client.srem(DIRTY_WALLETS_KEY, TEST_PLAYER)


class TestXPOperations:
	"""Tests for award_xp and get_wallet methods."""

	async def test_award_xp_increment(self, redis_client, test_prefix, wallet_service):
		"""Award XP twice - accumulates atomically."""
		result1 = await wallet_service.award_xp(TEST_PLAYER, 100)
		assert result1 == 100

		result2 = await wallet_service.award_xp(TEST_PLAYER, 50)
		assert result2 == 150

		# Verify HGET
		key = wallet_key(TEST_PLAYER)
		xp_value = await redis_client.hget(key, "xp")
		assert xp_value == "150"

	async def test_award_xp_marks_dirty(self, redis_client, test_prefix, wallet_service):
		"""Award XP marks dirty for background sync."""
		await wallet_service.award_xp(TEST_PLAYER, 100)

		# Verify dirty set membership
		is_member = await redis_client.sismember(DIRTY_WALLETS_KEY, TEST_PLAYER)
		assert bool(is_member) is True

	async def test_get_wallet_defaults(self, redis_client, test_prefix, wallet_service):
		"""get_wallet returns defaults for new players."""
		result = await wallet_service.get_wallet(TEST_PLAYER)
		assert result == {"xp": 0, "streak": 0}


class TestWalletHydration:
	"""Tests for wallet hydration from Frappe."""

	async def test_get_wallet_hydrates(self, redis_client, test_prefix, mock_frappe, wallet_service):
		"""get_wallet hydrates from Frappe on cache miss."""
		# Setup mock
		mock_frappe.call.return_value = {"total_xp": 1500, "current_streak": 7}

		# Call get_wallet
		result = await wallet_service.get_wallet(TEST_PLAYER)
		assert result == {"xp": 1500, "streak": 7}

		# Verify frappe was called
		mock_frappe.call.assert_called_once_with(
			"memora_admin.api.wallet.get_player_wallet",
			{"player_id": TEST_PLAYER},
		)

		# Verify Redis was seeded
		key = wallet_key(TEST_PLAYER)
		data = await redis_client.hgetall(key)
		assert data["xp"] == "1500"
		assert data["streak"] == "7"

	async def test_hydration_seeds_redis(self, redis_client, test_prefix, mock_frappe, wallet_service):
		"""ensure_hydrated seeds Redis from Frappe."""
		# Setup mock
		mock_frappe.call.return_value = {"total_xp": 500, "current_streak": 3}

		# Hydrate
		await wallet_service.ensure_hydrated(TEST_PLAYER)

		# Verify Redis was seeded
		key = wallet_key(TEST_PLAYER)
		xp_value = await redis_client.hget(key, "xp")
		streak_value = await redis_client.hget(key, "streak")
		assert xp_value == "500"
		assert streak_value == "3"

	async def test_hydration_skips_existing(self, redis_client, test_prefix, mock_frappe, wallet_service):
		"""Hydration skips if wallet already exists in Redis."""
		# Pre-seed wallet
		key = wallet_key(TEST_PLAYER)
		await redis_client.hset(key, mapping={"xp": "100", "streak": "2"})

		# Call hydration
		await wallet_service.ensure_hydrated(TEST_PLAYER)

		# Verify frappe was NOT called
		mock_frappe.call.assert_not_called()

		# Verify xp unchanged
		xp_value = await redis_client.hget(key, "xp")
		assert xp_value == "100"


class TestStreakLua:
	"""Tests for update_streak Lua script with 5 branches."""

	async def test_streak_first_completion(self, redis_client, test_prefix, wallet_service):
		"""First completion - streak becomes 1."""
		streak, was_updated = await wallet_service.update_streak(TEST_PLAYER, is_replay=False)
		assert streak == 1
		assert was_updated is True

		# Verify streak_date was set to today
		key = wallet_key(TEST_PLAYER)
		today = get_amman_today()
		date_value = await redis_client.hget(key, "streak_date")
		assert date_value == today

	async def test_streak_consecutive(self, redis_client, test_prefix, wallet_service):
		"""Consecutive day - streak increments."""
		key = wallet_key(TEST_PLAYER)
		yesterday = get_amman_yesterday()

		# Pre-seed: streak=5 from yesterday
		await redis_client.hset(key, mapping={"streak": "5", "streak_date": yesterday})

		# Update streak
		streak, was_updated = await wallet_service.update_streak(TEST_PLAYER, is_replay=False)
		assert streak == 6
		assert was_updated is True

		# Verify date was updated to today
		today = get_amman_today()
		date_value = await redis_client.hget(key, "streak_date")
		assert date_value == today

	async def test_streak_missed_day(self, redis_client, test_prefix, wallet_service):
		"""Missed day (2+ days gap) - streak resets to 1."""
		key = wallet_key(TEST_PLAYER)
		two_days_ago = (datetime.now(AMMAN_TZ) - timedelta(days=2)).strftime("%Y-%m-%d")

		# Pre-seed: streak=5 from 2 days ago
		await redis_client.hset(key, mapping={"streak": "5", "streak_date": two_days_ago})

		# Update streak
		streak, was_updated = await wallet_service.update_streak(TEST_PLAYER, is_replay=False)
		assert streak == 1
		assert was_updated is True

	async def test_streak_same_day(self, redis_client, test_prefix, wallet_service):
		"""Same day completion - no streak change."""
		key = wallet_key(TEST_PLAYER)
		today = get_amman_today()

		# Pre-seed: streak=3 from today
		await redis_client.hset(key, mapping={"streak": "3", "streak_date": today})

		# Update streak
		streak, was_updated = await wallet_service.update_streak(TEST_PLAYER, is_replay=False)
		assert streak == 3
		assert was_updated is False

	async def test_streak_replay_no_change(self, redis_client, test_prefix, wallet_service):
		"""Replay - streak unchanged and not marked dirty."""
		key = wallet_key(TEST_PLAYER)
		yesterday = get_amman_yesterday()

		# Pre-seed: streak=5 from yesterday
		await redis_client.hset(key, mapping={"streak": "5", "streak_date": yesterday})

		# Update streak with replay
		streak, was_updated = await wallet_service.update_streak(TEST_PLAYER, is_replay=True)
		assert streak == 5
		assert was_updated is False

	async def test_streak_marks_dirty(self, redis_client, test_prefix, wallet_service):
		"""Dirty set updated only when streak changed."""
		# First: completion (streak=1, dirty marked)
		await wallet_service.update_streak(TEST_PLAYER, is_replay=False)
		is_member = await redis_client.sismember(DIRTY_WALLETS_KEY, TEST_PLAYER)
		assert bool(is_member) is True

		# Clear dirty
		await redis_client.srem(DIRTY_WALLETS_KEY, TEST_PLAYER)

		# Replay (same day later): no dirty marking
		today = get_amman_today()
		key = wallet_key(TEST_PLAYER)
		await redis_client.hset(key, mapping={"streak": "1", "streak_date": today})

		await wallet_service.update_streak(TEST_PLAYER, is_replay=True)
		is_member = await redis_client.sismember(DIRTY_WALLETS_KEY, TEST_PLAYER)
		assert bool(is_member) is False
